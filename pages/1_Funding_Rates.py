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
from core.opportunities import (
    apply_oi_map,
    collect_oi_targets,
    compute_opportunities,
    fetch_oi_for_targets,
    has_dead_liquidity,
    has_implausible_price_pair,
)

# Cuántas oportunidades (de arriba del ranking) se enriquecen con OI Depth
# real. A propósito no son todas: pedir OI símbolo a símbolo para miles de
# pares sería lento y quemaría el rate limit para nada — solo importa la
# profundidad de las pocas que ya decidiste mirar.
OI_ENRICH_TOP_N = 10

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


def _fmt_usd(value: float | None) -> str:
    """
    Formatea un importe en USD para texto libre (los paneles de diagnóstico
    más abajo, que son st.json/f-strings, no una tabla ordenable) — con
    guion para None.

    OJO: NO usar esto para columnas de un st.dataframe que el usuario pueda
    querer ordenar (ver bug real encontrado en producción, comentado junto a
    la tabla de Ranking más abajo): pre-formatear un número como texto hace
    que Streamlit lo ordene alfabéticamente en vez de numéricamente cuando se
    pulsa la cabecera de columna ("$9.4M" antes que "$898,647", por ejemplo).
    Para esos casos, pasar el valor crudo (float | None) y usar
    column_config.NumberColumn(format=...) — comprobado con pandas/pyarrow
    que un None en una columna float se convierte a NaN, no al string
    "None", así que no hace falta este pre-formateo para evitarlo.
    """
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    return f"${value:,.0f}"


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

    refresh = st.button("🔄 Refrescar datos", width="stretch")

# ---------- Carga de datos (con cache) ----------
@st.cache_data(ttl=60, show_spinner="Consultando exchanges...")
def load_data(offline: bool):
    return fetch_normalized_rates(offline)


@st.cache_data(ttl=60, show_spinner="Consultando profundidad (OI) de las mejores oportunidades...")
def load_oi_map(targets: tuple[tuple[str, str, float | None], ...]):
    # `targets` es una tupla (hashable) a propósito, con el mark_price incluido
    # (exchange, raw_symbol, mark_price) — así st.cache_data puede cachear esto
    # sin que le pasemos objetos de conector de ccxt, que no son cacheables.
    # Ver core/opportunities.py: collect_oi_targets/fetch_oi_for_targets.
    # Devuelve (oi_map, errores) — los errores se guardan para poder verlos en
    # el panel de diagnóstico, igual que con los errores de funding rates.
    if not targets:
        return {}, {}
    return fetch_oi_for_targets(targets)

if refresh:
    load_data.clear()
    load_oi_map.clear()

try:
    all_rates, counts, errors = load_data(offline)
except Exception as e:
    st.error(f"No se pudo obtener datos: {e}")
    st.stop()

# Un exchange caído no debe ocultarse en un "0 pares" silencioso — si Binance
# (por ejemplo) bloquea la IP del servidor, esto lo dice explícitamente en
# vez de dejarte adivinar por qué la tabla sale más corta de lo esperado.
if errors:
    lines = "\n".join(f"- **{ex}**: {msg}" for ex, msg in errors.items())
    st.warning(f"Algunos exchanges no respondieron:\n\n{lines}", icon="⚠️")

