"""
Conector para Hibachi Exchange — DEX de perpetuos (zkTLS, con SDK oficial en PyPI).

--- Bug real de investigación (no en producción, pero vale la pena dejarlo
    documentado): la URL base correcta NO es la que la doc/el propio SDK
    sugieren a primera vista ---

Hibachi separa su API en dos dominios: `https://api.hibachi.xyz` (privado,
autenticado — trading) y `https://data-api.hibachi.xyz` (público, datos de
mercado). El SDK oficial (`hibachi-xyz` en PyPI) tiene un método interno
`__send_simple_request()` que, pese a que el cliente se construye con un
parámetro `api_url` que por defecto apunta a `api.hibachi.xyz`, en realidad
usa SIEMPRE `data_api_url` (`data-api.hibachi.xyz`) para las peticiones
públicas sin autenticar — confirmado leyendo el código fuente real del SDK
(`executors/httpx.py`, método `send_simple_request`: construye la URL como
`f"{self.data_api_url}{path}"`). Probar `api.hibachi.xyz/market/exchange-info`
directamente (como sugeriría la doc a primera lectura) da 404 — el dominio
correcto para todo lo de este conector es `data-api.hibachi.xyz`.

--- Nota sobre el endpoint elegido: /market/inventory en vez de llamadas por
    símbolo (CONFIRMADO en vivo) ---

La doc y el propio SDK exponen métodos por símbolo
(`/market/data/prices?symbol=X`, `/market/data/open-interest?symbol=X`) que
en un primer vistazo parecían obligar a un pool de hilos, como con
edgeX/GRVT. Pero `/market/inventory` (pensado para listar mercados) en
realidad YA TRAE, para cada mercado, tanto el funding rate estimado
(`info.estimatedFundingRate`) como el open interest
(`info.openInterestQuantity`) y el mark price (`info.markPrice`) — todo en
una sola llamada bulk, confirmado contra la respuesta real. Por tanto este
conector NO hace falta pool de hilos (como Backpack/Variational/RiseX, a
diferencia de edgeX/GRVT) ni llamadas por símbolo.

Forma real de la respuesta (confirmada en vivo):

    {
      "markets": [
        {
          "contract": {
            "symbol": "BTC/USDT-P",
            "underlyingSymbol": "BTC",
            "status": "LIVE",
            "symbolStatus": "OPEN",
            ...
          },
          "info": {
            "estimatedFundingRate": "0.000023",
            "markPrice": "77630.90168",
            "openInterestQuantity": "8.8877252888",
            "bestAskPrice": "...", "bestBidPrice": "...",
            ...
          }
        },
        ...
      ],
      ...
    }

--- Nota sobre el filtro de mercados operables ---

Cada `contract` trae DOS campos que parecen indicar si el mercado está
operable: `status` (visto en vivo: "LIVE") y `symbolStatus` (visto en vivo:
"OPEN"). Solo se han observado esos dos valores en los mercados de mayor
volumen consultados (BTC, ETH) — no se ha podido confirmar en vivo qué
valores toman mercados delistados/pausados/no lanzados (ningún exchange de
los ya integrados en este proyecto ha resultado NO tener mercados así
mezclados en su listado bulk — Aster, RiseX...). Por precaución, y siguiendo
el mismo patrón fail-loud que el resto de conectores, se exige AMBOS
`status == "LIVE"` y `symbolStatus == "OPEN"` para aceptar un mercado;
cualquier otro valor se descarta sin más (no se trata como error).

--- Nota sobre `estimatedFundingRate` (intervalo asumido, PENDIENTE DE
    VERIFICAR EN VIVO) ---

El valor en sí se confirmó en vivo como una tasa cruda del intervalo, no
anualizada (ej. "0.000023" para BTC, "-0.000014" para ETH — magnitudes
coherentes con un funding normal, no con un APY). Lo que NO se ha podido
confirmar en vivo es el intervalo de liquidación exacto: la documentación
conceptual de Hibachi (no la respuesta de esta API) indica liquidación
horaria. Como ningún campo de `/market/inventory` trae el intervalo
explícito por mercado (a diferencia de Backpack/RiseX/Aster), se usa 1h fijo
para todos los símbolos — igual de "asunción documentada, no confirmada por
campo explícito" que el intervalo por defecto que ya se usa para varios CEX
en connectors/cex_ccxt.py.

--- Nota sobre `openInterestQuantity` (CONFIRMADO en vivo, contrastado con
    /market/data/open-interest?symbol=X) ---

Viene en unidades del activo base, no en USD: `openInterestQuantity` para
BTC coincidía (mismo orden de magnitud, ~8.89) con `totalQuantity` del
endpoint dedicado `/market/data/open-interest?symbol=BTC/USDT-P` — un OI de
~8.89 solo tiene sentido como BTC, no como USD. Se multiplica por
`markPrice` para obtener el USD.

    open_interest_usd = openInterestQuantity × markPrice

--- Nota sobre volumen 24h: hueco conocido, no disponible en bulk ---

`/market/inventory` (el único endpoint bulk que usa este conector) NO trae
ningún campo de volumen — confirmado por dos vías independientes, ya que el
dominio `data-api.hibachi.xyz` está bloqueado tanto para curl directo (403
del proxy de egress de este sandbox) como para WebFetch (todo el dominio está
vedado por su propio `robots.txt`, a diferencia de KuCoin/MEXC/HTX/Backpack/
Nado, donde WebFetch sí pudo consultar la API en vivo):

1. El SDK oficial (`hibachi-xyz` en PyPI, inspeccionado leyendo su código
   fuente real descargado del paquete, no solo la doc): la clase
   `InventoryResponse` -> `Market` -> `MarketInfo` (en `hibachi_xyz/types.py`)
   no declara ningún campo de volumen. El único campo de volumen del SDK,
   `StatsResponse.volume24h`, se mapea al método `get_stats(self, symbol)`,
   que llama a `GET /market/data/stats?symbol=X` — endpoint POR SÍMBOLO, no
   bulk (`hibachi_xyz/api.py`, línea ~585).

2. ccxt (que sí tiene un módulo `hibachi.py`, confirmado con
   `'hibachi' in ccxt.exchanges`) corrobora lo mismo desde el lado
   independiente de otro equipo: declara explícitamente
   `has['fetchTickers'] = False` (sin endpoint de tickers bulk) y
   `has['fetchTicker'] = True`, y su `fetch_ticker(symbol)` combina DOS
   llamadas por símbolo (`/market/data/prices` + `/market/data/stats`) para
   construir el volumen — exactamente el mismo patrón por-símbolo que el SDK
   oficial.

Como la regla del proyecto es "bulk, sin pool de hilos por símbolo" y aquí no
existe ningún endpoint bulk con volumen, se deja `volume_24h_usd=None` para
todos los mercados de Hibachi en vez de inventar un valor o añadir peticiones
símbolo a símbolo — mismo criterio que el hueco ya documentado para
`mark_price` en HTX (ver connectors/cex_htx.py).
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

DATA_API_BASE_URL = "https://data-api.hibachi.xyz"
INVENTORY_URL = f"{DATA_API_BASE_URL}/market/inventory"

# Ver docstring: liquidación horaria según la doc conceptual de Hibachi — la
# API pública no expone el intervalo explícito por mercado, así que se usa
# fijo para todos los símbolos (pendiente de verificar en vivo).
INTERVAL_HOURS = 1.0


class HibachiConnector:
    name = "hibachi"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(INVENTORY_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()

        markets = payload.get("markets") if isinstance(payload, dict) else None
        if not markets:
            raise RuntimeError(
                "hibachi: /market/inventory no devolvió ningún mercado en 'markets' — "
                f"probablemente cambió la forma de la respuesta (claves de nivel superior: "
                f"{list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__})"
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for row in markets:
            if not isinstance(row, dict):
                skipped[str(row)] = "fila no es un objeto (formato inesperado)"
                continue

            contract = row.get("contract") or {}
            info = row.get("info") or {}

            raw_symbol = contract.get("symbol")
            base_symbol = contract.get("underlyingSymbol")

            if raw_symbol is None:
                skipped[str(raw_symbol)] = "sin contract.symbol"
                continue

            # Ver docstring: se exige AMBOS status == LIVE y symbolStatus == OPEN.
            if contract.get("status") != "LIVE" or contract.get("symbolStatus") != "OPEN":
                continue

            rate_raw = info.get("estimatedFundingRate")
            if rate_raw is None:
                skipped[raw_symbol] = "sin info.estimatedFundingRate"
                continue

            try:
                rate = float(rate_raw)
            except (TypeError, ValueError) as exc:
                skipped[raw_symbol] = f"funding rate no numérico: {exc}"
                continue

            mark_price_raw = info.get("markPrice")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            oi_base_raw = info.get("openInterestQuantity")
            open_interest_usd = None
            if oi_base_raw is not None and mark_price is not None:
                try:
                    # Ver docstring: confirmado en vivo, viene en activo base.
                    open_interest_usd = float(oi_base_raw) * mark_price
                except (TypeError, ValueError):
                    open_interest_usd = None

            out.append(
                FundingRate(
                    exchange="hibachi",
                    venue_type=VenueType.DEX,
                    symbol=base_symbol or raw_symbol,
                    raw_symbol=raw_symbol,
                    funding_rate=rate,
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=open_interest_usd,
                    # Ver docstring: hueco conocido, sin dato bulk disponible.
                    volume_24h_usd=None,
                )
            )

        if not out:
            sample = dict(list(skipped.items())[:3])
            raise RuntimeError(
                f"hibachi: {len(skipped)}/{len(markets)} mercados se saltaron (o no estaban "
                f"LIVE/OPEN) y no quedó ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "hibachi: %d/%d mercados se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(markets),
                skipped,
            )

        return out


def hibachi() -> HibachiConnector:
    return HibachiConnector()
