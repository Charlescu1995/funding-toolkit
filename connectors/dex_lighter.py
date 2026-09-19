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

--- Nota sobre volumen 24h (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El mismo `orderBookDetails` que ya se usa para mark price y open interest
trae, por mercado, DOS campos de volumen de 24h: `daily_base_token_volume`
(ej. BTC: 11497.75528, en unidades del activo base) y
`daily_quote_token_volume` (ej. BTC: 880251817.335129, en la moneda de
cotización — USDC en Lighter). `daily_quote_token_volume` ya es el notional
en USD, así que se usa DIRECTAMENTE, sin multiplicar por mark price — mismo
patrón que `daily_quote_token_volume`/`daily_base_token_volume` frente a
`turnoverOf24h`/`volumeOf24h` en KuCoin (ver connectors/cex_kucoin.py). Se
lee del mismo `depth_rows` que ya se recorre para mark price/OI, sin
ninguna llamada de red adicional.

    volume_24h_usd = daily_quote_token_volume   (directo, sin conversión)

--- Nota sobre `raw_symbol` (arreglado 2026-09-19, cosmético, sin impacto
    en el ranking) ---

Antes se usaba `raw_symbol=str(market_id)` (ej. "155", "29") — un ID
numérico interno, ilegible en cualquier panel de diagnóstico que muestre
`raw_symbol` (ver por ejemplo el patrón de `has_implausible_price_pair()`
en core/opportunities.py, que lo saca en sus mensajes). Confirmado en vivo
que tanto `/funding-rates` como `/orderBookDetails` ya traen, cada uno por
su lado, un campo `symbol` con el ticker legible de Lighter (ej. "APT",
"XLM", "POPMART", "MAGS") — y que los dos coinciden exactamente para el
mismo `market_id` (no hay un identificador "más crudo" por debajo de ese
ticker, a diferencia de otros exchanges donde `raw_symbol` sí difiere del
`symbol` normalizado). Se cambia a `raw_symbol=symbol` — mismo patrón que
ya usa Hyperliquid (`connectors/dex_hyperliquid.py`) cuando tampoco hay
nada más nativo que el propio ticker.

--- Nota sobre `status` en orderBookDetails — RESUELTO (2026-09-19,
    auditoría de bugs, Hallazgo #6) ---

Se vio (arreglo del `raw_symbol` de más arriba) un campo `status`
("active"/"inactive") en `orderBookDetails` que este conector no usaba.
Antes de tocar nada se confirmó en vivo (WebFetch): en el momento de esta
investigación había 8 mercados "inactive" identificados de forma
consistente en dos llamadas distintas (MAGS, SPACEX, AI16Z, HYUNDAI,
KRCOMP, DUSK, BIRB, LAUNCHCOIN — el recuento TOTAL de mercados que dio
WebFetch varió entre llamadas, 63 vs. 80, la misma truncación silenciosa
en arrays grandes ya documentada en connectors/cex_kucoin.py, así que ese
total no es fiable, pero estos 8 market_id concretos sí se repitieron
igual en ambas).

Se comprobó, uno a uno contra `/funding-rates`, si alguno de esos 8
mercados "inactive" tenía una fila `exchange="lighter"` — que es la que
este conector ya exige para no descartar el mercado (ver nota de arriba
sobre el bug real de exchanges de referencia mal etiquetados). **Resultado
confirmado: NINGUNO de los 8 la tenía.** Es decir, con el código actual,
un mercado "inactive" en `orderBookDetails` ya no llega a producir ninguna
`FundingRate` HOY — el filtro existente de "solo fila propia de lighter"
ya los descarta de facto, sin necesidad del campo `status` para nada.

Aun así, se añade el filtro por `status` como defensa adicional, no
porque haya un bug real observado: `funding-rates` y `orderBookDetails`
son dos llamadas HTTP independientes, sin ninguna garantía documentada de
que se actualicen atómicamente a la vez — es perfectamente posible que un
mercado pase a "inactive" en el order book mientras `/funding-rates`
todavía trae, por una ventana breve, una fila `exchange="lighter"`
residual (caché, orden de invalidación distinto entre los dos
endpoints...). Sin este filtro, esa ventana colaría el mercado en el
Ranking con datos de un libro de órdenes ya inactivo. Mismo criterio que
el guard de OI negativo añadido a MEXC/HTX (Hallazgo #3): defensivo, sin
causa raíz confirmada todavía, pero correcto tenerlo.

**Fix**: se excluyen del todo (no solo se dejan sin profundidad) los
market_id marcados `status` distinto de `"active"` en `orderBookDetails`
— mismo patrón que `has_implausible_price_pair()`/el filtro de `status`
de `dex_extended.py`: un `status` ausente NO se descarta (no hay evidencia
de que signifique nada malo, solo que el campo faltó en esa fila).
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

        # market_id -> (mark_price, open_interest en unidades base, volumen 24h en USD)
        depth_by_market: dict[int, tuple[float | None, float | None, float | None]] = {}
        # Ver Hallazgo #13 de la auditoría (2026-09-19, severidad baja):
        # mismo guard defensivo que el resto de conectores -- un mark_price
        # de 0 no se propaga (daría oi_usd=0.0 en silencio).
        zero_mark_price_samples: dict[str, object] = {}
        # Ver docstring, "Nota sobre status" (Hallazgo #6 de la auditoría,
        # 2026-09-19): mercados marcados status != "active" se excluyen del
        # todo más abajo, no solo se dejan sin profundidad.
        inactive_market_ids: dict[int, str] = {}  # market_id -> symbol, para el log
        for row in depth_rows:
            market_id = row.get("market_id")
            if market_id is None:
                continue

            status = row.get("status")
            if status is not None and status != "active":
                inactive_market_ids[market_id] = row.get("symbol") or str(market_id)
                continue

            mark_price_raw = row.get("mark_price")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None
                else:
                    if mark_price == 0:
                        zero_mark_price_samples[row.get("symbol") or str(market_id)] = mark_price_raw
                        mark_price = None
            open_interest = row.get("open_interest")
            # Ver docstring: daily_quote_token_volume ya viene en USD, sin conversión.
            volume_24h_raw = row.get("daily_quote_token_volume")
            volume_24h_usd = None
            if volume_24h_raw is not None:
                try:
                    volume_24h_usd = float(volume_24h_raw)
                except (TypeError, ValueError):
                    volume_24h_usd = None
            depth_by_market[market_id] = (mark_price, open_interest, volume_24h_usd)

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
        inactive_excluded: list[str] = []
        # Ver Hallazgo #16 de la auditoría (2026-09-19): a diferencia de
        # mark_price/open_interest/volumen (arriba), `rate` se convertía sin
        # try/except -- un solo mercado con un valor no numérico tiraba el
        # conector ENTERO en vez de perderse solo él.
        non_numeric_rate_samples: dict[str, object] = {}
        for market_id, rows in rows_by_market.items():
            own_row = next((r for r in rows if str(r.get("exchange", "")).lower() == "lighter"), None)
            if own_row is None:
                continue

            # Ver docstring, "Nota sobre status" (Hallazgo #6): defensivo --
            # en la práctica observada, un mercado "inactive" ya nunca llega
            # aquí porque tampoco tiene fila "lighter" propia (ver arriba),
            # pero por si /funding-rates y /orderBookDetails se desincronizan.
            if market_id in inactive_market_ids:
                inactive_excluded.append(inactive_market_ids[market_id])
                continue

            symbol = own_row.get("symbol")
            rate = own_row.get("rate")
            if symbol is None or rate is None:
                continue

            try:
                funding_rate_value = float(rate)
            except (TypeError, ValueError):
                if len(non_numeric_rate_samples) < 6:
                    non_numeric_rate_samples[symbol] = rate
                continue

            mark_price, open_interest_base, volume_24h_usd = depth_by_market.get(
                market_id, (None, None, None)
            )
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
                    raw_symbol=symbol,
                    funding_rate=funding_rate_value,
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=oi_usd,
                    volume_24h_usd=volume_24h_usd,
                )
            )

        if inactive_excluded:
            logger.info(
                "lighter: %d mercado(s) excluido(s) por status distinto de 'active' en "
                "orderBookDetails (Hallazgo #6 de la auditoría, defensivo -- en la práctica "
                "observada estos mercados ya no tienen fila 'lighter' propia en funding-rates, "
                "ver docstring del módulo): %s",
                len(inactive_excluded),
                sorted(inactive_excluded),
            )

        if zero_mark_price_samples:
            logger.warning(
                "lighter DIAGNÓSTICO mark_price == 0 (Hallazgo #13 de la auditoría, 2026-09-19): "
                "%d mercado(s) con mark_price explícito de 0 -- no se calculó open_interest_usd: %s",
                len(zero_mark_price_samples),
                zero_mark_price_samples,
            )

        if non_numeric_rate_samples:
            logger.warning(
                "lighter DIAGNÓSTICO rate no numérico (Hallazgo #16 de la auditoría, 2026-09-19): "
                "%d mercado(s) descartado(s) -- antes de este fix, cualquiera de estos habría "
                "tirado el conector ENTERO en vez de perderse solo él: %s",
                len(non_numeric_rate_samples),
                non_numeric_rate_samples,
            )

        return out


def lighter() -> LighterConnector:
    return LighterConnector()
