"""
Herramienta 1: Funding Rates — el screener delta-neutral, con todo lo
construido en los Pasos 1-6, ahora en una interfaz web en vez de terminal.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.aggregate import build_matrix, exchange_columns
from core.data_service import fetch_normalized_rates, filter_rates
from core.history import WINDOWS_HOURS, historical_apr_all_windows, init_db
from core.normalize import NormalizedRate
from core.opportunities import compute_opportunities

# Paleta compartida con el resto del toolkit (mismo verde/ámbar/rojo que el
# informe de análisis inicial), para que la matriz se sienta parte de la
# misma herramienta.
_GREEN = (94, 230, 196)   # mejor para ir LONG (tasa más baja)
_AMBER = (240, 180, 41)   # neutral
_RED = (240, 87, 107)     # mejor para ir SHORT (tasa más alta)


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _apr_cell_color(value: float, vmin: float = -50, vmax: float = 50) -> str:
    t = max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
    rgb = _lerp_color(_GREEN, _AMBER, t / 0.5) if t < 0.5 else _lerp_color(_AMBER, _RED, (t - 0.5) / 0.5)
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def render_matrix_html(matrix: dict[str, dict[str, NormalizedRate]], columns: list[str]) -> str:
    header = "".join(f"<th style='padding:8px 14px;text-align:right;font-weight:600;'>{ex}</th>" for ex in columns)
    rows_html = []
    for symbol in sorted(matrix):
        row = matrix[symbol]
        values = {ex: r.apr_pct for ex, r in row.items()}
        best_long = min(values, key=values.get) if len(values) >= 2 else None
        best_short = max(values, key=values.get) if len(values) >= 2 else None

        cells = [f"<td style='padding:8px 14px;font-weight:600;'>{symbol}</td>"]
        for ex in columns:
            if ex not in row:
                cells.append(
                    "<td style='padding:8px 14px;text-align:right;color:#5b6472;'>—</td>"
                )
                continue
            apr = row[ex].apr_pct
            bg = _apr_cell_color(apr)
            tag = ""
            if ex == best_long:
                tag = " · LONG"
            elif ex == best_short:
                tag = " · SHORT"
            cells.append(
                f"<td style='padding:8px 14px;text-align:right;background:{bg};color:#0b0e14;"
                f"font-weight:700;border-radius:4px;'>{apr:+.1f}%{tag}</td>"
            )
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    return f"""
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:separate;border-spacing:0 4px;font-size:14px;">
      <thead><tr><th style='padding:8px 14px;text-align:left;'>Símbolo</th>{header}</tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    </div>
    """


st.set_page_config(page_title="Funding Rates — Funding Toolkit", page_icon="📊", layout="wide")

st.title("📊 Funding Rates")
st.caption("Arbitraje delta-neutral: ranking, matriz completa, histórico real y consistency score.")

# ---------- Sidebar: fuente de datos y filtros ----------
with st.sidebar:
    st.header("Fuente de datos")
    source = st.radio(
        "Origen",
        ["Demo (offline)", "En vivo"],
        index=0,
        help="Este entorno de desarrollo no tiene salida a internet hacia los exchanges — "
             "usa Demo aquí. En vivo funcionará cuando esto corra en un servidor con internet normal.",
    )
    offline = source.startswith("Demo")

    st.header("Filtros")
    venue = st.selectbox("Tipo de venue", ["all", "cex", "dex"], format_func=lambda v: {"all": "Todos", "cex": "Solo CEX", "dex": "Solo DEX"}[v])

    refresh = st.button("🔄 Refrescar datos", use_container_width=True)

# ---------- Carga de datos (con cache) ----------
@st.cache_data(ttl=60, show_spinner="Consultando exchanges...")
def load_data(offline: bool):
    return fetch_normalized_rates(offline)

if refresh:
    load_data.clear()

try:
    all_rates, counts = load_data(offline)
except Exception as e:
    st.error(f"No se pudo obtener datos: {e}")
    st.stop()

if not all_rates:
    st.warning(
        "No se obtuvo ningún dato de los exchanges. Si estás en modo **En vivo**, comprueba que este "
        "entorno tiene salida a internet real hacia los exchanges (el sandbox de desarrollo de Claude no la tiene)."
    )
    st.stop()

exchanges_available = sorted({r.exchange for r in all_rates})
with st.sidebar:
    exchanges_selected = st.multiselect("Exchanges", exchanges_available, default=exchanges_available)

rates = filter_rates(all_rates, venue, exchanges_selected)

if not rates:
    st.warning("El filtro no dejó ningún resultado. Prueba a soltar algún exchange del filtro lateral.")
    st.stop()

# ---------- KPIs ----------
symbols = {r.symbol for r in rates}
best = max(rates, key=lambda r: abs(r.apr_pct))
now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Pares activos", f"{len(rates)}")
k2.metric("Símbolos únicos", f"{len(symbols)}")
k3.metric("Exchanges", f"{len(exchanges_selected)}")
k4.metric("Última actualización", now_str)

st.divider()

# ---------- Tabs: Ranking / Matriz / Histórico ----------
tab_ranking, tab_matrix, tab_history = st.tabs(["🏆 Ranking", "🔲 Matriz", "📈 Histórico"])

with tab_ranking:
    st.caption(
        "Mejor par long/short por símbolo, con Consistency Score (30d) y OI Depth. "
        "Esto es lo que verías primero al abrir la herramienta."
    )
    history_conn = init_db()
    opportunities = compute_opportunities(rates, history_conn)
    history_conn.close()

    if not opportunities:
        st.info("Ningún símbolo está presente en 2+ exchanges con los filtros actuales — no hay spread que calcular.")
    else:
        df = pd.DataFrame(
            [
                {
                    "Símbolo": o.symbol,
                    "Long en": f"{o.long_exchange} ({o.long_apr:+.1f}%)",
                    "Short en": f"{o.short_exchange} ({o.short_apr:+.1f}%)",
                    "Spread APR": o.spread_apr,
                    "Consistency (30d)": o.consistency_pct,
                    "OI long ($)": o.oi_long_usd,
                    "OI short ($)": o.oi_short_usd,
                    "Cuello de botella ($)": o.oi_bottleneck_usd,
                }
                for o in opportunities
            ]
        )

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Spread APR": st.column_config.NumberColumn(format="%.1f%%"),
                "Consistency (30d)": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=100
                ),
                "OI long ($)": st.column_config.NumberColumn(format="$%,.0f"),
                "OI short ($)": st.column_config.NumberColumn(format="$%,.0f"),
                "Cuello de botella ($)": st.column_config.NumberColumn(format="$%,.0f"),
            },
        )
        st.caption(
            "Consistency: % del tiempo (30d) que esta asignación long/short habría sido rentable. "
            "En blanco = sin histórico suficiente todavía."
        )

with tab_matrix:
    st.caption("Cada símbolo contra cada exchange, sin pre-filtrar — el dato crudo, estilo Loris.")
    matrix = build_matrix(rates)
    columns = exchange_columns(rates)

    # Render manual en HTML: st.dataframe + pandas Styler enseña "None" en las
    # celdas vacías en vez de un guion, y no hay forma limpia de evitarlo con
    # column_config — así que aquí controlamos el pixel exacto nosotros.
    st.markdown(render_matrix_html(matrix, columns), unsafe_allow_html=True)
    st.caption("Verde = mejor sitio para ir long (te pagan más). Rojo = mejor sitio para ir short.")

with tab_history:
    st.caption("APR histórico real, calculado a partir de snapshots guardados (no la tasa instantánea).")
    symbol_pick = st.selectbox("Símbolo", sorted(symbols))
    exchanges_for_symbol = sorted({r.exchange for r in rates if r.symbol == symbol_pick})
    exchange_pick = st.selectbox("Exchange", exchanges_for_symbol)

    conn = init_db()
    windows = historical_apr_all_windows(conn, exchange_pick, symbol_pick)

    cols = st.columns(len(WINDOWS_HOURS))
    for col, (label, stat) in zip(cols, windows.items()):
        with col:
            if stat.enough_history and stat.apr_avg is not None:
                col.metric(label.upper(), f"{stat.apr_avg:+.1f}%")
            else:
                col.metric(label.upper(), "s/d", help=f"Solo {stat.samples} snapshots — no hay histórico suficiente")

    history_df = pd.read_sql_query(
        "SELECT captured_at, apr_pct FROM funding_snapshots "
        "WHERE exchange = ? AND symbol = ? ORDER BY captured_at",
        conn,
        params=(exchange_pick, symbol_pick),
        parse_dates=["captured_at"],
    )
    conn.close()

    if history_df.empty:
        st.info(
            "Todavía no hay snapshots guardados para este par. En modo Demo, corre "
            "`python tools/seed_demo_history.py` para generar 30 días de histórico sintético."
        )
    else:
        st.line_chart(history_df.set_index("captured_at")["apr_pct"], height=320)

st.divider()
with st.expander("Diagnóstico: pares traídos por exchange"):
    st.json(counts)
