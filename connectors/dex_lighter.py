"""
Conector para Lighter (zkLighter) — DEX de perpetuos sobre zkSync.

Lighter no está en ccxt, así que hablamos directo con su API pública. A
diferencia de Hyperliquid (que trae rate + mark price + OI en una sola
llamada), aquí hacen falta DOS llamadas que hay que cruzar por `market_id`:

  1. GET /api/v1/funding-rates    -> tasa de funding por mercado
  2. GET /api/v1/orderBookDetails -> mark price + open interest por mercado

Docs: https://apidocs.lighter.xyz/reference/funding-rates
      https://apidocs.lighter.xyz/reference/orderbookdetails
      (el esquema de respuesta no se ve en la documentación interactiva —
      se sacó del SDK oficial: https://github.com/elliottech/lighter-python)

Nota sobre unidades: `open_interest` en orderBookDetails viene en unidades
del activo base (ej. cuántos BTC de OI), no en USD — igual que Hyperliquid,
hace falta multiplicar por el mark price para tener el USD.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://mainnet.zklighter.elliot.ai"
FUNDING_RATES_URL = f"{BASE_URL}/api/v1/funding-rates"
ORDER_BOOK_DETAILS_URL = f"{BASE_URL}/api/v1/orderBookDetails"

# Lighter liquida funding cada hora (documentado en docs.lighter.xyz/trading/funding).
INTERVAL_HOURS = 1


class LighterConnector:
    name = "lighter"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        # Dejamos que la excepción suba sin tragárnosla — igual que en el
        # resto de conectores — para que core/data_service.py pueda enseñar
        # el motivo real si Lighter falla, en vez de un "0 pares" mudo.
        funding_resp = self._session.get(FUNDING_RATES_URL, timeout=self._timeout)
        funding_resp.raise_for_status()
        funding_payload = funding_resp.json()
        funding_rows = funding_payload.get("funding_rates", [])

        depth_resp = self._session.get(
            ORDER_BOOK_DETAILS_URL, params={"filter": "perp"}, timeout=self._timeout
        )
        depth_resp.raise_for_status()
        depth_payload = depth_resp.json()
        depth_rows = depth_payload.get("order_book_details", [])

        # market_id -> (mark_price, open_interest en unidades base)
        depth_by_market: dict[int, tuple[float | None, float | None]] = {}
        for row in depth_rows:
            market_id = row.get("market_id")
            if market_id is None:
                continue
            mark_price_raw = row.get("mark_price")
            mark_price = float(mark_price_raw) if mark_price_raw is not None else None
            open_interest = row.get("open_interest")
            depth_by_market[market_id] = (mark_price, open_interest)

        # El endpoint puede traer, además de la tasa propia de Lighter, tasas
        # de referencia de otros exchanges bajo el mismo market_id (para eso
        # existe el campo `exchange`). Agrupamos primero por mercado y nos
        # quedamos con la fila marcada "lighter"; si ese mercado solo tiene
        # una fila sin ambigüedad la usamos tal cual, y si hay varias filas
        # sin ninguna marcada "lighter" la saltamos en vez de arriesgarnos a
        # etiquetar como propia la tasa de otro exchange.
        rows_by_market: dict[int, list[dict]] = {}
        for row in funding_rows:
            market_id = row.get("market_id")
            if market_id is None:
                continue
            rows_by_market.setdefault(market_id, []).append(row)

        out: list[FundingRate] = []
        for market_id, rows in rows_by_market.items():
            own_row = next((r for r in rows if str(r.get("exchange", "")).lower() == "lighter"), None)
            if own_row is None:
                if len(rows) == 1:
                    own_row = rows[0]
                else:
                    logger.debug(
                        "Lighter: %d filas de funding para market_id=%s y ninguna marcada 'lighter' — se descarta",
                        len(rows), market_id,
                    )
                    continue

            symbol = own_row.get("symbol")
            rate = own_row.get("rate")
            if symbol is None or rate is None:
                continue

            mark_price, open_interest_base = depth_by_market.get(market_id, (None, None))
            oi_usd = None
            if open_interest_base is not None and mark_price is not None:
                try:
                    oi_usd = float(open_interest_base) * float(mark_price)
                except (TypeError, ValueError):
                    oi_usd = None

            out.append(
                FundingRate(
                    exchange="lighter",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=str(market_id),
                    funding_rate=float(rate),
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=oi_usd,
                )
            )

        return out


def lighter() -> LighterConnector:
    return LighterConnector()
