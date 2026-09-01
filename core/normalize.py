"""
Paso 3 — Normalización de intervalos y cálculo de APR.

Este es el problema que resuelve Loris y que ProFunding no explica: cada
exchange liquida el funding a un ritmo distinto (Hyperliquid cada 1h, Binance
cada 8h...). Comparar la tasa "en bruto" de dos exchanges sin tener esto en
cuenta es comparar peras con manzanas — una tasa de 0.01% cada 1h es MUCHO
más que una de 0.01% cada 8h (8 veces más, de hecho).

La solución: convertir todo a una misma base antes de comparar nada. Usamos
el APR anualizado como base común, que es lo que de verdad le importa a
alguien evaluando si le compensa la operación.

Fórmula: APR = tasa_del_intervalo × (horas_en_un_año / horas_del_intervalo)

Ej.: Binance, ETH al +0.10% cada 8h
     APR = 0.0010 × (8760 / 8) = 0.0010 × 1095 = 1.095 → 109.5% anual

     Hyperliquid, ETH al -0.0020% cada 1h
     APR = -0.0000200 × (8760 / 1) = -0.0000200 × 8760 = -0.1752 → -17.52% anual

Ahora sí son comparables entre sí, aunque un exchange liquide 8 veces más
seguido que el otro.
"""

from __future__ import annotations

from dataclasses import dataclass

from connectors.base import FundingRate, VenueType

HOURS_PER_YEAR = 8760  # 365 * 24


@dataclass
class NormalizedRate:
    """Un FundingRate ya convertido a una base comparable entre exchanges."""

    exchange: str
    venue_type: VenueType
    symbol: str
    raw_symbol: str          # símbolo tal cual lo usa el exchange — hace falta para pedir el OI después
    raw_rate: float          # tasa tal cual la reportó el exchange, para su intervalo nativo
    interval_hours: float
    apr_pct: float           # tasa anualizada, en % → esto es lo que se compara entre exchanges
    rate_per_8h_pct: float   # tasa reescalada a "cada 8h", en % → referencia rápida, estilo Loris
    mark_price: float | None
    open_interest_usd: float | None

    @property
    def pays_longs(self) -> bool:
        """True si, con esta tasa, quien está LONG cobra (tasa negativa)."""
        return self.raw_rate < 0

    @property
    def pays_shorts(self) -> bool:
        """True si, con esta tasa, quien está SHORT cobra (tasa positiva)."""
        return self.raw_rate > 0


def normalize(rate: FundingRate) -> NormalizedRate:
    """Convierte un FundingRate crudo a su forma normalizada (APR anualizado)."""

    if rate.interval_hours <= 0:
        raise ValueError(
            f"interval_hours inválido ({rate.interval_hours}) para {rate.exchange}/{rate.symbol}"
        )

    periods_per_year = HOURS_PER_YEAR / rate.interval_hours
    apr = rate.funding_rate * periods_per_year * 100

    periods_per_8h = 8 / rate.interval_hours
    rate_per_8h = rate.funding_rate * periods_per_8h * 100

    return NormalizedRate(
        exchange=rate.exchange,
        venue_type=rate.venue_type,
        symbol=rate.symbol,
        raw_symbol=rate.raw_symbol,
        raw_rate=rate.funding_rate,
        interval_hours=rate.interval_hours,
        apr_pct=apr,
        rate_per_8h_pct=rate_per_8h,
        mark_price=rate.mark_price,
        open_interest_usd=rate.open_interest_usd,
    )


def normalize_all(rates: list[FundingRate]) -> list[NormalizedRate]:
    return [normalize(r) for r in rates]
