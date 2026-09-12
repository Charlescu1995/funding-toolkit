"""
Conector para RiseX — DEX de perpetuos sobre RISE Chain (no está en ccxt).

Un solo endpoint público trae todos los mercados de golpe (símbolo, funding
rate actual, intervalo de funding, mark price, index price, open interest...).
Es el diseño más simple de todos los DEX de este proyecto — ni thread pool
(como GRVT/edgeX) ni fallback de intervalo por adivinanza (como Lighter): el
propio endpoint trae el intervalo explícito en nanosegundos.

--- Nota sobre las dos webs de documentación ---

RiseX tiene documentación repartida en dos dominios distintos:
`docs.risechain.com/docs/risex` (conceptual — cómo funciona el funding, etc.)
y `developer.rise.trade` (referencia real de la API, con OpenAPI spec). Este
conector se basa en la segunda.

Base URL confirmada vía developer.rise.trade/reference/integration.md:
    Mainnet REST: https://api.rise.trade
    (Testnet:     https://api.testnet.rise.trade — no usado aquí)

Endpoint: GET /v1/markets — según la documentación, cacheado 5 minutos en el
servidor salvo que se pida `force_refresh`. No requiere autenticación.

--- Nota sobre `current_funding_rate` vs `funding_rate_8h` ---

Cada mercado trae AMBOS campos. Se usa `current_funding_rate` (la tasa
"vigente" en este momento) junto con el propio `funding_interval` del
mercado, en vez de `funding_rate_8h`, para ser consistente con el resto de
conectores del proyecto (todos usan "la tasa del intervalo que toca ahora
mismo", no una versión ya reescalada a 8h — ese reescalado lo hace
core/normalize.py para todos los exchanges por igual).

La documentación describe `current_funding_rate` como "decimal string, 18
decimales" — interpretado aquí como "hasta 18 decimales de precisión en el
string" (el propio ejemplo del OpenAPI spec, "0.00001234567890123456", ya es
una fracción decimal legible directamente, no un entero estilo wei), NO como
"hay que dividir entre 1e18". A diferencia de GRVT (que sí usa fixed-point
real ÷1e9), aquí no se ha podido confirmar esto contra una respuesta real en
vivo (no se logró un fetch en vivo de /v1/markets con datos reales esta
sesión, solo el ejemplo de la spec) — si los APR de RiseX salen en
producción con un orden de magnitud absurdo (positivo o negativo), esto es
lo primero a revisar.

--- Nota sobre `funding_interval` ---

Viene en NANOSEGUNDOS como string, ej. "28800000000000" = 28,800s = 8h. Esto
sí está confirmado sin ambigüedad por la spec, y además cuadra con la doc
conceptual de risechain.com, que indica que el funding de RiseX "se calcula
sobre una ventana de 8 horas" (aunque se pague cada hora). Conversión:

    interval_hours = funding_interval_ns / 1e9 / 3600

--- Nota sobre `open_interest` (asunción, NO confirmada — ambigüedad real en
    la documentación, no solo falta de datos en vivo) ---

La spec no indica denominación ni escala para `open_interest` ni `mark_price`
(a diferencia de los campos de funding, para los que sí se documentan "18
decimales" explícitamente). Ante esta ambigüedad genuina, se aplica aquí la
misma asunción conservadora usada para Hyperliquid/Lighter/Paradex/Pacifica
cuando no hay evidencia de lo contrario: `open_interest` se asume en
UNIDADES DEL ACTIVO BASE, y se convierte a USD multiplicando por
`mark_price`. Si RiseX en realidad reporta `open_interest` ya en USD (como
Extended o, según nuestra propia asunción, Variational), esto duplicaría
por error el OI mostrado para RiseX — comprobar esto es la primera cosa a
revisar si el OI de RiseX aparece desproporcionado frente a otros exchanges
en producción.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rise.trade"
MARKETS_URL = f"{BASE_URL}/v1/markets"

NANOS_PER_HOUR = 1e9 * 3600.0

# RiseX liquida funding cada hora según la doc conceptual, aunque la tasa se
# calcule sobre una ventana de 8h — mismo patrón que Extended. Solo se usa
# si algún mercado no trae funding_interval (no debería pasar).
FALLBACK_INTERVAL_HOURS = 8.0

_QUOTE_SUFFIXES = ("-PERP", "-USD", "-USDC", "-USDT")


def _strip_quote_suffix(raw_symbol: str) -> str:
    for suffix in _QUOTE_SUFFIXES:
        if raw_symbol.endswith(suffix):
            return raw_symbol[: -len(suffix)]
    return raw_symbol


class RiseXConnector:
    name = "risex"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(MARKETS_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()

        # La spec documenta la respuesta como un array de mercados en la
        # raíz, pero por si acaso viene envuelta en {"data": [...]} o
        # {"markets": [...]} (patrón visto en otros DEX de este proyecto),
        # se comprueban ambas formas.
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("markets") or payload.get("data") or []
        else:
            rows = []

        if not rows:
            raise RuntimeError(
                "risex: /v1/markets no devolvió ningún mercado — probablemente cambió "
                f"la forma de la respuesta (tipo recibido: {type(payload).__name__})"
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for row in rows:
            config = row.get("config") or {}
            raw_symbol = (
                row.get("display_name")
                or config.get("name")
                or row.get("base_asset_symbol")
            )
            base_symbol = row.get("base_asset_symbol") or row.get("display_base_asset_symbol")
            rate_raw = row.get("current_funding_rate")
            interval_ns_raw = row.get("funding_interval")

            if raw_symbol is None or rate_raw is None:
                skipped[str(raw_symbol)] = "faltan campos (símbolo/current_funding_rate)"
                continue

            try:
                rate = float(rate_raw)
            except (TypeError, ValueError) as exc:
                skipped[raw_symbol] = f"funding_rate no numérico: {exc}"
                continue

            if interval_ns_raw is not None:
                try:
                    interval_hours = float(interval_ns_raw) / NANOS_PER_HOUR
                except (TypeError, ValueError):
                    interval_hours = FALLBACK_INTERVAL_HOURS
                if interval_hours <= 0:
                    interval_hours = FALLBACK_INTERVAL_HOURS
            else:
                interval_hours = FALLBACK_INTERVAL_HOURS

            mark_price_raw = row.get("mark_price")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            oi_raw = row.get("open_interest")
            open_interest_usd = None
            if oi_raw is not None and mark_price is not None:
                try:
                    # Ver docstring: se asume activo base, se convierte a USD.
                    open_interest_usd = float(oi_raw) * mark_price
                except (TypeError, ValueError):
                    open_interest_usd = None

            symbol = (base_symbol or _strip_quote_suffix(raw_symbol)).upper()

            out.append(
                FundingRate(
                    exchange="risex",
                    venue_type=VenueType.DEX,
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
                f"risex: {len(skipped)}/{len(rows)} mercados se saltaron y no quedó "
                f"ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "risex: %d/%d mercados se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(rows),
                skipped,
            )

        return out


def risex() -> RiseXConnector:
    return RiseXConnector()
