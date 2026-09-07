"""
Conector para Pacifica — DEX de perpetuos sobre Solana.

Un solo endpoint (`/prices`) trae funding, mark price y open interest para
todos los mercados a la vez.

Docs: https://docs.pacifica.fi/api-documentation/api/rest-api/markets/get-prices

Nota sobre unidades: `open_interest` no está documentado explícitamente como
USD o como unidades del activo base. Se trata como unidades base (igual que
Hyperliquid/Lighter/Paradex) y se convierte a USD con el mark price — es la
convención más común; revisar si al desplegar los números salen desproporcionados.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

PRICES_URL = "https://api.pacifica.fi/api/v1/info/prices"

# Pacifica liquida funding cada hora ("funding rate paid in the past funding
# epoch (hour)" — docs.pacifica.fi).
INTERVAL_HOURS = 1


class PacificaConnector:
    name = "pacifica"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(PRICES_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()

        if not payload.get("success", True):
            # La API envuelve errores en un 200 con success=false en vez de
            # un status HTTP de error — lo convertimos en excepción real para
            # que no pase desapercibido como "0 pares".
            raise RuntimeError(f"Pacifica devolvió error: {payload.get('error')}")

        rows = payload.get("data", [])

        out: list[FundingRate] = []
        for row in rows:
            symbol = row.get("symbol")
            rate = row.get("funding")
            if symbol is None or rate is None:
                continue

            mark_price_raw = row.get("mark")
            mark_price = float(mark_price_raw) if mark_price_raw is not None else None

            open_interest_raw = row.get("open_interest")
            oi_usd = None
            if open_interest_raw is not None and mark_price is not None:
                try:
                    oi_usd = float(open_interest_raw) * mark_price
                except (TypeError, ValueError):
                    oi_usd = None

            out.append(
                FundingRate(
                    exchange="pacifica",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=symbol,
                    funding_rate=float(rate),
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=oi_usd,
                )
            )

        return out


def pacifica() -> PacificaConnector:
    return PacificaConnector()
