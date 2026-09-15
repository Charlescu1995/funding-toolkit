"""
Conector para ApeX Omni — DEX de perpetuos (producto actual de ApeX
Protocol; el antiguo "ApeX Pro" sobre StarkEx se discontinuó, ver más abajo).

--- Bug de investigación: hay que usar ApeX OMNI, no ApeX Pro ---

ApeX tuvo dos productos: "ApeX Pro" (StarkEx) y "ApeX Omni" (chain propia).
Pro se discontinuó (comunicado oficial "ApeX Pro Sunset", ~marzo 2025) — su
API en vivo (`pro.apex.exchange`) ya devuelve un error interno genérico en
vez de datos reales. La documentación de ambos productos ahora vive bajo el
mismo dominio de docs, lo cual puede confundir. Este conector usa
exclusivamente ApeX Omni:

    Base URL: https://omni.apex.exchange/api/v3

--- Nota sobre varios paths documentados que NO funcionan en vivo ---

Se comprobó en vivo (no solo se leyó la doc) y varios endpoints
"documentados" dan 404 reales:

    /v3/all-config             -> 404 (el real es /v3/config)
    /v3/funding-rate-history   -> 404 (el real es /v3/history-funding)
    /v3/all-tickers, /v3/tickers -> 404 (no existen)

--- Bug de arquitectura real: NO HAY endpoint bulk que funcione ---

A diferencia de todos los demás DEX de este proyecto, ApeX Omni NO tiene
ninguna forma de pedir el funding/precio/OI de TODOS los mercados en una
sola llamada HTTP. Se comprobó en vivo:

    GET /v3/ticker              (sin symbol)   -> {"data": []}
    GET /v3/ticker?symbol=A,B   (varios symbol) -> {"data": []} (no soportado)
    GET /v3/ticker?symbol=BTCUSDT (uno)         -> SÍ funciona, un mercado

`/v3/ticker` EXIGE un `symbol` individual. La única vía realmente bulk es
un WebSocket público (`wss://quote.omni.apex.exchange/realtime_public`),
fuera del alcance de este conector (que sigue el mismo patrón HTTP síncrono
que el resto). Por tanto este conector pide el ticker símbolo a símbolo, en
paralelo con un pool de hilos — mismo patrón que edgeX/GRVT (ver
connectors/dex_edgex.py), a diferencia de Backpack/Nado/Hibachi/Vertex, que
sí tienen (o se asume que tienen) endpoints bulk.

El universo de símbolos candidatos sale de `GET /v3/config`
(`data.contractConfig.tokens[]`, cada uno con un `token` base, ej. "BTC",
"1000PEPE" — el "1000" es el propio multiplicador de contrato de ApeX ya
integrado en el nombre, se usa tal cual, sin intentar recortarlo). Se
construye el símbolo de mercado como `{token}USDT` y se pide el ticker de
cada uno.

--- Nota sobre el estado operable (SIN campo dedicado) ---

Ni `/v3/ticker` ni `/v3/config` traen ningún campo de estado/activo — se
comprobó en vivo que `config.contractConfig.tokens[]` solo trae
`{token, stepSize, iconUrl}`, sin campos de estado ni tick size ni
apalancamiento (pese a que la doc sugiere que debería traer más). El único
proxy real de "¿está operable?" es si `/v3/ticker?symbol=X` devuelve una
fila o `data: []` — eso ya lo resuelve el propio pool de hilos (un símbolo
sin ticker simplemente no aparece en el resultado, no hace falta filtro
aparte).

--- Nota sobre `fundingRate` (CONFIRMADO en vivo) ---

Viene como string, tasa cruda del intervalo (NO anualizada) — confirmado
dos formas: (1) `/v3/history-funding?symbol=BTC-USDT` (ojo: este endpoint
concreto quiere el símbolo CON GUION, a diferencia de ticker/depth/trades,
que lo quieren sin guion — inconsistencia real de la propia API) mostró
liquidaciones espaciadas exactamente 1h; (2) magnitud: BTC en vivo
`"0.0000125"` × 24 × 365 ≈ 10.9% APR anualizado, una cifra normal — si fuera
ya un APY, sería un valor absurdamente pequeño.

    interval_hours = 1.0

--- Nota sobre `openInterest` (CONFIRMADO en vivo, contrastado con mark
    price) ---

Viene en unidades del activo base (del contrato, incluyendo el
multiplicador "1000x" ya integrado en el símbolo cuando aplica) — NO en
USD. Confirmado cruzando contra `markPrice` para BTC (~$122.6M de notional
plausible) y 1000PEPE (~$118.8K de notional plausible), ambos coherentes al
multiplicar `openInterest × markPrice`.

    open_interest_usd = openInterest × markPrice

--- Bug real encontrado y corregido en producción: `data.contractConfig.tokens`
    mezcla perpetuos cripto de verdad con mercados de predicción/apuestas ---

Primer despliegue real: 271 de 370 símbolos candidatos fallaron con 403
Forbidden (no 404, no vacío — 403 real). Mirando la lista de símbolos que
fallaron, casi ninguno es un activo cripto: son mercados de predicción de
ApeX ("BTC_hit_115k_JulyUSDT", "Christopher_Waller_nominated_as_Fed_ChairUSDT",
"Russia_Ukraine_ceasefire_2025USDT") y apuestas deportivas
("Raptors_Win_Against_Hornets_Nov29USDT", "MUN_Win_Against_BOU_Dec15_EPLUSDT")
— todos con guiones bajos como separador de palabras, un patrón que ningún
token cripto real de la lista usa (BTC, ETH, 1000PEPE... siempre sin "_").
`data.contractConfig.tokens` no distingue estos tipos de producto con ningún
campo — los mezcla todos igual. Se añadió un filtro por FORMA (ver
`_looks_like_crypto_token()`): se descarta cualquier token con "_" antes de
pedir su ticker, en vez de gastar una petición HTTP (y un 403 logueado) por
cada uno. Esto no es una asunción arriesgada — los ~300 tokens cripto reales
observados en vivo nunca llevan guion bajo, así que el filtro no puede
descartar un mercado cripto legítimo por error.

Quedan sin filtrar (a propósito) símbolos de acciones/materias primas sin
guion bajo (ej. "AAPLUSDT", "TSLAUSDT", "XAUUSDT") que también dieron 403 en
el primer despliegue — probablemente productos RWA con acceso restringido
por compliance/región, no fantasmas. Se dejan en la lista de candidatos
porque no hay forma fiable de distinguirlos de un cripto real solo por el
nombre; si siguen fallando no rompen nada (se registran como error por
símbolo, sin tirar el resto), solo generan algo de ruido en el log.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://omni.apex.exchange/api/v3"
CONFIG_URL = f"{BASE_URL}/config"
TICKER_URL = f"{BASE_URL}/ticker"

INTERVAL_HOURS = 1.0
MAX_WORKERS = 20

QUOTE_SUFFIX = "USDT"


def _looks_like_crypto_token(token: str) -> bool:
    """
    Ver docstring del módulo (bug real de producción): descarta mercados de
    predicción/apuestas deportivas por FORMA — todos usan "_" como
    separador de palabras, algo que ningún token cripto real de ApeX trae.
    """
    return "_" not in token


class ApexConnector:
    name = "apex"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def _fetch_ticker(self, market_symbol: str) -> dict | None:
        resp = self._session.get(TICKER_URL, params={"symbol": market_symbol}, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data") or []
        return rows[0] if rows else None

    def fetch_funding_rates(self) -> list[FundingRate]:
        config_resp = self._session.get(CONFIG_URL, timeout=self._timeout)
        config_resp.raise_for_status()
        config_payload = config_resp.json()

        tokens = ((config_payload.get("data") or {}).get("contractConfig") or {}).get("tokens") or []

        base_symbols = [
            t.get("token")
            for t in tokens
            if isinstance(t, dict) and t.get("token") and _looks_like_crypto_token(t["token"])
        ]

        if not base_symbols:
            raise RuntimeError(
                "apex: /v3/config no devolvió ningún token en "
                "data.contractConfig.tokens — probablemente cambió la forma de la "
                f"respuesta (claves de nivel superior: {list(config_payload.keys())})"
            )

        market_symbols = [f"{base}{QUOTE_SUFFIX}" for base in base_symbols]

        out: list[FundingRate] = []
        errors: dict[str, str] = {}
        no_ticker = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            future_to_symbol = {
                pool.submit(self._fetch_ticker, market_symbol): (base, market_symbol)
                for base, market_symbol in zip(base_symbols, market_symbols)
            }
            for future in as_completed(future_to_symbol):
                base_symbol, market_symbol = future_to_symbol[future]
                try:
                    row = future.result()
                except Exception as exc:  # noqa: BLE001 — un símbolo suelto no debe tirar todo el conector
                    errors[market_symbol] = f"{type(exc).__name__}: {exc}"
                    continue

                if row is None:
                    # Ver docstring: sin ticker == no operable, no es un error.
                    no_ticker += 1
                    continue

                rate_raw = row.get("fundingRate")
                mark_price_raw = row.get("markPrice")
                oi_raw = row.get("openInterest")

                if rate_raw is None:
                    errors[market_symbol] = "sin fundingRate en la respuesta"
                    continue

                try:
                    rate = float(rate_raw)
                except (TypeError, ValueError) as exc:
                    errors[market_symbol] = f"funding rate no numérico: {exc}"
                    continue

                mark_price = None
                if mark_price_raw is not None:
                    try:
                        mark_price = float(mark_price_raw)
                    except (TypeError, ValueError):
                        mark_price = None

                open_interest_usd = None
                if oi_raw is not None and mark_price is not None:
                    try:
                        # Ver docstring: confirmado en vivo, viene en activo base.
                        open_interest_usd = float(oi_raw) * mark_price
                    except (TypeError, ValueError):
                        open_interest_usd = None

                out.append(
                    FundingRate(
                        exchange="apex",
                        venue_type=VenueType.DEX,
                        symbol=base_symbol,
                        raw_symbol=market_symbol,
                        funding_rate=rate,
                        interval_hours=INTERVAL_HOURS,
                        mark_price=mark_price,
                        next_funding_time=None,
                        open_interest_usd=open_interest_usd,
                    )
                )

        if not out:
            sample = dict(list(errors.items())[:3])
            raise RuntimeError(
                f"apex: {len(errors)}/{len(market_symbols)} símbolos fallaron y "
                f"{no_ticker} no tenían ticker operable — no quedó ningún par válido. "
                f"Muestra de errores: {sample}"
            )
        elif errors:
            logger.warning(
                "apex: %d/%d símbolos fallaron al pedir su ticker (se omiten, no tiran "
                "el resto; además %d sin ticker operable, normal): %s",
                len(errors),
                len(market_symbols),
                no_ticker,
                errors,
            )

        return out


def apex() -> ApexConnector:
    return ApexConnector()
