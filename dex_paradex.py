"""
Conector para Paradex — DEX de perpetuos sobre Starknet.

Un solo endpoint público trae funding rate, mark price y open interest para
todos los mercados a la vez (parecido a Hyperliquid, a diferencia de Lighter
que necesita cruzar dos llamadas).

Docs: https://docs.paradex.trade/api/prod/markets/get-markets-summary

Nota sobre unidades: al igual que en Hyperliquid/Lighter, `open_interest` se
trata como unidades del activo base (no está documentado explícitamente que
sea así, pero es la convención más común en DEX de perpetuos con order book)
y se convierte a USD multiplicando por `mark_price`. Si al desplegar los
números de OI salen claramente desproporcionados, es la primera sospecha a
revisar — puede que Paradex ya lo dé directamente en USD.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

MARKETS_SUMMARY_URL = "https://api.prod.paradex.trade/v1/markets/summary"

# Paradex liquida funding cada 8h ("Funding Period: 8h" en la documentación
# de riesgo — docs.paradex.trade/risk/funding-mechanism).
INTERVAL_HOURS = 8


class ParadexConnector:
    name = "paradex"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        # Igual que en el resto de conectores: la excepción sube sin
        # tragársela, para que el motivo real del fallo se pueda enseñar.
        resp = self._session.get(
            MARKETS_SUMMARY_URL, params={"market": "ALL"}, timeout=self._timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("results", [])

        out: list[FundingRate] = []
        for row in rows:
            raw_symbol = row.get("symbol")  # ej. "BTC-USD-PERP"
            rate = row.get("funding_rate")
            if raw_symbol is None or rate is None:
                continue

            # Solo nos interesan los perpetuos (Paradex también lista
            # opciones bajo el mismo endpoint, con símbolos que no siguen
            # este patrón "-PERP").
            if not raw_symbol.endswith("-PERP"):
                continue
            symbol = raw_symbol.split("-")[0]

            mark_price_raw = row.get("mark_price")
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
                    exchange="paradex",
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


def paradex() -> ParadexConnector:
    return ParadexConnector()
