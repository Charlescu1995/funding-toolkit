"""
Paso 6 — vista matriz (estilo Loris): en vez de una lista ya pre-filtrada de
"mejores oportunidades" (que es lo que hace `render_opportunities` en cli.py,
al estilo ProFunding), esto pone cada símbolo contra cada exchange en una
sola rejilla — para quien quiera ver el dato crudo sin que nadie decida por
él qué es "lo mejor".

Las dos vistas conviven a propósito: el ranking es el punto de entrada para
decidir rápido, la matriz es el modo auditoría/avanzado.
"""

from __future__ import annotations

from collections import defaultdict

from core.normalize import NormalizedRate


def build_matrix(rates: list[NormalizedRate]) -> dict[str, dict[str, NormalizedRate]]:
    """symbol -> {exchange -> NormalizedRate}. Un símbolo puede faltar en algún exchange."""
    matrix: dict[str, dict[str, NormalizedRate]] = defaultdict(dict)
    for r in rates:
        matrix[r.symbol][r.exchange] = r
    return matrix


def exchange_columns(rates: list[NormalizedRate]) -> list[str]:
    """Lista de exchanges presentes, en un orden estable (CEX primero, luego DEX, alfabético dentro de cada uno)."""
    seen = {r.exchange: r.venue_type.value for r in rates}
    return sorted(seen, key=lambda ex: (seen[ex], ex))
