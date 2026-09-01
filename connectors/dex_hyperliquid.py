"""
Conector para Hyperliquid (DEX de perpetuos).

Hyperliquid no está cubierto por ccxt de forma completa, así que hablamos
directo con su API pública "info" (no requiere autenticación para leer datos
de mercado — solo hará falta autenticación en el Paso 8, para ejecutar).

Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"

# Hyperliquid liquida funding cada hora.
INTERVAL_HOURS = 1


class HyperliquidConnector:
    name = "hyperliquid"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        # Igual que en cex_ccxt.py: dejamos que la excepción suba en vez de
        # tragarla aquí, para que core/data_service.py pueda enseñar el
        # motivo real del fallo en vez de un simple "0 pares".
        resp = self._session.post(
            INFO_URL,
            json={"type": "metaAndAssetCtxs"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        meta, asset_ctxs = resp.json()

        universe = meta.get("universe", [])
        out: list[FundingRate] = []

        for asset, ctx in zip(universe, asset_ctxs):
            symbol = asset.get("name")
            funding = ctx.get("funding")
            if symbol is None or funding is None:
                continue

            mark_price = ctx.get("markPx")
            open_interest = ctx.get("openInterest")
            oi_usd = None
            if open_interest is not None and mark_price is not None:
                try:
                    oi_usd = float(open_interest) * float(mark_price)
                except (TypeError, ValueError):
                    oi_usd = None

            out.append(
                FundingRate(
                    exchange="hyperliquid",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=symbol,
                    funding_rate=float(funding),
                    interval_hours=INTERVAL_HOURS,
                    mark_price=float(mark_price) if mark_price is not None else None,
                    next_funding_time=self._next_hour_utc(),
                    open_interest_usd=oi_usd,
                )
            )

        return out

    @staticmethod
    def _next_hour_utc() -> datetime:
        now = datetime.now(timezone.utc)
        return now.replace(minute=0, second=0, microsecond=0).replace(
            hour=(now.hour + 1) % 24
        )
