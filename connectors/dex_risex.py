"""
Conector para RiseX — DEX de perpetuos sobre RISE Chain (no está en ccxt).

Un solo endpoint público trae todos los mercados de golpe (símbolo, funding
rate actual, intervalo de funding, mark price, index price, open interest...).
Es el diseño más simple de todos los DEX de este proyecto — ni thread pool
(como GRVT/edgeX) ni fallback de intervalo por adivinanza (como Lighter): el
propio endpoint trae el intervalo explícito en nanosegundos.

--- Historial de bugs reales encontrados en producción — importante para no
    repetir el mismo error dos veces ---

Ronda 1: el conector asumía que el contenedor de mercados (`payload["markets"]`
o `["data"]`) es siempre una LISTA, tal como lo documenta el ejemplo del
OpenAPI spec. El primer despliegue real tiró
`AttributeError: 'str' object has no attribute 'get'`.

Ronda 2 (con el fix de la ronda 1 ya puesto): el conector pasó a asumir que,
si el contenedor es un `dict`, es necesariamente una tabla `{market_id:
mercado}` y se limitaba a hacer `.values()`. Pero el segundo despliegue real
reveló que la respuesta real de `/v1/markets` NO usa ninguna de las claves
`"markets"` ni `"data"` en el nivel esperado — el conector no las encontró y
cayó al último `fallback` (`container = payload`), y ESE nivel sí es un
dict, pero uno que mezcla la lista completa de mercados con metadata suelta
(ej. un timestamp) en un puñado de claves — así que `.values()` devolvía,
como "filas", la lista entera de mercados de golpe (como un único elemento)
y el timestamp, en vez de cada mercado por separado. Resultado: "2/2
mercados se saltaron" con un error mostrando toda la lista aplastada en una
sola entrada.

**Ya corregido de raíz**: en vez de intentar adivinar el nombre exacto de la
clave que envuelve los mercados (que ya se ha visto que no es fiable), el
conector ahora busca la lista de mercados POR FORMA, no por nombre —
`_extract_market_rows()` recorre el payload (raíz, y un nivel de anidación
dentro de cada valor) buscando la primera lista de objetos que "parecen"
mercados (tienen `market_id` o `current_funding_rate`), o un dict cuyos
valores todos parecen mercados (tabla id → mercado). Esto es agnóstico al
nombre de la clave envolvente, así que sobrevive a que RiseX cambie esa
clave sin previo aviso — el fallo real de las dos rondas anteriores.

--- Confirmado contra datos reales en producción (tras la ronda 2) ---

Con el error de la ronda 2 la API devolvió, dentro del propio mensaje, la
lista completa de mercados reales — eso permitió confirmar de golpe varias
cosas que antes eran solo asunciones documentadas:

- `current_funding_rate` SÍ es una fracción decimal directamente utilizable
  (ej. BTC: `"0.000004358528047845"` con `funding_interval` de 1h → APR ≈
  3.8%, una cifra perfectamente normal) — NO hay que dividir entre 1e18.
  Confirmado, ya no es una asunción pendiente.
- `open_interest` SÍ está en unidades del activo base, no en USD — BTC traía
  `open_interest: "142.86417"` con `mark_price ≈ 77046.87`; interpretado
  como USD directo daría un OI de apenas $142, absurdamente bajo para BTC en
  un DEX con volumen de varios millones diarios; interpretado como
  142.86 BTC × mark_price ≈ $11.0M de OI, una cifra realista. Confirmado, la
  asunción "activo base × mark_price" era correcta.
- `base_asset_symbol` y `display_base_asset_symbol` NO vienen limpios como
  "BTC" — vienen con el par completo pegado, ej. `"BTC/USDC"`. Esto NO se
  había anticipado (el ejemplo del OpenAPI spec sí mostraba "BTC" limpio) y
  habría roto el cruce de símbolos entre exchanges (RiseX habría aparecido
  como "BTC/USDC" en vez de "BTC", sin emparejar con el resto). **Ya
  corregido**: se usa `quote_asset_symbol` (ej. "USDC") para recortar el
  sufijo `/USDC` con precisión, con un fallback a partir por "/" si por lo
  que sea no coincide.
- Cada mercado trae un flag `active` — y en los datos reales hay mercados
  inactivos mezclados con los activos: uno de ellos es literalmente un
  "DOGE/USDC [deprecated-...]" viejo (con precios y funding en cero, viviendo
  junto a un "DOGE/USDC" nuevo y activo), y otros mercados nuevos como ONDO
  con `active: false` y todos los precios/volumen en cero (probablemente
  pendientes de lanzamiento). **Ya corregido**: el conector descarta
  cualquier mercado con `active` distinto de `True` antes de procesarlo, así
  no aparecen duplicados fantasma ni mercados con datos en cero mezclados
  con los reales.

--- Nota sobre las dos webs de documentación ---

RiseX tiene documentación repartida en dos dominios distintos:
`docs.risechain.com/docs/risex` (conceptual — cómo funciona el funding, etc.)
y `developer.rise.trade` (referencia real de la API, con OpenAPI spec). Este
conector se basa en la segunda — aunque, visto lo visto con los dos bugs de
arriba, esa referencia tampoco coincide del todo con la respuesta real.

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

--- Nota sobre `funding_interval` ---

Viene en NANOSEGUNDOS como string, ej. "3600000000000" = 3,600s = 1h (en los
datos reales observados, TODOS los mercados liquidan cada hora, no cada 8h
como sugería la doc conceptual de risechain.com — se usa el valor que trae
cada mercado, así que esto no afecta al cálculo, solo corrige la expectativa
del docstring anterior). Conversión:

    interval_hours = funding_interval_ns / 1e9 / 3600
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rise.trade"
MARKETS_URL = f"{BASE_URL}/v1/markets"

NANOS_PER_HOUR = 1e9 * 3600.0

# Solo se usa si algún mercado no trae funding_interval (no debería pasar,
# todos los observados en vivo lo traían).
FALLBACK_INTERVAL_HOURS = 1.0

_QUOTE_SUFFIXES = ("-PERP", "-USD", "-USDC", "-USDT")


def _looks_like_market(obj: object) -> bool:
    """Heurística de forma, no de nombre de clave — ver docstring del módulo."""
    return isinstance(obj, dict) and (
        "current_funding_rate" in obj or "market_id" in obj
    )


def _extract_market_rows(payload: object) -> list[dict]:
    """
    Busca la lista de mercados dentro del payload por FORMA en vez de por el
    nombre de la clave que la envuelve — la clave real no se pudo confirmar
    de forma fiable ni siquiera tras dos rondas de despliegue (ver docstring
    del módulo), así que adivinar un nombre concreto ("markets", "data"...)
    es justo lo que ya falló dos veces.
    """
    if isinstance(payload, list) and payload and all(_looks_like_market(r) for r in payload):
        return payload

    if not isinstance(payload, dict):
        return []

    # ¿El propio payload es una tabla {market_id: mercado}?
    top_values = list(payload.values())
    if top_values and all(_looks_like_market(v) for v in top_values):
        return top_values

    # Busca, en cada valor de primer y segundo nivel, una lista de mercados
    # o una tabla {market_id: mercado} — cubre tanto
    # {"<clave>": [...mercados...], "<otra_clave>": ...} como wrappers
    # anidados un nivel más, tipo {"data": {"<clave>": [...], ...}}.
    for value in payload.values():
        if isinstance(value, list) and value and all(_looks_like_market(r) for r in value):
            return value
        if isinstance(value, dict):
            inner_values = list(value.values())
            if inner_values and all(_looks_like_market(v) for v in inner_values):
                return inner_values
            for inner_value in value.values():
                if (
                    isinstance(inner_value, list)
                    and inner_value
                    and all(_looks_like_market(r) for r in inner_value)
                ):
                    return inner_value

    return []


def _base_symbol(row: dict) -> str | None:
    """
    En los datos reales, base_asset_symbol/display_base_asset_symbol vienen
    con el par completo pegado (ej. "BTC/USDC"), no limpios — se recorta
    usando quote_asset_symbol si está disponible, con fallback a partir por
    "/". Ver docstring del módulo.
    """
    quote = row.get("quote_asset_symbol")
    for candidate in (row.get("base_asset_symbol"), row.get("display_base_asset_symbol")):
        if not candidate:
            continue
        if quote and candidate.upper().endswith(f"/{quote.upper()}"):
            return candidate[: -(len(quote) + 1)]
        if "/" in candidate:
            return candidate.split("/")[0]
        return candidate
    return None


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

        rows = _extract_market_rows(payload)

        if not rows:
            raise RuntimeError(
                "risex: /v1/markets no devolvió ningún mercado reconocible — probablemente "
                f"cambió otra vez la forma de la respuesta (tipo recibido: {type(payload).__name__})"
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for row in rows:
            if not isinstance(row, dict):
                skipped[str(row)] = "fila no es un objeto (formato inesperado)"
                continue

            # Mercados deprecados/no lanzados todavía conviven con los
            # activos en la misma respuesta (visto en vivo) — se descartan
            # sin más, no es un error de datos.
            if row.get("active") is False:
                continue

            config = row.get("config") or {}
            raw_symbol = (
                row.get("display_name")
                or config.get("name")
                or row.get("base_asset_symbol")
            )
            base_symbol = _base_symbol(row)
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
                    # Confirmado en vivo: viene en activo base, se convierte a USD.
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
                f"risex: {len(skipped)}/{len(rows)} mercados se saltaron (o estaban "
                f"inactivos) y no quedó ningún par válido — muestra de motivos: {sample}"
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
