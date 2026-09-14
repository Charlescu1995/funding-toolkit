"""
Paso 5 — Consistency Score y OI Depth.

Estas dos métricas son las que le robamos a John5Cripto: la pieza que falta
entre "este spread tiene un APR altísimo" y "esto es de fiar".

Consistency Score
------------------
Un spread puede tener un APR enorme ahora mismo y aun así ser una trampa si
lleva revirtiéndose cada pocas horas (un día pagan los longs, al siguiente
pagan los shorts). El score mide qué porcentaje del tiempo, en la ventana
elegida, la asignación long/short que recomendamos se habría mantenido a tu
favor (spread > 0).

    100%  -> siempre fue rentable en esa dirección durante la ventana
     50%  -> básicamente una moneda al aire
      0%  -> se lo llevó siempre el lado contrario

No es una garantía de futuro, es una foto del pasado — pero es infinitamente
mejor que decidir solo con la tasa de este instante.

OI Depth
--------
Cuánto open interest hay en cada pierna de la operación. No es exactamente
"cuánto puedes meter sin mover el precio" (para eso haría falta el libro de
órdenes, que es un dato más caro de conseguir), pero es la aproximación
barata estándar: si una de las dos piernas tiene un OI muy bajo comparado con
el tamaño que quieres mover, esa es la señal de alerta de que vas a sufrir
slippage entrando o saliendo.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.normalize import NormalizedRate

DEFAULT_WINDOW_HOURS = 24 * 30  # 30 días, igual que la ventana más larga del histórico
MIN_SAMPLES_FOR_SCORE = 20      # con menos snapshots que esto, no publicamos un score


@dataclass
class ConsistencyResult:
    score_pct: float | None   # None si no hay histórico suficiente
    samples: int
    enough_history: bool


def consistency_score(
    conn: sqlite3.Connection,
    long_exchange: str,
    short_exchange: str,
    symbol: str,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    min_samples: int = MIN_SAMPLES_FOR_SCORE,
    now: datetime | None = None,
) -> ConsistencyResult:
    """
    % de snapshots, en la ventana dada, donde short_exchange pagó más que
    long_exchange (es decir, donde ir long en `long_exchange` y short en
    `short_exchange` habría sido la asignación correcta).
    """
    since = ((now or datetime.now(timezone.utc)) - timedelta(hours=window_hours)).isoformat()

    cur = conn.execute(
        """
        SELECT a.apr_pct, b.apr_pct
        FROM funding_snapshots a
        JOIN funding_snapshots b
          ON a.captured_at = b.captured_at AND a.symbol = b.symbol
        WHERE a.exchange = ? AND b.exchange = ? AND a.symbol = ? AND a.captured_at >= ?
        """,
        (long_exchange, short_exchange, symbol, since),
    )
    rows = cur.fetchall()

    if len(rows) < min_samples:
        return ConsistencyResult(score_pct=None, samples=len(rows), enough_history=False)

    favorable = sum(1 for long_apr, short_apr in rows if short_apr > long_apr)
    score = (favorable / len(rows)) * 100
    return ConsistencyResult(score_pct=score, samples=len(rows), enough_history=True)


@dataclass
class OIDepth:
    long_oi_usd: float | None
    short_oi_usd: float | None
    bottleneck_usd: float | None  # la pierna más fina; el tamaño real que aguanta la operación

    @property
    def bottleneck_side(self) -> str | None:
        if self.long_oi_usd is None or self.short_oi_usd is None:
            return None
        return "long" if self.long_oi_usd <= self.short_oi_usd else "short"


def oi_depth(long_rate: NormalizedRate, short_rate: NormalizedRate) -> OIDepth:
    long_oi = long_rate.open_interest_usd
    short_oi = short_rate.open_interest_usd
    bottleneck = min(long_oi, short_oi) if long_oi is not None and short_oi is not None else None
    return OIDepth(long_oi_usd=long_oi, short_oi_usd=short_oi, bottleneck_usd=bottleneck)
