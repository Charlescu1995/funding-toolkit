"""
Vista matriz (estilo Loris) — HTML puro, sin ninguna llamada a Streamlit, a
propósito: así se puede importar y testear directamente con pytest, sin
arrastrar la ejecución completa de `pages/1_Funding_Rates.py` (que hace
`st.set_page_config` y trae datos en vivo nada más importarse). Extraído de
esa página el 2026-09-21 al añadir enlaces clicables en cada celda — antes
vivía ahí mismo sin test dedicado.

Paleta compartida con el resto del toolkit (mismo verde/ámbar/rojo que el
informe de análisis inicial), para que la matriz se sienta parte de la misma
herramienta.
"""

from __future__ import annotations

from html import escape

from connectors.exchange_links import REGION_RESTRICTED_EXCHANGES, build_link
from core.normalize import NormalizedRate

_GREEN = (94, 230, 196)   # mejor para ir LONG (tasa más baja)
_AMBER = (240, 180, 41)   # neutral
_RED = (240, 87, 107)     # mejor para ir SHORT (tasa más alta)

_DASH_TD = "<td style='padding:8px 14px;text-align:right;color:#5b6472;'>—</td>"


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def apr_cell_color(value: float, vmin: float = -50, vmax: float = 50) -> str:
    t = max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
    rgb = _lerp_color(_GREEN, _AMBER, t / 0.5) if t < 0.5 else _lerp_color(_AMBER, _RED, (t - 0.5) / 0.5)
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def _link_marker_and_title(exchange: str, symbol: str, confirmed: bool) -> tuple[str, str]:
    """
    Mismo criterio de "nunca inventar" que ya usa build_link(): un enlace sin
    confirmar sigue siendo clicable (fallback a la página general del
    exchange), pero se marca en la celda para que quede claro que no apunta
    al símbolo exacto -- nunca se finge un enlace directo que no se confirmó.
    """
    if not confirmed:
        return " ?", (
            f"{exchange}: no se pudo confirmar un enlace directo a {symbol} -- esto abre la "
            "página general de trading del exchange, no el par concreto."
        )
    if exchange in REGION_RESTRICTED_EXCHANGES:
        return " 🌍", (
            f"Abrir {symbol} en {exchange} -- el enlace apunta al par correcto, pero este "
            "exchange puede bloquear el producto según tu país."
        )
    return "", f"Abrir {symbol} en {exchange}"


def _link_td(exchange: str, symbol: str, label: str, bg: str) -> str:
    url, confirmed = build_link(exchange, symbol)
    marker, title = _link_marker_and_title(exchange, symbol, confirmed)
    return (
        f"<td style='padding:8px 14px;text-align:right;background:{bg};border-radius:4px;'>"
        f"<a href='{escape(url)}' target='_blank' rel='noopener noreferrer' title='{escape(title)}' "
        f"style='display:block;color:#0b0e14;font-weight:700;text-decoration:none;'>"
        f"{escape(label)}{marker}</a></td>"
    )


def render_matrix_html(matrix: dict[str, dict[str, NormalizedRate]], columns: list[str]) -> str:
    """
    Vista matriz, estilo Loris. A petición de Carlos (2026-09-21, viendo un
    screenshot real de la matriz de Loris): el propio % de cada celda es un
    enlace que abre ese símbolo en ese exchange -- se reutiliza `build_link()`
    (el mismo que ya usan "Abrir Long"/"Abrir Short" en el Ranking), así que
    hereda el mismo criterio de "nunca inventar" -- para los exchanges sin
    patrón confirmado, el enlace lleva a la página general del exchange (no
    al símbolo), marcado con "?" y un title explicándolo, en vez de fingir un
    enlace directo que no se pudo confirmar.

    Actualización (2026-09-21, misma noche): dos columnas nuevas, "Long" y
    "Short", justo después de Símbolo -- a petición de Carlos, para no tener
    que escanear toda la fila buscando el "· LONG"/"· SHORT" de la celda
    correcta. Cada una enseña el mejor exchange para esa dirección (el mismo
    best_long/best_short que ya se calculaba para marcar la celda) con su
    APR y su propio enlace directo -- exactamente el mismo `_link_td()`, así
    que hereda los mismos marcadores ?/🌍 que el resto de la matriz. Si el
    símbolo solo tiene un exchange con dato, no hay "mejor" long/short que
    elegir entre dos sitios distintos -- se deja en guion, igual que una
    celda sin dato.
    """
    header = "".join(f"<th style='padding:8px 14px;text-align:right;font-weight:600;'>{ex}</th>" for ex in columns)
    rows_html = []
    for symbol in sorted(matrix):
        row = matrix[symbol]
        values = {ex: r.apr_pct for ex, r in row.items()}
        best_long = min(values, key=values.get) if len(values) >= 2 else None
        best_short = max(values, key=values.get) if len(values) >= 2 else None

        cells = [f"<td style='padding:8px 14px;font-weight:600;'>{escape(symbol)}</td>"]

        if best_long is not None:
            apr = values[best_long]
            cells.append(_link_td(best_long, symbol, f"{best_long} {apr:+.1f}%", apr_cell_color(apr)))
        else:
            cells.append(_DASH_TD)

        if best_short is not None:
            apr = values[best_short]
            cells.append(_link_td(best_short, symbol, f"{best_short} {apr:+.1f}%", apr_cell_color(apr)))
        else:
            cells.append(_DASH_TD)

        for ex in columns:
            if ex not in row:
                cells.append(_DASH_TD)
                continue
            apr = row[ex].apr_pct
            tag = ""
            if ex == best_long:
                tag = " · LONG"
            elif ex == best_short:
                tag = " · SHORT"
            cells.append(_link_td(ex, symbol, f"{apr:+.1f}%{tag}", apr_cell_color(apr)))
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    return f"""
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:separate;border-spacing:0 4px;font-size:14px;">
      <thead><tr>
        <th style='padding:8px 14px;text-align:left;'>Símbolo</th>
        <th style='padding:8px 14px;text-align:right;font-weight:600;'>Long</th>
        <th style='padding:8px 14px;text-align:right;font-weight:600;'>Short</th>
        {header}
      </tr></thead>
      <tbody>{''.join(rows_html)}</tbody>
    </table>
    </div>
    """
