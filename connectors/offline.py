"""
Conector "offline": lee un fixture JSON grabado en vez de llamar al exchange.

Sirve para dos cosas:
1. Probar el pipeline completo aquí, en un entorno sin salida a internet real
   hacia los exchanges.
2. Como base para tests automáticos más adelante (no dependemos de que la red
   esté arriba para comprobar que la lógica de normalización/ranking es correcta).

No sustituye a los conectores reales — en tu máquina, con internet normal,
usa siempre CexConnector / HyperliquidConnector para datos en vivo.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import FundingRate, VenueType

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


class OfflineConnector:
    def __init__(self, name: str, venue_type: VenueType, fixture_file: str):
        self.name = name
        self.venue_type = venue_type
        self._path = FIXTURES_DIR / fixture_file

    def fetch_funding_rates(self) -> list[FundingRate]:
        with open(self._path, encoding="utf-8") as f:
            records = json.load(f)

        return [
            FundingRate(
                exchange=self.name,
                venue_type=self.venue_type,
                symbol=r["symbol"],
                raw_symbol=r.get("raw_symbol", r["symbol"]),
                funding_rate=r["funding_rate"],
                interval_hours=r["interval_hours"],
                mark_price=r.get("mark_price"),
                open_interest_usd=r.get("open_interest_usd"),
            )
            for r in records
        ]


def binance_offline() -> OfflineConnector:
    return OfflineConnector("binanceusdm", VenueType.CEX, "binance_funding.json")


def bybit_offline() -> OfflineConnector:
    return OfflineConnector("bybit", VenueType.CEX, "bybit_funding.json")


def hyperliquid_offline() -> OfflineConnector:
    return OfflineConnector("hyperliquid", VenueType.DEX, "hyperliquid_funding.json")
