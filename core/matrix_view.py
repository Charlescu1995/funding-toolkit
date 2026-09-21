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


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def apr_cell_color(value: float, vmin: float = -50, vmax: float = 50) -> str:
    t = max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
    rgb = _lerp_color(_GREEN, _AMBER, t / 0.5) if t < 0.5 else _lerp_color(_AMBER, _RED, (t - 0.5) / 0.5)
    return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


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
    """
    header = "".join(f"<th style='padding:8px 14px;text-align:right;font-weight:600;'>{ex}</th>" for ex in columns)
    rows_html = []
    for symbol in sorted(matrix):
        row = matrix[symbol]
        values = {ex: r.apr_pct for ex, r in row.items()}
        best_long = min(values, key=values.get) if len(values) >= 2 else None
        best_short = max(values, key=values.get) if len(values) >= 2 else None

        cells = [f"<td style='padding:8px 14px;font-weight:600;'>{escape(symbol)}</td>"]
        for ex in columns:
            if ex not in row:
                cells.append(
                    "<td style='padding:8px 14px;text-align:right;color:#5b6472;'>—</td>"
                )
                continue
            apr = row[ex].apr_pct
            bg = apr_cell_color(apr)
            tag = ""
            if ex == best_long:
                tag = " · LONG"
            elif ex == best_short:
                tag = " · SHORT"

            url, confirmed = build_link(ex, symbol)
            if not confirmed:
                marker = " ?"
                title = (
                    f"{ex}: no se pudo confirmar un enlace directo a {symbol} -- esto abre la "
                    "página general de trading del exchange, no el par concreto."
                )
            elif ex in REGION_RESTRICTED_EXCHANGES:
                marker = " 🌍"
                title = (
                    f"Abrir {symbol} en {ex} -- el enlace apunta al par correcto, pero este "
                    "exchange puede bloquear el producto según tu país."
                )
            else:
                marker = ""
                title = f"Abrir {symbol} en {ex}"

            cells.append(
                f"<td style='padding:8px 14px;text-align:right;background:{bg};border-radius:4px;'>"
                f"<a href='{escape(url)}' target='_blank' rel='noopener noreferrer' title='{escape(title)}' "
                f"style='display:block;color:#0b0e14;font-weight:700;text-decoration:none;'>"
                f"{apr:+.1f}%{tag}{marker}</a></td>"
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
