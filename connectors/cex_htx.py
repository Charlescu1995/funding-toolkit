"""
Conector para HTX (antes Huobi) — CEX de derivados, swaps USDT-margined
("linear-swap").

No soportado por ccxt: `ccxt.htx.fetch_funding_rates()` lanza `NotSupported`
(comprobado leyendo el código fuente de ccxt).

Cuatro endpoints públicos, todos bulk (sin pool de hilos), cruzados por
`contract_code` (ej. "BTC-USDT"), presente en los cuatro:

    GET https://api.hbdm.com/linear-swap-api/v1/swap_contract_info
        -> metadata: contract_size, contract_status, settlement_period (horas)
    GET https://api.hbdm.com/linear-swap-api/v1/swap_batch_funding_rate
        -> funding_rate vigente, por contrato
    GET https://api.hbdm.com/linear-swap-api/v1/swap_open_interest
        -> open interest YA EN USD (campo "value"), por contrato
    GET https://api.hbdm.com/linear-swap-ex/market/detail/batch_merged
        -> "close" (último precio negociado), usado como proxy de mark price

Todo comprobado en vivo (WebFetch directo — ver nota de entorno en
connectors/cex_kucoin.py: estos hosts están bloqueados por la política de
red de ESTE sandbox de desarrollo, así que la investigación se hizo con
WebFetch en vez de curl directo, pero contra la API real, no solo contra
documentación).

--- Nota sobre la clave de nivel superior: NO es la misma en los cuatro
    endpoints (confirmado en vivo, real inconsistencia de la API) ---

`swap_contract_info`, `swap_batch_funding_rate` y `swap_open_interest`
envuelven la lista bajo `"data"`. El endpoint de ticker
(`/market/detail/batch_merged`) NO — la envuelve bajo `"ticks"` en vez de
`"data"` (confirmado explícitamente en vivo, pidiendo las claves de primer
nivel: solo trae `"status"` y `"ticks"`). Si en algún momento HTX cambia esto
y `batch_merged` empieza a devolver `[]`/None con la clave `"ticks"`, es la
primera sospecha a revisar (con fallback ya puesto a `"data"` por si acaso).

--- Nota sobre `settlement_period` (CONFIRMADO en vivo — SÍ hay campo
    explícito, no hace falta asumir 8h fijo como con el resto de CEX de ccxt
    en cex_ccxt.py) ---

`swap_contract_info` trae `settlement_period` como STRING en horas (ej. "8"
para BTC-USDT) — se usa directamente:

    interval_hours = float(settlement_period)

--- Nota sobre `funding_rate` (CONFIRMADO en vivo, tasa cruda del intervalo) ---

BTC-USDT en vivo: funding_rate≈0.0001 con settlement_period="8" -> APR≈10.95%,
cifra normal — confirma que es la tasa cruda del intervalo, no anualizada.

--- Nota sobre `open_interest` — el campo "value" YA viene en USD, al
    contrario que KuCoin/MEXC en este mismo lote (ver connectors/cex_kucoin.py
    y connectors/cex_mexc.py, que sí vienen en Nº de contratos) ---

`swap_open_interest` trae, por contrato, `volume` (Nº de contratos), `amount`
(unidades del activo base = volume × contract_size) y `value`. Confirmado
por consistencia interna en vivo (ejemplo real, QNTX-USDT): volume=15154,
amount=151.54 (coherente con contract_size≈0.01), value=7480.0144 ->
value/amount ≈ 49.36, un precio por unidad plausible para ese token. Por
tanto `value` ya es el notional en USD y se usa DIRECTAMENTE, sin multiplicar
por contract_size ni por precio.

    open_interest_usd = value   (directo, sin conversión)

--- Nota sobre el mark price: NO existe un campo dedicado, se usa "close"
    como proxy (hueco conocido y documentado, no crítico) ---

No se encontró, tras comprobarlo en vivo, ningún endpoint público de HTX que
traiga un "mark price" con ese nombre explícito para swaps linear (se probó
`/linear-swap-api/v1/swap_batch_mark_price`, que no existe — 404 real,
confirmado). El endpoint de ticker bulk (`batch_merged`) sí trae `close`
(último precio negociado), que se usa como aproximación razonable — no es
técnicamente lo mismo que un mark price (que normalmente incluye el premium
del book), pero es la mejor fuente bulk disponible. Como el open interest en
USD de este conector NO depende de este precio (ver nota de arriba), este
hueco solo afecta al campo informativo `mark_price`, no al cálculo de OI.

--- Nota sobre `contract_status` (solo un valor visto en vivo, mismo
    criterio de precaución que el resto del proyecto) ---

Solo se ha observado `1` en el contrato de mayor volumen consultado (BTC-USDT)
— se acepta únicamente ese valor exacto.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://api.hbdm.com"
CONTRACT_INFO_URL = f"{BASE_URL}/linear-swap-api/v1/swap_contract_info"
FUNDING_RATE_URL = f"{BASE_URL}/linear-swap-api/v1/swap_batch_funding_rate"
OPEN_INTEREST_URL = f"{BASE_URL}/linear-swap-api/v1/swap_open_interest"
TICKER_URL = f"{BASE_URL}/linear-swap-ex/market/detail/batch_merged"


class HtxConnector:
    name = "htx"
    venue_type = VenueType.CEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        info_resp = self._session.get(CONTRACT_INFO_URL, timeout=self._timeout)
        info_resp.raise_for_status()
        info_payload = info_resp.json()
        info_rows = info_payload.get("data")
        if not isinstance(info_rows, list) or not info_rows:
            raise RuntimeError(
                "htx: swap_contract_info no devolvió una lista en 'data' — "
                f"claves de nivel superior: "
                f"{list(info_payload.keys()) if isinstance(info_payload, dict) else type(info_payload).__name__}"
            )

        # contract_code -> (base_symbol, interval_hours, operable)
        info_by_code: dict[str, tuple[str, float | None, bool]] = {}
        for row in info_rows:
            if not isinstance(row, dict):
                continue
            code = row.get("contract_code")
            if code is None:
                continue
            base_symbol = row.get("symbol") or code.split("-")[0]
            period_raw = row.get("settlement_period")
            try:
                interval_hours = float(period_raw) if period_raw is not None else None
            except (TypeError, ValueError):
                interval_hours = None
            # Ver docstring: solo se acepta el estado confirmado en vivo.
            operable = row.get("contract_status") == 1
            info_by_code[code] = (base_symbol, interval_hours, operable)

        funding_resp = self._session.get(FUNDING_RATE_URL, timeout=self._timeout)
        funding_resp.raise_for_status()
        funding_payload = funding_resp.json()
        funding_rows = funding_payload.get("data")
        if not isinstance(funding_rows, list) or not funding_rows:
            raise RuntimeError(
                "htx: swap_batch_funding_rate no devolvió una lista en 'data' — "
                f"claves de nivel superior: "
                f"{list(funding_payload.keys()) if isinstance(funding_payload, dict) else type(funding_payload).__name__}"
            )

        oi_resp = self._session.get(OPEN_INTEREST_URL, timeout=self._timeout)
        oi_resp.raise_for_status()
        oi_payload = oi_resp.json()
        oi_rows = oi_payload.get("data") or []
        oi_usd_by_code = {
            row["contract_code"]: row.get("value")
            for row in oi_rows
            if isinstance(row, dict) and row.get("contract_code")
        }

        ticker_resp = self._session.get(TICKER_URL, timeout=self._timeout)
        ticker_resp.raise_for_status()
        ticker_payload = ticker_resp.json()
        # Ver docstring: este endpoint concreto envuelve bajo "ticks", no
        # "data" como los otros tres — con fallback a "data" por si cambia.
        ticker_rows = ticker_payload.get("ticks")
        if ticker_rows is None:
            ticker_rows = ticker_payload.get("data") or []
        close_by_code = {
            row["contract_code"]: row.get("close")
            for row in ticker_rows
            if isinstance(row, dict) and row.get("contract_code")
        }

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for row in funding_rows:
            if not isinstance(row, dict):
                continue
            code = row.get("contract_code")
            if code is None:
                continue

            base_symbol, interval_hours, operable = info_by_code.get(code, (None, None, False))
            if not operable or base_symbol is None:
                continue

            rate_raw = row.get("funding_rate")
            if rate_raw is None or interval_hours is None or interval_hours <= 0:
                skipped[code] = "sin funding_rate o settlement_period inválido/ausente"
                continue

            try:
                rate = float(rate_raw)
            except (TypeError, ValueError) as exc:
                skipped[code] = f"funding_rate no numérico: {exc}"
                continue

            mark_price_raw = close_by_code.get(code)
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            oi_usd_raw = oi_usd_by_code.get(code)
            open_interest_usd = None
            if oi_usd_raw is not None:
                try:
                    # Ver docstring: "value" ya viene en USD, sin conversión.
                    open_interest_usd = float(oi_usd_raw)
                except (TypeError, ValueError):
                    open_interest_usd = None

            out.append(
                FundingRate(
                    exchange="htx",
                    venue_type=VenueType.CEX,
                    symbol=base_symbol,
                    raw_symbol=code,
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
                f"htx: {len(skipped)} contratos con datos incompletos de {len(funding_rows)} "
                f"totales, y ningún par operable/válido quedó — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "htx: %d/%d contratos se saltaron por datos incompletos (se omiten, no tiran el resto): %s",
                len(skipped),
                len(funding_rows),
                skipped,
            )

        return out


def htx() -> HtxConnector:
    return HtxConnector()
