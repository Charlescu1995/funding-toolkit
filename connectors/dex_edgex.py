"""
Conector para edgeX — DEX de perpetuos con order book propio (no está en ccxt).

Un solo endpoint bulk trae funding rate + mark price + open interest de todos
los contratos a la vez (parecido a Hyperliquid/Extended), así que no hace
falta cruzar dos llamadas como con Lighter:

    GET /api/v2/public/quote/getTicker   (sin `contractId` -> todos los contratos)

Para el intervalo de liquidación hace falta una segunda llamada, porque NO
viene en el ticker: es una propiedad estática de cada contrato, expuesta en
el endpoint de metadata:

    GET /api/v2/public/meta/getMetaData  -> data.contractList[].fundingRateIntervalMin

Docs: https://edgex-1.gitbook.io/edgeX-documentation/api-v2/public-api/quote-api
      https://edgex-1.gitbook.io/edgeX-documentation/api-v2/public-api/metadata-api
      https://edgex-1.gitbook.io/edgeX-documentation/api-v2/public-api/funding-api
      (el esquema de respuesta no se ve en la documentación interactiva —
      se pidió campo a campo vía fetch dirigido, igual que con Lighter)

Nota sobre unidades: `openInterest` en el ticker no documenta explícitamente
si es en unidades del activo base o ya en USD. Se trata como unidades base
(igual que Hyperliquid/Lighter/Paradex/Pacifica) y se convierte a USD
multiplicando por el mark price — sin verificar aún contra la red real
(este entorno de desarrollo no tiene salida a internet hacia exchanges).
Si al desplegar el OI de edgeX sale desproporcionado, esta es la primera
sospechosa, igual que ya pasó y se corrigió con Lighter.

Nota sobre el intervalo: `fundingRateIntervalMin` es una propiedad POR
CONTRATO (la documentación da un ejemplo de 240 min = 4h), no un valor fijo
para todo el exchange como Extended o Pacifica. Por eso este conector cruza
metadata + ticker por `contractId` en vez de asumir un único INTERVAL_HOURS
global.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://edgex-prod-v2.edgex.exchange"
METADATA_URL = f"{BASE_URL}/api/v2/public/meta/getMetaData"
TICKER_URL = f"{BASE_URL}/api/v2/public/quote/getTicker"

# Si por lo que sea un contrato no trae fundingRateIntervalMin en metadata,
# usamos 1h como fallback conservador (mejor infravalorar el APR que
# multiplicar de más, que es el error que ya cometimos una vez con Lighter).
FALLBACK_INTERVAL_HOURS = 1.0


class EdgeXConnector:
    name = "edgex"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        meta_resp = self._session.get(METADATA_URL, timeout=self._timeout)
        meta_resp.raise_for_status()
        meta_payload = meta_resp.json()
        contract_list = (meta_payload.get("data") or {}).get("contractList", [])

        # contractId -> intervalo de liquidación en horas.
        interval_by_contract: dict[str, float] = {}
        for contract in contract_list:
            contract_id = contract.get("contractId")
            interval_min = contract.get("fundingRateIntervalMin")
            if contract_id is None or interval_min is None:
                continue
            try:
                interval_by_contract[contract_id] = float(interval_min) / 60.0
            except (TypeError, ValueError):
                continue

        ticker_resp = self._session.get(TICKER_URL, timeout=self._timeout)
        ticker_resp.raise_for_status()
        ticker_payload = ticker_resp.json()
        rows = ticker_payload.get("data", [])

        out: list[FundingRate] = []
        for row in rows:
            contract_id = row.get("contractId")
            raw_symbol = row.get("contractName")  # ej. "BTCUSDT"
            rate = row.get("fundingRate")
            if raw_symbol is None or rate is None:
                continue

            symbol = raw_symbol[:-4] if raw_symbol.endswith("USDT") else raw_symbol

            mark_price_raw = row.get("markPrice")
            mark_price = float(mark_price_raw) if mark_price_raw is not None else None

            oi_raw = row.get("openInterest")
            oi_usd = None
            if oi_raw is not None and mark_price is not None:
                try:
                    oi_usd = float(oi_raw) * mark_price
                except (TypeError, ValueError):
                    oi_usd = None

            interval_hours = interval_by_contract.get(contract_id, FALLBACK_INTERVAL_HOURS)

            out.append(
                FundingRate(
                    exchange="edgex",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=raw_symbol,
                    funding_rate=float(rate),
                    interval_hours=interval_hours,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=oi_usd,
                )
            )

        return out


def edgex() -> EdgeXConnector:
    return EdgeXConnector()
