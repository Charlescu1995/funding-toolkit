"""
Conector para edgeX — DEX de perpetuos con order book propio (no está en ccxt).

Historial de este conector — importante para no repetir el mismo error dos
veces: la primera versión usaba `GET /api/v2/public/quote/getTicker` sin
`contractId`, que según la documentación interactiva debía traer TODOS los
contratos de golpe. En el primer despliegue real dio 0 pares, sin ningún
error visible. Se investigó pidiendo la URL en vivo directamente (no solo la
documentación) y el endpoint devolvía `"data": []` siempre — probado con y
sin `contractId`, con distintos contratos. En cambio, el endpoint de funding
por contrato SÍ devolvió datos reales y completos en la misma comprobación:

    GET /api/v2/public/funding/getLatestFundingRate?contractId=30000001
    -> {"code":"SUCCESS","data":[{"contractId":"30000001","markPrice":"78528.95...",
        "fundingRate":"0.00005000","fundingRateIntervalMin":"240", ...}]}

Por eso este conector usa ESE endpoint, uno por contrato (como GRVT, con un
pool de hilos en paralelo, ya que tampoco hay aquí una versión bulk que
funcione de verdad). Es más peticiones que el diseño original, pero son
peticiones que se ha comprobado que responden con datos reales.

Flujo:
    1. GET /api/v2/public/meta/getMetaData  -> lista de contratos activos
       (contractId, contractName) — esto si funciona bulk, lo comprobado en
       vivo trajo 26+ contratos con normalidad.
    2. GET /api/v2/public/funding/getLatestFundingRate?contractId=<id>
       -> funding rate, mark price y fundingRateIntervalMin, por contrato.

Docs: https://edgex-1.gitbook.io/edgeX-documentation/api-v2/public-api/metadata-api
      https://edgex-1.gitbook.io/edgeX-documentation/api-v2/public-api/funding-api

Nota sobre el open interest: `getLatestFundingRate` (el endpoint que sí
funciona) NO trae open interest en ningún campo — solo precios y funding.
No se encontró, tras la comprobación en vivo, ningún endpoint de edgeX que
combine funding + OI de forma fiable, así que de momento `open_interest_usd`
queda en `None` para todos los pares de edgeX (se verá como "s/d" en la
interfaz, igual que cuando un exchange no da profundidad). Si más adelante
aparece un endpoint bulk que sí funcione, se puede retomar el diseño
original con OI incluido.

Nota sobre el símbolo: `contractName` viene con el activo de colateral
pegado al final, y en edgeX ese activo puede ser USDT o USDC según el
contrato (comprobado en vivo: "BTCUSDC", no "BTCUSDT") — se recortan ambos
sufijos, no solo USDT como en el resto de conectores.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://edgex-prod-v2.edgex.exchange"
METADATA_URL = f"{BASE_URL}/api/v2/public/meta/getMetaData"
FUNDING_URL = f"{BASE_URL}/api/v2/public/funding/getLatestFundingRate"

# Fallback si algún contrato no trae fundingRateIntervalMin ni en metadata ni
# en la propia respuesta de funding (no debería pasar, visto en vivo que
# ambos lo traen, pero por si acaso — mejor infravalorar el APR que
# multiplicar de más, el error que ya cometimos una vez con Lighter).
FALLBACK_INTERVAL_HOURS = 1.0

MAX_WORKERS = 12

_QUOTE_SUFFIXES = ("USDT", "USDC")


def _strip_quote_suffix(raw_symbol: str) -> str:
    for suffix in _QUOTE_SUFFIXES:
        if raw_symbol.endswith(suffix):
            return raw_symbol[: -len(suffix)]
    return raw_symbol


class EdgeXConnector:
    name = "edgex"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def _fetch_funding(self, contract_id: str) -> dict | None:
        resp = self._session.get(
            FUNDING_URL, params={"contractId": contract_id}, timeout=self._timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", [])
        return rows[0] if rows else None

    def fetch_funding_rates(self) -> list[FundingRate]:
        meta_resp = self._session.get(METADATA_URL, timeout=self._timeout)
        meta_resp.raise_for_status()
        meta_payload = meta_resp.json()
        contract_list = (meta_payload.get("data") or {}).get("contractList", [])

        contracts: list[tuple[str, str]] = []  # (contractId, contractName)
        for contract in contract_list:
            contract_id = contract.get("contractId")
            contract_name = contract.get("contractName")
            if contract_id is None or contract_name is None:
                continue
            if contract.get("enableTrade") is False:
                continue
            contracts.append((contract_id, contract_name))

        if not contracts:
            raise RuntimeError(
                "edgex: getMetaData no devolvió ningún contrato — probablemente cambió "
                "la forma de la respuesta (revisar contra la API en vivo)"
            )

        out: list[FundingRate] = []
        errors: dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            future_to_contract = {
                pool.submit(self._fetch_funding, contract_id): (contract_id, contract_name)
                for contract_id, contract_name in contracts
            }
            for future in as_completed(future_to_contract):
                contract_id, contract_name = future_to_contract[future]
                try:
                    row = future.result()
                except Exception as exc:  # noqa: BLE001 — un contrato suelto no debe tirar todo el conector
                    errors[contract_name] = f"{type(exc).__name__}: {exc}"
                    continue

                if not row:
                    continue

                rate = row.get("fundingRate")
                if rate is None:
                    continue

                mark_price_raw = row.get("markPrice")
                mark_price = float(mark_price_raw) if mark_price_raw is not None else None

                interval_min = row.get("fundingRateIntervalMin")
                interval_hours = (
                    float(interval_min) / 60.0 if interval_min is not None else FALLBACK_INTERVAL_HOURS
                )

                symbol = _strip_quote_suffix(contract_name)

                out.append(
                    FundingRate(
                        exchange="edgex",
                        venue_type=VenueType.DEX,
                        symbol=symbol,
                        raw_symbol=contract_name,
                        funding_rate=float(rate),
                        interval_hours=interval_hours,
                        mark_price=mark_price,
                        next_funding_time=None,
                        open_interest_usd=None,  # ver nota del docstring
                    )
                )

        if not out:
            # Si TODOS los contratos fallaron, no devolvemos silenciosamente
            # una lista vacía — eso es indistinguible de "edgex no tiene
            # nada que decir" cuando en realidad algo se rompió (URL, forma
            # de la respuesta...). Mejor que suba el motivo real.
            sample = dict(list(errors.items())[:3])
            raise RuntimeError(
                f"edgex: {len(errors)}/{len(contracts)} contratos fallaron y no quedó "
                f"ningún par válido — muestra de errores: {sample}"
            )
        elif errors:
            logger.warning(
                "edgex: %d/%d contratos fallaron al pedir su funding (se omiten, no tiran el resto): %s",
                len(errors),
                len(contracts),
                errors,
            )

        return out


def edgex() -> EdgeXConnector:
    return EdgeXConnector()
