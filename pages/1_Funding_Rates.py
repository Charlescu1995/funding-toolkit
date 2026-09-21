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

from connectors.exchange_links import REGION_RESTRICTED_EXCHANGES, UNCONFIRMED_EXCHANGES, build_link
from core.aggregate import build_matrix, exchange_columns
from core.data_service import fetch_normalized_rates, filter_rates
from core.history import WINDOWS_HOURS, historical_apr_all_windows, init_db
from core.matrix_view import render_matrix_html
from core.opportunities import (
    LOW_LIQUIDITY_FLOOR_USD,
    apply_oi_map,
    collect_oi_targets,
    compute_opportunities,
    fetch_oi_for_targets,
    has_dead_liquidity,
    has_implausible_price_pair,
    has_low_liquidity,
)

# Cuántas oportunidades (de arriba del ranking) se enriquecen con OI Depth
# real. A propósito no son todas: pedir OI símbolo a símbolo para miles de
# pares sería lento y quemaría el rate limit para nada — solo importa la
# profundidad de las pocas que ya decidiste mirar.
OI_ENRICH_TOP_N = 10


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
low_liquidity: list = []  # idem — descartadas por Cuello de botella OI/Vol < piso (ver has_low_liquidity)
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
        # RESUELTO (2026-09-19, auditoría de bugs): el usuario detectó, en un
        # export CSV del Ranking, 121 filas donde long_exchange ==
        # short_exchange con Spread APR exactamente 0.0% (ej. SSV en okx vs
        # okx, CIFR en gate vs gate, MUSTOCK en mexc vs mexc).
        # compute_opportunities() nunca comprueba que las dos piernas de una
        # oportunidad vengan de exchanges distintos — solo agrupa por
        # símbolo normalizado y coge el mínimo/máximo APR del grupo (ver
        # core/opportunities.py). Se dejó "en observación" varios
        # despliegues porque el caso confirmado (Spread=0%, dos filas
        # EMPATADAS en apr_pct — min()/max() de Python devuelven el mismo
        # objeto como long_leg Y short_leg cuando hay empate, confirmado
        # probándolo) siempre se hundía solo al fondo del ranking por no ser
        # atractivo.
        #
        # Pero auditando el código se encontró que ESTE filtro, a diferencia
        # de los tres de abajo (implausible_pairs/dead_liquidity/
        # low_liquidity), se calculaba para el diagnóstico pero nunca se
        # restaba de `opportunities` — así que si alguna vez dos filas
        # DISTINTAS del mismo exchange (no empatadas) colisionan al
        # normalizar, con un Spread APR llamativo en vez de 0%, esa fila se
        # colaría en el Ranking principal como si fuera un arbitraje cruzado
        # real, sin ningún aviso salvo abrir el expander de diagnóstico. Se
        # descarta ya, mismo patrón que los otros tres filtros.
        same_exchange_pairs = [o for o in opportunities if o.long_exchange == o.short_exchange]
        opportunities = [o for o in opportunities if o.long_exchange != o.short_exchange]

        if same_exchange_pairs:
            symbols_same_exchange = ", ".join(sorted({o.symbol for o in same_exchange_pairs}))
            st.caption(
                f"⚠️ {len(same_exchange_pairs)} oportunidad(es) descartada(s) del ranking por tener "
                f"long y short en el MISMO exchange ({symbols_same_exchange}) — no es un arbitraje "
                "cruzado real. Detalle en el diagnóstico de abajo."
            )

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

        # Ver core/opportunities.py::has_low_liquidity — piso de liquidez
        # mínima pedido por el usuario tras estudiar el CSV completo del
        # Ranking (2026-09-19, subido de $1.000 a $5.000 el 2026-09-21 tras
        # el caso real MOG): un OI o Volumen 24h CONFIRMADO por debajo del
        # piso en CUALQUIERA de las cuatro piernas (OI long/short, Vol
        # long/short — no solo el "cuello de botella" ya calculado, que
        # exige las dos piernas conocidas, ver el bug real documentado en el
        # docstring de has_low_liquidity) es "una trampa" (Spread APR
        # llamativo, pero imposible de operar en ningún tamaño real, o un
        # mercado a punto de darse de baja) — no solo el caso extremo de $0
        # exacto que ya saca has_dead_liquidity de arriba.
        low_liquidity = [o for o in opportunities if has_low_liquidity(o)]
        opportunities = [o for o in opportunities if not has_low_liquidity(o)]

        if low_liquidity:
            symbols_low_liquidity = ", ".join(sorted({o.symbol for o in low_liquidity}))
            st.caption(
                f"⚠️ {len(low_liquidity)} oportunidad(es) descartada(s) del ranking por OI o "
                f"Volumen 24h por debajo de ${LOW_LIQUIDITY_FLOOR_USD:,.0f} confirmado en al menos "
                f"una pierna ({symbols_low_liquidity}) — no hace falta que las dos piernas tengan "
                "dato, con que UNA sola esté confirmada por debajo del piso ya es una trampa: casi "
                "nadie tradeando de verdad ahí, así que no es una operación ejecutable en ningún "
                "tamaño razonable aunque el Spread APR parezca bueno. Detalle en el diagnóstico de "
                "abajo."
            )

        if not opportunities:
            st.info(
                "Todas las oportunidades del top se descartaron — ver los avisos de arriba (long/"
                "short en el mismo exchange, Open Interest $0 confirmado, Cuello de botella OI/Vol "
                "por debajo del piso de liquidez y/o Price Spread implausible)."
            )

        # Links directos a cada exchange (punto 5 de la lista de Carlos/
        # Charles, 2026-09-21, ver connectors/exchange_links.py): se
        # calculan aquí, fila a fila, a partir del símbolo base ya
        # normalizado (o.symbol) y el exchange de cada pierna -- build_link()
        # nunca lanza ni devuelve None, así que no hace falta try/except.
        long_links = [build_link(o.long_exchange, o.symbol) for o in opportunities]
        short_links = [build_link(o.short_exchange, o.symbol) for o in opportunities]

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
                    "Abrir Long": long_url,
                    "Abrir Short": short_url,
                }
                for o, (long_url, _long_confirmed), (short_url, _short_confirmed) in zip(
                    opportunities, long_links, short_links
                )
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
                "Abrir Long": st.column_config.LinkColumn(display_text="Abrir ↗"),
                "Abrir Short": st.column_config.LinkColumn(display_text="Abrir ↗"),
            },
        )
        if UNCONFIRMED_EXCHANGES:
            # Ver connectors/exchange_links.py: para estos exchanges no se
            # pudo confirmar en vivo un patrón de URL que preseleccione el
            # símbolo (SPA sin ruta indexable, acceso con whitelist, o sin
            # documentación) -- "Abrir Long"/"Abrir Short" llevan ahí a la
            # página general de trading, SIN el símbolo puesto, en vez de
            # inventar una URL que podría no funcionar.
            st.caption(
                "⚠️ Para "
                + ", ".join(sorted(UNCONFIRMED_EXCHANGES))
                + " no se pudo confirmar en vivo un enlace directo al símbolo exacto (interfaz muy "
                "dependiente de JavaScript, acceso restringido, o sin documentación) — 'Abrir' lleva "
                "a la página general de trading de ese exchange, no al par concreto."
            )
        if REGION_RESTRICTED_EXCHANGES:
            # Ver connectors/exchange_links.py (auditoría 2026-09-21): aquí el
            # enlace SÍ apunta al símbolo correcto, pero el propio exchange
            # bloquea el producto entero para ciertas regiones (confirmado en
            # vivo desde España) -- distinto de "no confirmado" de arriba.
            st.caption(
                "🌍 "
                + ", ".join(sorted(REGION_RESTRICTED_EXCHANGES))
                + " pueden mostrar un aviso de bloqueo regional al abrir 'Abrir Long'/'Abrir Short' "
                "según desde dónde te conectes (confirmado en vivo desde España, 2026-09-21) — el "
                "enlace en sí apunta al par correcto, es el propio exchange el que restringe el "
                "producto por país."
            )
        st.caption(
            "⏱️ Un enlace 'Abrir' puede fallar (o el exchange puede no reconocer el símbolo) aunque "
            "la plantilla esté bien: el dato se saca con caché de 60s, y un mercado muy ilíquido "
            "puede darse de baja entre que lo vemos aquí y que haces clic — caso real comprobado: "
            "MOG en Bitget y ApeX, ambos exchanges dejaron de reconocer el símbolo en su propia API "
            "justo después de aparecer como oportunidad, 2026-09-21. Cuanto más bajo el 'Cuello de "
            "botella OI' de una pierna, más riesgo de esto — no es algo que se pueda arreglar desde "
            "el enlace, es el propio mercado desapareciendo."
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
    st.caption(
        "Cada símbolo contra cada exchange, sin pre-filtrar — el dato crudo, estilo Loris. "
        "Haz clic en cualquier porcentaje para abrir ese par directamente en ese exchange."
    )
    matrix = build_matrix(rates)
    columns = exchange_columns(rates)

    # Render manual en HTML: st.dataframe + pandas Styler enseña "None" en las
    # celdas vacías en vez de un guion, y no hay forma limpia de evitarlo con
    # column_config — así que aquí controlamos el pixel exacto nosotros.
    st.markdown(render_matrix_html(matrix, columns), unsafe_allow_html=True)
    st.caption("Verde = mejor sitio para ir long (te pagan más). Rojo = mejor sitio para ir short.")
    if UNCONFIRMED_EXCHANGES:
        st.caption(
            "? = "
            + ", ".join(sorted(UNCONFIRMED_EXCHANGES))
            + ": no se pudo confirmar en vivo un enlace directo al símbolo — el clic lleva a la "
            "página general de trading de ese exchange, no al par concreto (mismo criterio que "
            "'Abrir Long'/'Abrir Short' en el Ranking, ver aviso ⚠️ en esa pestaña)."
        )
    if REGION_RESTRICTED_EXCHANGES:
        st.caption(
            "🌍 = "
            + ", ".join(sorted(REGION_RESTRICTED_EXCHANGES))
            + ": el enlace apunta al par correcto, pero el propio exchange puede bloquear el "
            "producto según tu país (confirmado en vivo desde España, 2026-09-21)."
        )

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

if low_liquidity:
    with st.expander(
        f"Diagnóstico: {len(low_liquidity)} oportunidad(es) descartada(s) por liquidez "
        f"< ${LOW_LIQUIDITY_FLOOR_USD:,.0f}"
    ):
        st.caption(
            "Ver core/opportunities.py::has_low_liquidity. Se descarta si CUALQUIERA de las cuatro "
            f"piernas (OI long, OI short, Vol 24h long, Vol 24h short) tiene un valor CONFIRMADO "
            f"por debajo de ${LOW_LIQUIDITY_FLOOR_USD:,.0f} — no hace falta que las dos piernas de "
            "un mismo lado (OI o Vol) tengan dato: una sola pierna confirmada ya basta, no incluye "
            "filas donde ese dato simplemente no se consultó (eso se enseña como «—» en la tabla, "
            "no se descarta). Bug real corregido el 2026-09-19: la primera versión solo miraba el "
            "'cuello de botella' ya calculado (que exige las DOS piernas conocidas) y dejaba pasar "
            "casos como B2 (Vol 24h short confirmado=$16, con la otra pierna sin consultar). Umbral "
            "calibrado contra los percentiles del CSV completo del Ranking (ver README): cae entre "
            "p5 y p10 de ambas distribuciones, así que solo saca el ~5-8% más ilíquido de cada una."
        )

        def _min_known(*values: float | None) -> float:
            known = [v for v in values if v is not None]
            return min(known) if known else float("inf")

        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Símbolo": o.symbol,
                        "Long en": o.long_exchange,
                        "Short en": o.short_exchange,
                        "Spread APR descartado": f"{o.spread_apr:.1f}%",
                        "OI long ($)": o.oi_long_usd,
                        "OI short ($)": o.oi_short_usd,
                        "Vol 24h long ($)": o.volume_long_usd,
                        "Vol 24h short ($)": o.volume_short_usd,
                    }
                    for o in sorted(
                        low_liquidity,
                        key=lambda o: _min_known(
                            o.oi_long_usd, o.oi_short_usd, o.volume_long_usd, o.volume_short_usd
                        ),
                    )
                ]
            ),
            width="stretch",
            hide_index=True,
            height=400,
        )

if same_exchange_pairs:
    with st.expander(
        f"Diagnóstico: {len(same_exchange_pairs)} oportunidad(es) descartada(s) por long y short "
        "en el MISMO exchange"
    ):
        st.caption(
            "Ver core/opportunities.py::compute_opportunities — no comprueba que las dos piernas "
            "vengan de exchanges distintos — esto pasa cuando un solo exchange aporta 2+ filas para "
            "el mismo símbolo normalizado (ver comentario en pages/1_Funding_Rates.py, justo antes "
            "de calcular esta lista). Compara long_raw_symbol/short_raw_symbol: si son dos "
            "contratos reales distintos del mismo exchange (ej. un perpetuo y uno con "
            "vencimiento, o dos variantes de quote/margen), eso confirma el mecanismo — si son "
            "IDÉNTICOS, es un empate de apr_pct (duplicado literal en la respuesta del exchange/"
            "conector, o dos filas idénticas del mismo contrato) — min()/max() de Python devuelven "
            "el mismo objeto en ambos lados cuando hay empate, confirmado probándolo. Ya se "
            "descartan del ranking (2026-09-19), esto es solo diagnóstico de por qué."
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
        st.dataframe(same_exchange_df, width="stretch", hide_index=True, height=400)

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
