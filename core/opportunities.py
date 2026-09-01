"""
Cálculo de oportunidades compartido entre el CLI y la interfaz Streamlit —
para no tener la misma lógica de "mejor long/short + consistency + OI depth"
escrita dos veces en dos sitios que se puedan desincronizar.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from core.normalize import NormalizedRate
from core.scoring import oi_depth
from core.scoring import consistency_score as _consistency_score


@dataclass
class OpportunityRow:
    symbol: str
    long_exchange: str
    long_apr: float
    short_exchange: str
    short_apr: float
    spread_apr: float
    consistency_pct: float | None
    consistency_samples: int
    oi_long_usd: float | None
    oi_short_usd: float | None
    oi_bottleneck_usd: float | None
    oi_bottleneck_side: str | None


def compute_opportunities(
    rates: list[NormalizedRate], history_conn: sqlite3.Connection | None
) -> list[OpportunityRow]:
    by_symbol: dict[str, list[NormalizedRate]] = defaultdict(list)
    for r in rates:
        by_symbol[r.symbol].append(r)

    rows: list[OpportunityRow] = []
    for symbol, group in by_symbol.items():
        if len(group) < 2:
            continue

        long_leg = min(group, key=lambda r: r.apr_pct)
        short_leg = max(group, key=lambda r: r.apr_pct)
        spread = short_leg.apr_pct - long_leg.apr_pct

        cons_pct: float | None = None
        cons_samples = 0
        if history_conn is not None:
            cons = _consistency_score(history_conn, long_leg.exchange, short_leg.exchange, symbol)
            cons_pct = cons.score_pct
            cons_samples = cons.samples

        depth = oi_depth(long_leg, short_leg)

        rows.append(
            OpportunityRow(
                symbol=symbol,
                long_exchange=long_leg.exchange,
                long_apr=long_leg.apr_pct,
                short_exchange=short_leg.exchange,
                short_apr=short_leg.apr_pct,
                spread_apr=spread,
                consistency_pct=cons_pct,
                consistency_samples=cons_samples,
                oi_long_usd=depth.long_oi_usd,
                oi_short_usd=depth.short_oi_usd,
                oi_bottleneck_usd=depth.bottleneck_usd,
                oi_bottleneck_side=depth.bottleneck_side,
            )
        )

    rows.sort(key=lambda r: r.spread_apr, reverse=True)
    return rows
