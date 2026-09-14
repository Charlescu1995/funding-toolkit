"""
Conector para Extended — DEX de perpetuos sobre Starknet.

Un solo endpoint trae todos los mercados con sus stats anidadas en
`marketStats` (funding rate, mark price, open interest...).

Docs: https://api.docs.extended.exchange/ (GET /api/v1/info/markets)

Nota sobre unidades: a diferencia de Hyperliquid/Lighter/Paradex, Extended
documenta explícitamente DOS campos de open interest — `openInterest` (en el
activo de colateral, es decir ya en USD) y `openInterestBase` (en el activo
base). Usamos `openInterest` directamente, sin multiplicar por mark price.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

MARKETS_URL = "https://api.starknet.extended.exchange/api/v1/info/markets"

# Extended liquida funding cada hora (aunque la tasa se calcula sobre una
# ventana de 8h — ver docs.extended.exchange/extended-resources/trading/funding-payments).
INTERVAL_HOURS = 1


class ExtendedConnector:
    name = "extended"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(MARKETS_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", [])

        out: list[FundingRate] = []
        for row in rows:
            if row.get("type") not in (None, "PERPETUAL"):
                continue  # nos saltamos mercados spot si el endpoint los mezclara

            raw_symbol = row.get("name")  # ej. "BTC-USD"
            stats = row.get("marketStats") or {}
            rate = stats.get("fundingRate")
            if raw_symbol is None or rate is None:
                continue

            symbol = raw_symbol.split("-")[0]
            mark_price_raw = stats.get("markPrice")
            mark_price = float(mark_price_raw) if mark_price_raw is not None else None

            oi_raw = stats.get("openInterest")  # ya en USD (activo de colateral)
            oi_usd = float(oi_raw) if oi_raw is not None else None

            out.append(
                FundingRate(
                    exchange="extended",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=raw_symbol,
                    funding_rate=float(rate),
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=oi_usd,
                )
            )

        return out


def extended() -> ExtendedConnector:
    return ExtendedConnector()
