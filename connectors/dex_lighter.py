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

Nota importante sobre /funding-rates: NO es un endpoint solo de Lighter — es
un endpoint de comparación que trae, para un universo amplio de símbolos, la
tasa de varios exchanges de referencia (binance, bybit, hyperliquid) junto a
la propia de Lighter, y ese universo incluye símbolos que Lighter ni
siquiera lista (comprobado en vivo: aparecen tickers de acciones como "GME",
"ORCL", "TTWO" con exchange="binance", sin fila "lighter" correspondiente).
Por eso este conector es estricto: solo se queda con la fila cuyo `exchange`
sea exactamente "lighter"; si un mercado no tiene esa fila, se descarta en
vez de arriesgarse a etiquetar la tasa de otro exchange como si fuera propia
de Lighter (bug real, encontrado y corregido tras probar contra la API en vivo).

Nota sobre el periodo del `rate`: aunque Lighter liquida el funding cada
hora, el valor que devuelve /funding-rates está normalizado a un
equivalente de 8h, no a 1h (tiene sentido: es un endpoint de comparación
entre varios exchanges con distinta frecuencia de liquidación, así que los
normalizan a un periodo común). Verificado en vivo contra la propia UI de
Lighter: para BTC la UI mostraba "1HR FUNDING: +0.0012%" mientras que
/funding-rates devolvía 0.0096% — exactamente 8 veces más. Ver
INTERVAL_HOURS más abajo.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://mainnet.zklighter.elliot.ai"
FUNDING_RATES_URL = f"{BASE_URL}/api/v1/funding-rates"
ORDER_BOOK_DETAILS_URL = f"{BASE_URL}/api/v1/orderBookDetails"

# OJO: Lighter LIQUIDA el funding cada hora, pero el campo `rate` que devuelve
# /funding-rates NO es la tasa por hora — es la tasa normalizada a un
# equivalente de 8h. Tiene sentido porque este endpoint compara varios
# exchanges a la vez (binance, bybit, hyperliquid, lighter) y cada uno
# liquida con su propia frecuencia, así que normalizan todos a un periodo
# común para poder comparar. Verificado en vivo comparando contra la propia
# interfaz de Lighter: la UI mostraba "1HR FUNDING: +0.0012%" para BTC,
# mientras que /funding-rates devolvía 0.0096% (exactamente 8 veces más) —
# si se tratara ese 0.0096% como tasa de 1h (como hacía la versión anterior
# de este conector) el APR salía en 84.1% en vez del 10.5% real. Por eso
# INTERVAL_HOURS = 8 aquí, aunque la liquidación real sea cada hora.
INTERVAL_HOURS = 8


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

        # /api/v1/funding-rates es, en realidad, un endpoint de COMPARACIÓN:
        # trae, para un universo amplio de símbolos, la tasa de varios
        # exchanges de referencia (binance, bybit, hyperliquid) junto a la
        # propia de Lighter — y ese universo incluye símbolos que Lighter NI
        # SIQUIERA LISTA (se ha visto en vivo "GME", "ORCL", "TTWO",
        # "SAMSUNG"... tickers de acciones, no perpetuos de Lighter). Antes
        # este conector caía a "si solo hay una fila, es la propia" cuando no
        # encontraba una fila marcada "lighter" — y esa fila única resultó
        # ser, en la práctica, la referencia de OTRO exchange para un mercado
        # que Lighter no soporta, mal etiquetada como si fuera de Lighter.
        # Ahora es estricto: si no hay fila marcada "lighter" para ese
        # mercado, se descarta sin más — mejor no traer ese símbolo que
        # traerlo con la tasa de otro exchange puesta a su nombre.
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
