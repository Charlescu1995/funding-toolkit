"""
Conector para KuCoin Futures — CEX de derivados.

No soportado por ccxt: `ccxt.kucoinfutures.fetch_funding_rates()` lanza
`NotSupported` (comprobado leyendo el código fuente de ccxt, no solo la
bandera `has[...]`) — por eso este exchange necesita conector propio, igual
que ya pasó con los DEX que no estaban en ccxt (edgeX, GRVT...).

Un único endpoint público trae TODO de golpe: metadata del contrato, funding
rate vigente, mark price, index price y open interest — a diferencia de la
mayoría de los otros CEX (que necesitan ccxt precisamente porque no existe
un endpoint tan completo), aquí ni siquiera hace falta una segunda llamada
ni pool de hilos.

    GET https://api-futures.kucoin.com/api/v1/contracts/active

Docs: https://www.kucoin.com/docs/rest/futures-trading/market-data/get-all-tickers

Nota sobre el entorno de investigación: este endpoint concreto está en la
lista de hosts bloqueados por la política de red de ESTE sandbox de
desarrollo (`curl` directo da 403 del propio proxy de egress, no del
exchange — confirmado vía `/__agentproxy/status`), así que toda la
investigación de este conector se hizo con WebFetch en vez de con peticiones
directas, pero SIGUE siendo contra la API en vivo real, no solo contra
documentación — no debería fallar por esto en Streamlit Cloud, que no tiene
esta restricción.

--- Confirmado en vivo (WebFetch directo) ---

Ejemplo real, contrato XBTUSDTM (BTC-USDT perpetuo):

    {
      "symbol": "XBTUSDTM", "baseCurrency": "XBT", "quoteCurrency": "USDT",
      "settleCurrency": "USDT",
      "fundingFeeRate": 3.6E-5, "fundingRateGranularity": 28800000,
      "markPrice": 77925.3, "indexPrice": 77954.35,
      "openInterest": "10419591", "multiplier": 0.001,
      "status": "Open", ...
    }

--- Nota sobre "XBT" en vez de "BTC" (bug real de cruce de símbolos, no una
    curiosidad) ---

KuCoin Futures usa la nomenclatura heredada "XBT" para Bitcoin en
`baseCurrency` (igual que Kraken) — ningún otro exchange de este proyecto usa
ese nombre para BTC. Sin corregirlo, KuCoin aparecería con un activo "XBT"
separado de "BTC" en el resto de exchanges y nunca cruzaría en el ranking.
Se normaliza con un diccionario de alias explícito (`_SYMBOL_ALIASES`), no
con una heurística — es el único caso conocido en todo el universo de
contratos de KuCoin, no hace falta nada más genérico.

--- Nota sobre `fundingFeeRate` (CONFIRMADO en vivo, tasa cruda del
    intervalo) ---

fundingFeeRate=3.6E-5 con fundingRateGranularity=28800000ms (=8h) da un APR
de ≈3.94% — una cifra normal, confirma que es la tasa cruda del intervalo de
8h, no algo ya anualizado.

    interval_hours = fundingRateGranularity / 1000 / 3600

--- Nota sobre `openInterest` (CONFIRMADO en vivo, contrastado con mark
    price y multiplier) ---

`openInterest` viene en NÚMERO DE CONTRATOS (string), no en el activo base ni
en USD directamente. Cada contrato de KuCoin representa `multiplier`
unidades del activo base (ej. 0.001 XBT/contrato para XBTUSDTM) — aplicando
la conversión da un notional de orden de magnitud plausible (~$812M para
BTC, coherente con un exchange grande):

    open_interest_usd = float(openInterest) * multiplier * markPrice

--- Nota sobre `status` (solo un valor visto en vivo, mismo criterio de
    precaución que el resto del proyecto) ---

Solo se ha observado "Open" en el contrato de mayor volumen consultado — se
acepta únicamente ese valor exacto, igual que con `orderBookState` en
Backpack o `trading_status` en Nado, por si existen otros estados (pausado,
deslistado...) no vistos todavía en este único ejemplo.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://api-futures.kucoin.com"
CONTRACTS_URL = f"{BASE_URL}/api/v1/contracts/active"

MS_PER_HOUR = 1000.0 * 3600.0

# Ver docstring: único alias conocido en todo el universo de contratos de
# KuCoin Futures — no hace falta nada más genérico que esto.
_SYMBOL_ALIASES = {"XBT": "BTC"}


class KucoinConnector:
    name = "kucoinfutures"
    venue_type = VenueType.CEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(CONTRACTS_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()

        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(
                "kucoinfutures: /api/v1/contracts/active no devolvió una lista en 'data' — "
                f"claves de nivel superior recibidas: "
                f"{list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}"
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for row in rows:
            if not isinstance(row, dict):
                skipped[str(row)] = "fila no es un objeto (formato inesperado)"
                continue

            raw_symbol = row.get("symbol")
            if raw_symbol is None:
                continue

            # Ver docstring: solo se acepta el estado confirmado en vivo.
            if row.get("status") != "Open":
                continue

            rate_raw = row.get("fundingFeeRate")
            granularity_raw = row.get("fundingRateGranularity")
            if rate_raw is None or granularity_raw is None:
                skipped[raw_symbol] = "faltan campos (fundingFeeRate/fundingRateGranularity)"
                continue

            try:
                rate = float(rate_raw)
                interval_hours = float(granularity_raw) / MS_PER_HOUR
            except (TypeError, ValueError) as exc:
                skipped[raw_symbol] = f"valor no numérico: {exc}"
                continue

            if interval_hours <= 0:
                skipped[raw_symbol] = f"fundingRateGranularity inválido ({granularity_raw})"
                continue

            mark_price_raw = row.get("markPrice")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            oi_contracts_raw = row.get("openInterest")
            multiplier_raw = row.get("multiplier")
            open_interest_usd = None
            if oi_contracts_raw is not None and multiplier_raw is not None and mark_price is not None:
                try:
                    # Ver docstring: openInterest viene en Nº de contratos.
                    open_interest_usd = float(oi_contracts_raw) * float(multiplier_raw) * mark_price
                except (TypeError, ValueError):
                    open_interest_usd = None

            base_currency = row.get("baseCurrency") or raw_symbol
            symbol = _SYMBOL_ALIASES.get(base_currency, base_currency)

            out.append(
                FundingRate(
                    exchange="kucoinfutures",
                    venue_type=VenueType.CEX,
                    symbol=symbol,
                    raw_symbol=raw_symbol,
                    funding_rate=rate,
                    interval_hours=interval_hours,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=open_interest_usd,
                )
            )

        if not out:
            sample = dict(list(skipped.items())[:3])
            raise RuntimeError(
                f"kucoinfutures: {len(skipped)}/{len(rows)} contratos se saltaron (o no "
                f"estaban 'Open') y no quedó ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "kucoinfutures: %d/%d contratos se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(rows),
                skipped,
            )

        return out


def kucoin() -> KucoinConnector:
    return KucoinConnector()
