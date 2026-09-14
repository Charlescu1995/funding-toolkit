"""
Conector para Backpack Exchange — DEX de perpetuos (Solana, orden central book).

Tres endpoints públicos, los tres en bulk (sin pool de hilos, igual que
Variational/RiseX y a diferencia de edgeX/GRVT):

    GET /api/v1/markets      -> metadata de TODOS los mercados (spot + perp mezclados)
    GET /api/v1/markPrices   -> mark price, index price y funding rate vigente, por símbolo
    GET /api/v1/openInterest -> open interest, por símbolo

Todo verificado contra la API en vivo (no solo contra la documentación).

--- Nota sobre `marketType` (CONFIRMADO en vivo) ---

`/api/v1/markets` devuelve mercados SPOT y PERP mezclados en el mismo array
(ej. "SOL_USDC" es spot, "SOL_USDC_PERP" es el perpetuo). Se filtra por
`marketType == "PERP"` explícitamente — coger todo sin filtrar duplicaría
símbolos (un SOL spot y un SOL perp) y rompería el cruce con el resto de
exchanges.

--- Nota sobre `fundingInterval` (CONFIRMADO en vivo — la doc sugiere lo
    contrario) ---

Es un campo plano de nivel superior en cada mercado (milisegundos), NO viene
anidado bajo ningún `fundingRateConfig` ni similar. Ejemplo real: BTC_USDC_PERP
trae `"fundingInterval": 3600000` (3.6M ms = 1h). Los mercados spot traen
`fundingInterval: null` (razón de más para filtrar por marketType antes).

    interval_hours = fundingInterval_ms / 1000 / 3600

--- Nota sobre `orderBookState` (CONFIRMADO en vivo, valor real observado:
    "Open") ---

Cada mercado trae un `orderBookState` que indica si el libro está operable
ahora mismo. Solo se ha observado "Open" en los mercados perp de mayor
volumen consultados, pero la propia forma del campo (un string, no un bool)
sugiere que existen otros estados (cerrado, solo-reduce, etc. — exchanges
similares como RiseX y Aster ya nos han enseñado que SIEMPRE hay mercados no
operables mezclados en el listado bulk). Por eso, igual que con RiseX
(`active`) y Aster (`active` vía ccxt), se descarta aquí cualquier mercado
cuyo `orderBookState` no sea exactamente "Open", en vez de asumir que todo lo
que aparece en el listado es operable.

--- Nota sobre `fundingRate` en /markPrices (CONFIRMADO en vivo) ---

Viene como STRING pero es directamente la tasa cruda del intervalo, sin
anualizar — valores en vivo observados del orden de 1e-5 a 1e-4 (ej.
"0.0000125", "-0.0000327768238884436722995162"), coherente con un intervalo
de liquidación de 1h. No hace falta ninguna conversión de escala (a
diferencia de Variational, que si la necesita).

--- Nota sobre `openInterest` (CONFIRMADO en vivo, contrastado contra mark
    price) ---

`/api/v1/openInterest` da un único campo `openInterest` por símbolo, en
UNIDADES DEL ACTIVO BASE (no en USD) — confirmado cruzando el valor contra
`markPrice` de /markPrices para varios símbolos de precio muy distinto entre
sí (un token de ~$90, otro de ~$707, otro de ~$0.025): multiplicar
`openInterest × markPrice` da en los tres casos un notional del mismo orden
de magnitud (~$30-50K), mientras que interpretarlo directamente como USD
daría cifras absurdamente dispares entre mercados con volumen similar. Por
tanto:

    open_interest_usd = openInterest_base × mark_price

--- Nota sobre el símbolo base ---

A diferencia de RiseX (que había que recortar "BTC/USDC" a mano) o Aster,
aquí `/api/v1/markets` ya trae un campo `baseSymbol` limpio (ej. "BTC" para
"BTC_USDC_PERP") — se usa directamente, sin necesidad de heurísticas de
recorte.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://api.backpack.exchange"
MARKETS_URL = f"{BASE_URL}/api/v1/markets"
MARK_PRICES_URL = f"{BASE_URL}/api/v1/markPrices"
OPEN_INTEREST_URL = f"{BASE_URL}/api/v1/openInterest"

MS_PER_HOUR = 1000.0 * 3600.0


class BackpackConnector:
    name = "backpack"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        markets_resp = self._session.get(MARKETS_URL, timeout=self._timeout)
        markets_resp.raise_for_status()
        markets_raw = markets_resp.json()

        if not isinstance(markets_raw, list) or not markets_raw:
            raise RuntimeError(
                "backpack: /api/v1/markets no devolvió una lista de mercados — "
                f"tipo recibido: {type(markets_raw).__name__}"
            )

        # Ver docstring: spot y perp vienen mezclados, se filtra por marketType.
        perp_markets = {
            m["symbol"]: m
            for m in markets_raw
            if isinstance(m, dict) and m.get("marketType") == "PERP" and m.get("symbol")
        }

        if not perp_markets:
            raise RuntimeError(
                "backpack: /api/v1/markets no trajo ningún mercado con marketType == 'PERP' — "
                f"¿cambió el nombre/valor del campo? ({len(markets_raw)} mercados recibidos en total)"
            )

        mark_prices_resp = self._session.get(MARK_PRICES_URL, timeout=self._timeout)
        mark_prices_resp.raise_for_status()
        mark_prices_raw = mark_prices_resp.json()
        mark_prices_by_symbol = {
            row["symbol"]: row for row in mark_prices_raw if isinstance(row, dict) and row.get("symbol")
        }

        oi_resp = self._session.get(OPEN_INTEREST_URL, timeout=self._timeout)
        oi_resp.raise_for_status()
        oi_raw = oi_resp.json()
        oi_by_symbol = {
            row["symbol"]: row.get("openInterest")
            for row in oi_raw
            if isinstance(row, dict) and row.get("symbol")
        }

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for symbol, market in perp_markets.items():
            # Ver docstring: se descarta cualquier mercado que no esté con el
            # libro abierto — igual que el filtro `active`/`status` en
            # RiseX/Aster, para no dejar colar mercados fantasma/cerrados.
            if market.get("orderBookState") != "Open":
                continue

            price_row = mark_prices_by_symbol.get(symbol)
            if price_row is None:
                skipped[symbol] = "sin entrada en /markPrices"
                continue

            rate_raw = price_row.get("fundingRate")
            interval_ms_raw = market.get("fundingInterval")
            if rate_raw is None or interval_ms_raw is None:
                skipped[symbol] = "faltan campos (fundingRate/fundingInterval)"
                continue

            try:
                rate = float(rate_raw)
                interval_hours = float(interval_ms_raw) / MS_PER_HOUR
            except (TypeError, ValueError) as exc:
                skipped[symbol] = f"valor no numérico: {exc}"
                continue

            if interval_hours <= 0:
                skipped[symbol] = f"fundingInterval inválido ({interval_ms_raw})"
                continue

            mark_price_raw = price_row.get("markPrice")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            oi_base_raw = oi_by_symbol.get(symbol)
            open_interest_usd = None
            if oi_base_raw is not None and mark_price is not None:
                try:
                    # Ver docstring: confirmado en vivo, viene en activo base.
                    open_interest_usd = float(oi_base_raw) * mark_price
                except (TypeError, ValueError):
                    open_interest_usd = None

            base_symbol = market.get("baseSymbol") or symbol

            out.append(
                FundingRate(
                    exchange="backpack",
                    venue_type=VenueType.DEX,
                    symbol=base_symbol,
                    raw_symbol=symbol,
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
                f"backpack: {len(skipped)}/{len(perp_markets)} mercados perp se saltaron y no "
                f"quedó ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "backpack: %d/%d mercados perp se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(perp_markets),
                skipped,
            )

        return out


def backpack() -> BackpackConnector:
    return BackpackConnector()