if not all_rates:
    st.error(
        "No se obtuvo ningún dato de ningún exchange. Revisa los errores de arriba — "
        "lo más probable es que el exchange esté bloqueando la IP de este servidor, "
        "no que no tengas internet."
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
oi_errors: dict[tuple[str, str], str] = {}  # se rellena en la pestaña Ranking, se enseña en Diagnóstico
dead_liquidity: list = []  # idem — oportunidades descartadas por OI $0 confirmado (ver has_dead_liquidity)
implausible_pairs: list = []  # idem — descartadas por Price Spread implausible (ver has_implausible_price_pair)
same_exchange_pairs: list = []  # idem — long y short en el MISMO exchange (investigando, ver README 2026-09-18)
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
        # Investigando (2026-09-18): el usuario detectó, en un export CSV del
        # Ranking, 121 filas donde long_exchange == short_exchange con Spread
        # APR exactamente 0.0% (ej. SSV en okx vs okx, CIFR en gate vs gate,
        # MUSTOCK en mexc vs mexc). compute_opportunities() nunca comprueba
        # que las dos piernas de una oportunidad vengan de exchanges
        # distintos — solo agrupa por símbolo normalizado y coge el
        # mínimo/máximo APR del grupo. Si un símbolo está listado en solo UN
        # exchange, `len(group) < 2` lo descarta entero (no puede producir
        # esto) — así que para que pase hace falta lo contrario: que ESE
        # exchange, él solo, aporte 2+ filas para el mismo símbolo
        # normalizado (candidatos de código, sin confirmar aún en vivo por
        # bloqueo de red del sandbox de desarrollo: un perpetuo + un futuro
        # con vencimiento pasando el mismo filtro de quote en cex_ccxt.py, o
        # dos variantes de quote/contrato colapsando al mismo símbolo en
        # MEXC/KuCoin — ver sus conectores). Solo diagnóstico por ahora, NO
        # se descarta nada todavía: hace falta ver el raw_symbol real de
        # ambas piernas en producción para confirmar el mecanismo exacto
        # antes de decidir el fix.
        same_exchange_pairs = [o for o in opportunities if o.long_exchange == o.short_exchange]

        # Ver core/opportunities.py::has_implausible_price_pair — bug real
        # encontrado en producción (2026-09-16, confirmado con raw_symbol/
        # mark_price reales: "CAT" = Caterpillar Inc. en bitget a $785.85
        # frente a un memecoin sin relación también llamado "CAT" en mexc a
        # $0.000001946). Se descarta ANTES de pedir OI Depth (top N) para no
        # gastar esas llamadas en oportunidades que ya son basura de raíz —
        # el "Símbolo" coincide mismo por casualidad, no son el mismo activo.
        implausible_pairs = [o for o in opportunities if has_implausible_price_pair(o)]
        opportunities = [o for o in opportunities if not has_implausible_price_pair(o)]

        if implausible_pairs:
            symbols_implausible = ", ".join(sorted({o.symbol for o in implausible_pairs}))
            st.caption(
                f"⚠️ {len(implausible_pairs)} oportunidad(es) descartada(s) del ranking por Price "
                f"Spread demasiado alto para ser el mismo activo ({symbols_implausible}) — probable "
                "choque de símbolos entre dos activos sin relación que comparten el mismo ticker "
                "corto normalizado. Detalle en el diagnóstico de abajo."
            )

        # OI Depth real para las mejores oportunidades: los CEX no lo traen
        # en el fetch masivo de funding rates (ccxt no expone un endpoint
        # bulk para eso), así que se pide aparte, solo para el top N y con
        # su propia caché — no en cada re-render.
        oi_targets = collect_oi_targets(opportunities, top_n=OI_ENRICH_TOP_N)
        oi_map, oi_errors = load_oi_map(oi_targets)
        apply_oi_map(opportunities, oi_map, top_n=OI_ENRICH_TOP_N)

        # Ver core/opportunities.py::has_dead_liquidity — descubierto en
        # producción con Aster/STORJ: un mercado con Open Interest $0
        # confirmado no es una oportunidad ejecutable, aunque el spread de
        # APR salga enorme. Se saca del ranking en vez de dejarlo arriba.
        dead_liquidity = [o for o in opportunities if has_dead_liquidity(o)]
        opportunities = [o for o in opportunities if not has_dead_liquidity(o)]

        if dead_liquidity:
            symbols_dead = ", ".join(sorted({o.symbol for o in dead_liquidity}))
            st.caption(
                f"⚠️ {len(dead_liquidity)} oportunidad(es) descartada(s) del ranking por Open "
                f"Interest $0 confirmado en una de las dos piernas ({symbols_dead}) — el exchange "
                "responde un funding rate pero no hay ninguna posición abierta ahí, así que no es "
                "una operación ejecutable de verdad. Detalle en el diagnóstico de abajo."
            )

        if not opportunities:
            st.info(
                "Todas las oportunidades del top se descartaron — ver los avisos de arriba (Open "
                "Interest $0 confirmado y/o Price Spread implausible)."
            )

        df = pd.DataFrame(
            [
                {
                    "Símbolo": o.symbol,
                    "Long en": f"{o.long_exchange} ({o.long_apr:+.1f}%)",
                    "Short en": f"{o.short_exchange} ({o.short_apr:+.1f}%)",
                    "Spread APR": o.spread_apr,
                    "Price Spread": o.price_spread_pct,
                    "Consistency (30d)": o.consistency_pct,
                    "OI long ($)": o.oi_long_usd,
                    "OI short ($)": o.oi_short_usd,
                    "Cuello de botella OI ($)": o.oi_bottleneck_usd,
                    "Lado OI": o.oi_bottleneck_side or "—",
                    "Vol 24h long ($)": o.volume_long_usd,
                    "Vol 24h short ($)": o.volume_short_usd,
                    "Cuello de botella Vol ($)": o.volume_bottleneck_usd,
                    "Lado Vol": o.volume_bottleneck_side or "—",
                }
                for o in opportunities
            ]
        )

        # Todas las columnas numéricas de abajo se pasan como número crudo
        # (float o None), NUNCA pre-formateadas a texto (a diferencia de una
        # versión anterior de esta tabla, que usaba _fmt_usd/_fmt_pct para
        # evitar un supuesto "None" literal de column_config.NumberColumn).
        # BUG REAL encontrado en producción: pre-formatear como texto rompe
        # el orden al pulsar la cabecera de columna — Streamlit ordena texto
        # alfabéticamente, así que "$9.4M" salía antes que "$898,647" (el '9'
        # gana al '8' comparando caracter a caracter, aunque 898,647 < 9.4M
        # como número). Comprobado con pandas/pyarrow que un None en una
        # columna float se convierte a NaN (no a la cadena "None") y
        # column_config.NumberColumn lo enseña en blanco — el motivo original
        # para pre-formatear ya no aplicaba, y estaba rompiendo el ordenado.
        st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            column_config={
                "Spread APR": st.column_config.NumberColumn(format="%.1f%%"),
                "Price Spread": st.column_config.NumberColumn(format="%.2f%%"),
                "Consistency (30d)": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=100
                ),
                "OI long ($)": st.column_config.NumberColumn(format="compact"),
                "OI short ($)": st.column_config.NumberColumn(format="compact"),
                "Cuello de botella OI ($)": st.column_config.NumberColumn(format="compact"),
                "Vol 24h long ($)": st.column_config.NumberColumn(format="compact"),
                "Vol 24h short ($)": st.column_config.NumberColumn(format="compact"),
                "Cuello de botella Vol ($)": st.column_config.NumberColumn(format="compact"),
            },
        )
        st.caption(
            "Price Spread: diferencia de precio (mark price) entre las dos piernas — un coste que "
            "se paga una sola vez al entrar y que puede comerse varios días de funding acumulado si "
            "sale alto; no confundir con el Spread APR, que es el beneficio recurrente. "
            "Consistency: % del tiempo (30d) que esta asignación long/short habría sido rentable. "
            "OI = profundidad de open interest en cada pierna, solo para el top "
            f"{OI_ENRICH_TOP_N}. Vol 24h = volumen negociado en 24h en cada pierna — a diferencia del "
            "OI (cuánto hay abierto ahora), dice cuánto se ha estado moviendo; un mercado con OI "
            "decente pero volumen bajo probablemente tenga más slippage del que el OI por sí solo "
            "sugiere. No todos los exchanges lo exponen en su fetch masivo, así que puede salir en "
            "blanco incluso fuera del top. Lado OI/Lado Vol: qué pierna (long/short) es la más fina "
            "en cada caso — en columna aparte para que las de cuello de botella sean 100% numéricas y "
            "se puedan ordenar bien. «—» = sin dato disponible."
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

if dead_liquidity:
    with st.expander(f"Diagnóstico: {len(dead_liquidity)} oportunidad(es) descartada(s) por OI $0"):
        st.caption(
            "Ver core/opportunities.py::has_dead_liquidity. No es un fallo de conexión (eso "
            "sale en 'OI Depth no disponible' de abajo) — es el exchange respondiendo que el "
            "Open Interest real de esa pierna es exactamente $0."
        )
        st.json(
            [
                {
                    "símbolo": o.symbol,
                    "long": f"{o.long_exchange} (OI {_fmt_usd(o.oi_long_usd)})",
                    "short": f"{o.short_exchange} (OI {_fmt_usd(o.oi_short_usd)})",
                    "spread_apr_descartado": f"{o.spread_apr:.1f}%",
                }
                for o in dead_liquidity
            ]
        )

if same_exchange_pairs:
    with st.expander(
        f"Diagnóstico: {len(same_exchange_pairs)} oportunidad(es) con long y short en el MISMO exchange"
    ):
        st.caption(
            "compute_opportunities() no comprueba que las dos piernas vengan de exchanges "
            "distintos — esto pasa cuando un solo exchange aporta 2+ filas para el mismo símbolo "
            "normalizado (ver comentario justo encima de esta variable en este archivo, y "
            "core/opportunities.py). Compara long_raw_symbol/short_raw_symbol: si son dos "
            "contratos reales distintos del mismo exchange (ej. un perpetuo y uno con "
            "vencimiento, o dos variantes de quote/margen), eso confirma el mecanismo — si son "
            "IDÉNTICOS, sería otra cosa (duplicado literal en la respuesta del exchange/conector)."
        )
        # Calculado aquí mismo, no hace falta que el usuario compare 123 filas
        # a ojo en un JSON anidado (que Streamlit pagina en rangos [0-99]/
        # [100-122] e ilegible en una captura): cuenta cuántas de estas filas
        # tienen raw_symbol IDÉNTICO en las dos piernas (duplicado literal)
        # frente a cuántas tienen dos raw_symbol DISTINTOS (dos contratos
        # reales del mismo exchange colisionando al normalizar).
        n_identical = sum(1 for o in same_exchange_pairs if o.long_raw_symbol == o.short_raw_symbol)
        n_different = len(same_exchange_pairs) - n_identical
        st.caption(
            f"De {len(same_exchange_pairs)} filas: **{n_identical}** tienen long_raw_symbol == "
            f"short_raw_symbol EXACTAMENTE IGUAL (duplicado literal del mismo contrato) y "
            f"**{n_different}** tienen raw_symbol DISTINTO (dos contratos reales distintos del "
            "mismo exchange colisionando al normalizar). La tabla de abajo ya viene ordenada por eso."
        )
        same_exchange_df = pd.DataFrame(
            [
                {
                    "Símbolo": o.symbol,
                    "Exchange": o.long_exchange,
                    "long_raw_symbol": o.long_raw_symbol,
                    "short_raw_symbol": o.short_raw_symbol,
                    "raw_symbol idéntico": o.long_raw_symbol == o.short_raw_symbol,
                    "long_apr": f"{o.long_apr:.4f}%",
                    "short_apr": f"{o.short_apr:.4f}%",
                }
                for o in same_exchange_pairs
            ]
        ).sort_values(["raw_symbol idéntico", "Exchange", "Símbolo"])
        st.dataframe(same_exchange_df, use_container_width=True, hide_index=True, height=400)

if implausible_pairs:
    with st.expander(
        f"Diagnóstico: {len(implausible_pairs)} oportunidad(es) descartada(s) por Price Spread implausible"
    ):
        st.caption(
            "Ver core/opportunities.py::has_implausible_price_pair. Símbolo real (raw_symbol) y "
            "mark_price de cada pierna — si son de órdenes de magnitud muy distintos (como aquí "
            "abajo) casi seguro son dos activos sin relación que comparten el mismo ticker corto, "
            "no el mismo activo con una divergencia real."
        )
        st.json(
            [
                {
                    "símbolo (normalizado)": o.symbol,
                    "long": f"{o.long_exchange} · raw={o.long_raw_symbol} · mark_price={o.long_mark_price}",
                    "short": f"{o.short_exchange} · raw={o.short_raw_symbol} · mark_price={o.short_mark_price}",
                    "price_spread_pct": f"{o.price_spread_pct:.2f}%",
                }
                for o in sorted(implausible_pairs, key=lambda o: o.price_spread_pct, reverse=True)
            ]
        )

if oi_errors:
    with st.expander(f"Diagnóstico: OI Depth no disponible para {len(oi_errors)} pierna(s) del top {OI_ENRICH_TOP_N}"):
        st.caption(
            "Motivo real por (exchange, símbolo) de por qué esa pierna concreta se queda en «—» "
            "en vez de mostrar profundidad — no es que falte el dato, es lo que respondió (o no) el exchange."
        )
        st.json({f"{ex} · {sym}": msg for (ex, sym), msg in oi_errors.items()})

# Diagnóstico Price Spread alto (reportado en producción, 2026-09-16): tras
# arreglar el bug de GRVT, siguen apareciendo Price Spread de decenas/cientos
# de % en pares que NO tocan GRVT (ej. CAT bitget/mexc, RTX gate/aster, HK50
# mexc/gate...). El "Símbolo" de la tabla de Ranking es el ticker corto ya
# normalizado (core/normalize.py / cex_ccxt.py: `base = ...symbol.split("/")[0]`)
# — a propósito no se enseña ahí el símbolo real de cada exchange, así que no
# hay forma de distinguir desde la tabla si es (a) divergencia real de precio
# entre exchanges poco líquidos (justo lo que esta métrica está pensada para
# avisar, ver docstring de core/scoring.py) o (b) un choque de símbolos: dos
# activos DISTINTOS que casualmente normalizan al mismo ticker corto (p.ej. un
# contrato con prefijo "1000X" en un exchange frente al mismo ticker sin ese
# prefijo en otro, o un ticker de 3-4 letras que por casualidad coincide entre
# un memecoin y otra cosa). En vez de adivinar cuál de las dos es, se enseña
# aquí el símbolo real (raw_symbol) y el mark_price de cada pierna para las
# oportunidades con el Price Spread más alto — con eso sí se puede saber a
# ciencia cierta cuál de las dos hipótesis es la correcta, sin inventar nada.
high_price_spread = sorted(
    (o for o in opportunities if o.price_spread_pct is not None),
    key=lambda o: o.price_spread_pct,
    reverse=True,
)[:20]
if high_price_spread:
    with st.expander(
        f"Diagnóstico: Price Spread más alto (top {len(high_price_spread)}) — símbolo real y precio de cada pierna"
    ):
        st.caption(
            "Compara long_raw_symbol/short_raw_symbol: si son el mismo activo con nombres distintos "
            "(ej. 'CATUSDT' vs '1000CATUSDT') es un choque de símbolos al normalizar, no una "
            "divergencia de precio real. Si de verdad son el mismo contrato en ambos exchanges y "
            "aun así el mark_price difiere tanto, es divergencia real (probablemente por baja "
            "liquidez, ver docstring de core/scoring.py)."
        )
        st.json(
            [
                {
                    "símbolo (normalizado)": o.symbol,
                    "long": f"{o.long_exchange} · raw={o.long_raw_symbol} · mark_price={o.long_mark_price}",
                    "short": f"{o.short_exchange} · raw={o.short_raw_symbol} · mark_price={o.short_mark_price}",
                    "price_spread_pct": f"{o.price_spread_pct:.2f}%",
                }
                for o in high_price_spread
            ]
        )
