"""
Conector para GRVT — DEX de perpetuos con order book propio (no está en ccxt:
`ccxt.grvt.fetch_funding_rates()` existe como método pero está sin implementar,
lanza `NotSupported` — comprobado leyendo el código fuente de ccxt, no solo la
bandera `has['fetchFundingRates']`, que en este caso concreto está mal
declarada como `None` en vez de `False`).

GRVT es, de los DEX que hemos integrado hasta ahora, el que menos se presta a
un conector "barato": su API de datos de mercado NO tiene ningún endpoint
bulk para funding/ticker — todo es por instrumento, uno a uno. El flujo es:

    1. POST /full/v1/all_instruments  {"is_active": true, "kinds": ["PERPETUAL"]}
       -> lista de instrumentos (ej. "BTC_USDT_Perp")
    2. POST /full/v1/ticker  {"instrument": "BTC_USDT_Perp"}   (uno por instrumento)
       -> funding_rate_curr, mark_price, index_price, open_interest

Para no convertir cada refresco de la app en decenas/cientos de peticiones
secuenciales, el paso 2 se hace en paralelo con un pool de hilos — aun así,
GRVT va a ser el conector más lento de la lista por diseño de su API, no por
nada que podamos optimizar desde aquí.

Docs: https://api-docs.grvt.io/market_data_api/
      (la documentación interactiva no renderiza los ejemplos JSON completos
      vía fetch dirigido — se contrastó contra el SDK oficial en Python:
      https://github.com/gravity-technologies/grvt-pysdk, concretamente
      src/pysdk/grvt_ccxt.py y grvt_ccxt_env.py, que si confirman que estos
      endpoints de market data NO requieren API key: el helper de cookie de
      sesión se salta la autenticación por completo cuando no se le pasa
      una api_key, y solo hace falta para los endpoints de trading)

Puntos SIN verificar contra la red real (este entorno de desarrollo no tiene
salida a internet hacia exchanges) — de los conectores DEX que hemos hecho,
este es el que más asunciones acumula, así que es el primero a revisar en
cuanto lo tengas desplegado, igual que hicimos con Lighter:

  - **Escala de precios**: mark_price/index_price vienen como enteros en
    formato texto (ej. "59373870996065" para ~59,373.87 USD), lo que sugiere
    un punto fijo de 9 decimales (÷ 1e9). Se asume ese factor para TODOS los
    instrumentos por igual — el propio esquema de GRVT tiene un campo
    `base_decimals`/`quote_decimals` por instrumento que podría implicar que
    la escala varíe caso a caso; no se ha podido confirmar.
  - **Unidad del funding rate**: el campo `funding_rate_curr` del ticker se
    documenta como expresado en "centibeeps". Se asume 1 centibeep = 1e-6 en
    fracción decimal (100 centibeeps = 0.01% = 0.0001), pero esta conversión
    no viene de un ejemplo numérico oficial confirmado, es una deducción del
    propio nombre de la unidad — es EL PRIMER NÚMERO a comparar contra la
    interfaz oficial de GRVT en cuanto haya datos en vivo.
  - **Intervalo de liquidación**: no se encontró un campo fiable de "horas
    de intervalo" ni en el listado de instrumentos ni en el ticker (el tipo
    `Instrument` del SDK oficial no lo incluye, solo distingue PERPETUAL vs
    instrumentos con vencimiento). Se intenta leer un campo así por si la
    API real lo trae con otro nombre, y si no aparece se usa 8h por defecto
    (el más común en el sector) — candidato número dos a revisar en vivo.
  - **Unidad del open interest**: la documentación pública dice que viene
    "en unidades decimales del activo base" — pero probado con un valor de
    ejemplo real (8174350000000, del propio SDK oficial) sin escalar, el USD
    resultante da cifras de cientos de billones de dólares, imposible para
    un exchange de este tamaño. Como el resto de campos numéricos de GRVT
    (precios incluidos) van en punto fijo de 9 decimales, se asume que
    open_interest usa la MISMA escala (÷ 1e9) antes de multiplicar por el
    mark price — coherente mejor que sin escalar, pero sigue sin confirmarse
    contra la red real. Tercer y último candidato a revisar en vivo.

Primer despliegue real de este conector (con el fail-loud ya puesto): dio
"grvt: 0/194 instrumentos fallaron y no quedó ningún par válido — muestra de
errores: {}" — es decir, CERO peticiones lanzaron excepción (todas las 194
respondieron 200 OK), pero NINGUNA trajo un ticker reconocible. Eso descartó
un problema de red/autenticación y apuntó a un desajuste de forma. Se añadió
un segundo nivel de diagnóstico (`unparsed_samples`) que, en el SIGUIENTE
despliegue, reveló la causa exacta sin necesitar otra ronda de "prueba y
build": la clave `"result"` SÍ es correcta y el ticker SÍ trae datos, pero el
campo del funding rate no se llama `funding_rate_curr` como decía el SDK/doc
— se llama **`funding_rate_8h_curr`** (junto a `funding_rate_8h_avg`). Claves
completas vistas en vivo en el ticker: `event_time, instrument, mark_price,
index_price, last_price, last_size, mid_price, best_bid_price,
best_bid_size, best_ask_price, best_ask_size, funding_rate_8h_curr,
funding_rate_8h_avg, interest_rate, forward_price, buy_volume_24h_b,
sell_volume_24h_b, buy_volume_24h_q, sell_volume_24h_q, high_price,
low_price, open_price, open_i(nterest, truncado en el log)`. **Ya
corregido**: el conector ahora lee `funding_rate_8h_curr` (con
`funding_rate_curr` como segundo intento por compatibilidad). De paso, el
propio nombre del campo confirma algo que antes era una suposición: el
intervalo de liquidación de GRVT es de 8h — coincide con el `FALLBACK_INTERVAL_HOURS`
que ya se estaba usando, así que no hace falta tocarlo, pero ahora ese valor
tiene respaldo directo en vez de ser solo "el más común del sector".

Lo que SIGUE sin confirmarse, y es lo primero a comparar contra la interfaz
oficial de GRVT en cuanto el conector devuelva números: la escala de precios
(÷ 1e9) y la conversión de "centibeeps" (÷ 1e6) — ver puntos más arriba en
este docstring, siguen siendo deducciones sin un ejemplo numérico oficial
confirmado.

--- Nota sobre volumen 24h (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El log de producción citado arriba ya reveló, sin necesitar ninguna llamada
extra, que el ticker de GRVT trae CUATRO campos de volumen de 24h al mismo
nivel que `mark_price`/`open_interest`: `buy_volume_24h_b`,
`sell_volume_24h_b`, `buy_volume_24h_q`, `sell_volume_24h_q`. El propio
`/full/v1/ticker` es POST-only (confirmado de nuevo hoy: un GET directo a esa
URL responde `405 Method Not Allowed`, así que no se puede reproducir un
ejemplo en vivo con WebFetch, que solo hace GET — la misma limitación ya
explicada más arriba para el resto de este conector), así que la evidencia
aquí es la documentación oficial en vivo (WebFetch contra
https://api-docs.grvt.io/market_data_api/, no solo el nombre del campo):

  - `buy_volume_24h_b` / `sell_volume_24h_b`: "the 24 hour taker buy/sell
    volume of the instrument, **expressed in base asset decimal units**".
  - `buy_volume_24h_q` / `sell_volume_24h_q`: lo mismo pero "**expressed in
    quote asset decimal units**" — para un instrumento tipo "BTC_USDT_Perp",
    el activo de cotización es USDT ≈ USD.

Confirma exactamente lo que sugería el sufijo (`_b` = base, `_q` = quote) y
la pista de esta tarea: el lado a usar es `_q`, y como GRVT reporta
comprador y vendedor por separado, se suman ambos para el volumen total
negociado en el instrumento:

    volume_24h_usd = (buy_volume_24h_q + sell_volume_24h_q) / <escala>

**Sobre la escala** — esto es lo que NO se pudo confirmar con un ejemplo
numérico real: el JSON de ejemplo que trae la propia página de docs para
`ticker` es un placeholder, no un valor real (los cuatro campos de volumen
aparecen con el mismo valor de relleno idéntico, "123456.78", y el
`mark_price` de ese mismo ejemplo, "65038.01", contradice la escala de punto
fijo ÷1e9 que sí se confirmó en producción para `mark_price` — ver el bloque
de "Puntos SIN verificar" más arriba: los ejemplos de esta documentación NO
son fiables numéricamente). Ante esa contradicción, se seguye el mismo
criterio que el resto del módulo: como la propia redacción oficial para
`buy_volume_24h_q`/`sell_volume_24h_q` ("expressed in ... decimal units") es
la MISMA fórmula de palabras que usa la documentación para `open_interest`
("in base asset decimal units" — ver nota de open_interest más arriba, que
el módulo YA escala ÷1e9 por eso), se aplica la misma escala de punto fijo
÷1e9 (`PRICE_SCALE`) a la suma de `_q`, en vez de asumir que viene en USD
plano sin escalar:

    volume_24h_usd = (float(buy_volume_24h_q) + float(sell_volume_24h_q)) / PRICE_SCALE

Esto es una asunción adicional, NO confirmada contra un valor numérico real
(se añade a la lista de "puntos sin verificar" del módulo, arriba) — el
primer número de volumen que devuelva este conector en producción es el
candidato a comparar contra la interfaz oficial de GRVT, igual que ya se
hizo con `funding_rate_8h_curr` y como sigue pendiente con el resto de
escalas.

--- BUG REAL encontrado en producción, corregido (Paso 6, tras reporte del
    usuario: "el Price Spread es una bestialidad" + OI/Volumen con
    decenas de decimales para valores cercanos a cero, en TODAS las filas
    donde grvt es una de las dos piernas) ---

La asunción de arriba ("se aplica la MISMA escala ÷1e9 a todo, incluido
open_interest y volumen, porque no se pudo confirmar lo contrario") era la
causa. Se releyó hoy (2026-09-16), campo por campo, la documentación oficial
en vivo — no el ejemplo JSON de la página (ya demostrado no fiable
numéricamente más arriba en este mismo docstring), sino el TEXTO de cada
campo, vía WebFetch contra:

  - https://api-docs.grvt.io/schemas/api_ticker_response/
  - https://api-docs.grvt.io/schemas/api_get_all_instruments_response/

Y el texto es inequívoco, campo por campo:

  - `mark_price`, `index_price`, `last_price`, `mid_price`,
    `best_bid_price`, `best_ask_price`, `high_price`, `low_price`,
    `open_price`, `forward_price`: TODOS "expressed in `9` decimals", sin
    excepción por instrumento. Es decir: la escala de PRECIOS sí es
    uniforme ÷1e9 para todos los instrumentos — esa parte de la asunción
    original era correcta, no ha cambiado.
  - `open_interest`, `last_size`, `best_bid_size`, `best_ask_size`,
    `buy_volume_24h_b`, `sell_volume_24h_b`: "expressed in **base asset
    decimal units**" — NO la escala de precio. La unidad real depende del
    campo `base_decimals` que trae `all_instruments` por instrumento
    ("the smallest denomination of the base asset supported by GRVT (+3
    represents 0.001, -3 represents 1000, 0 represents 1)").
  - `buy_volume_24h_q` / `sell_volume_24h_q`: "expressed in **quote asset
    decimal units**" — el campo `quote_decimals` correspondiente, misma
    idea que `base_decimals` pero para el activo de cotización (USDT).

O sea: aplicar ÷1e9 a `open_interest` y a `buy/sell_volume_24h_q` estaba
mal para cualquier instrumento cuyo `base_decimals`/`quote_decimals` no
fuera casualmente 9 — y BTC/ETH sí lo son (según el propio SDK oficial de
GRVT, que usa un multiplicador de fallback de 1e9 específicamente para
"BTC_ETH", ver `grvt_ccxt_utils.py` del SDK), lo que explica por qué el bug
llevaba semanas invisible: los pares con BTC/ETH como pierna salían bien
por pura coincidencia de escala, y los primeros símbolos de baja
capitalización que aparecieron en el ranking (KPEPE y similares) fueron los
que lo delataron con OI/Volumen prácticamente cero (de dividir por 1e9 de
más) y, en consecuencia, un Price Spread disparatado (si algo más en la
cadena de cálculo llegaba a depender de ese OI casi-cero — ver más abajo
sobre por qué el precio en sí NO era la causa de eso).

**Corrección aplicada**: `base_decimals` y `quote_decimals` se leen del
MISMO `all_instruments` que ya se pedía (no hace falta ninguna llamada de
red extra), uno por instrumento, y se usan así:

    oi_base_units    = open_interest_raw / 10**base_decimals
    oi_usd            = oi_base_units * mark_price          # mark_price sigue ÷1e9, sin cambios
    volume_24h_usd    = (buy_volume_24h_q + sell_volume_24h_q) / 10**quote_decimals

Si algún instrumento no trae `base_decimals`/`quote_decimals` en
`all_instruments` (no debería pasar según el esquema, pero por si acaso),
se deja el OI/Volumen de ESE instrumento en `None` en vez de asumir un
divisor — mismo criterio de "no inventar" que el resto del proyecto.

**Sobre el Price Spread disparatado en sí**: el mark_price NO estaba mal
(su escala ÷1e9 es uniforme y está confirmada arriba), así que la
corrección de OI/Volumen no lo toca. Como este mismo conector ya demostró
una vez que la documentación de GRVT puede no coincidir con la realidad en
vivo (ver el ejemplo numérico de `mark_price` de la propia página de docs,
que contradice el formato real confirmado en producción), no se da por
sentado que el precio esté libre de problemas solo porque el texto de la
documentación lo diga — como red de seguridad adicional, `price_spread()`
en `core/scoring.py` ahora descarta (devuelve `None`, se ve como "—" en la
interfaz) cualquier spread por encima de un umbral que ya es imposible para
dos precios reales del mismo activo, en vez de mostrar un porcentaje que no
nos creemos ni nosotros. Ver el docstring de esa función para el umbral
exacto y el razonamiento.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://market-data.grvt.io"
ALL_INSTRUMENTS_URL = f"{BASE_URL}/full/v1/all_instruments"
TICKER_URL = f"{BASE_URL}/full/v1/ticker"

# Ver nota en el docstring del módulo: no se encontró un campo fiable de
# intervalo de liquidación, así que si no aparece en la respuesta real se
# usa este valor por defecto.
FALLBACK_INTERVAL_HOURS = 8.0

# Ver nota sobre "centibeeps" en el docstring del módulo.
CENTIBEEPS_TO_DECIMAL = 1e-6

# Precios (mark/index) vienen como enteros de punto fijo — ver nota de escala.
PRICE_SCALE = 1e9

# Nº de hilos para las llamadas de ticker en paralelo (una por instrumento,
# porque GRVT no ofrece un endpoint bulk). Ajustado para no disparar el rate
# limit de golpe, pero sin tardar minutos en un exchange con muchos mercados.
MAX_WORKERS = 12


class GrvtConnector:
    name = "grvt"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def _fetch_ticker_raw(self, instrument: str) -> dict:
        # Devuelve el payload de la petición TAL CUAL, sin recortar a
        # "result" — ver comentario en fetch_funding_rates() sobre por qué
        # esto ahora importa: si la forma real no es la esperada, necesitamos
        # poder enseñar el JSON crudo en el error, no solo silenciarlo.
        resp = self._session.post(
            TICKER_URL, json={"instrument": instrument}, timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_funding_rates(self) -> list[FundingRate]:
        instruments_resp = self._session.post(
            ALL_INSTRUMENTS_URL,
            json={"is_active": True, "kinds": ["PERPETUAL"]},
            timeout=self._timeout,
        )
        instruments_resp.raise_for_status()
        instruments_payload = instruments_resp.json()
        instruments = instruments_payload.get("result", [])

        # instrument (símbolo GRVT) -> intervalo en horas, si la respuesta
        # real trae el campo bajo alguno de estos nombres.
        interval_by_instrument: dict[str, float] = {}
        # instrument -> base_decimals / quote_decimals, tal cual los trae
        # all_instruments — ver nota "BUG REAL" en el docstring del módulo:
        # esta es la escala real de open_interest / volumen 24h, NO el
        # PRICE_SCALE de los precios.
        base_decimals_by_instrument: dict[str, int] = {}
        quote_decimals_by_instrument: dict[str, int] = {}
        instrument_names: list[str] = []
        for row in instruments:
            name = row.get("instrument")
            if name is None:
                continue
            instrument_names.append(name)
            for key in ("funding_interval_hours", "fundingIntervalHours", "fi"):
                if row.get(key) is not None:
                    try:
                        interval_by_instrument[name] = float(row[key])
                    except (TypeError, ValueError):
                        pass
                    break
            if row.get("base_decimals") is not None:
                try:
                    base_decimals_by_instrument[name] = int(row["base_decimals"])
                except (TypeError, ValueError):
                    pass
            if row.get("quote_decimals") is not None:
                try:
                    quote_decimals_by_instrument[name] = int(row["quote_decimals"])
                except (TypeError, ValueError):
                    pass

        if not instrument_names:
            # No devolvemos silenciosamente [] — eso es indistinguible de
            # "GRVT no tiene mercados" cuando lo real es que la forma de la
            # respuesta cambió o el endpoint falló de otra manera.
            raise RuntimeError(
                "grvt: all_instruments no devolvió ningún instrumento — probablemente "
                "cambió la forma de la respuesta (revisar contra la API en vivo)"
            )

        out: list[FundingRate] = []
        errors: dict[str, str] = {}
        # Instrumentos donde la petición SÍ respondió 200 sin lanzar excepción,
        # pero no encontramos dentro datos que sepamos interpretar (el campo
        # "result" no está donde lo esperábamos, o falta "funding_rate_curr").
        # Antes esto se descartaba con un simple `continue`, indistinguible de
        # "este instrumento no tiene datos" — pero si TODOS los instrumentos
        # caen aquí a la vez, lo real es que la forma de la respuesta no es la
        # que asumimos, y sin ver el JSON crudo no hay forma de saber por qué
        # desde aquí (esta API es POST y no se puede probar ni con WebFetch ni
        # desde este sandbox). Guardamos una muestra del payload crudo para
        # poder enseñarlo en el error si hace falta.
        unparsed_samples: dict[str, object] = {}

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            future_to_name = {
                pool.submit(self._fetch_ticker_raw, name): name for name in instrument_names
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    payload = future.result()
                except Exception as exc:  # noqa: BLE001 — un instrumento suelto no debe tirar todo el conector
                    errors[name] = f"{type(exc).__name__}: {exc}"
                    continue

                result = payload.get("result") if isinstance(payload, dict) else None
                # La API puede devolver el resultado como dict directo o como
                # lista de un elemento según el endpoint/versión — cubrimos
                # ambos casos.
                ticker = result[0] if isinstance(result, list) and result else (
                    result if isinstance(result, dict) else None
                )

                if not ticker:
                    if len(unparsed_samples) < 3:
                        unparsed_samples[name] = payload
                    continue

                # Ver docstring del módulo: el primer despliegue con diagnóstico
                # reveló que el campo real NO es "funding_rate_curr" (lo que
                # decía la documentación/SDK), sino "funding_rate_8h_curr". Hoy
                # (2026-09-16), releyendo la documentación oficial en vivo para
                # el bug de escala de OI/Volumen, se confirmó que
                # `funding_rate_8h_curr`/`funding_rate_8h_avg` están marcados
                # DEPRECATED en el esquema actual, y existe un campo nuevo
                # `funding_rate` ("the current indicative funding rate for the
                # active interval, expressed in centibeeps" — misma unidad, así
                # que la conversión ÷CENTIBEEPS_TO_DECIMAL de abajo sigue
                # aplicando sin cambios). Se prueba el nuevo primero y se cae a
                # los antiguos por compatibilidad, en vez de esperar a que GRVT
                # retire el campo deprecated y rompa el conector sin avisar.
                rate_raw = ticker.get(
                    "funding_rate",
                    ticker.get("funding_rate_8h_curr", ticker.get("funding_rate_curr")),
                )
                if rate_raw is None:
                    if len(unparsed_samples) < 3:
                        # Aquí sí encontramos un "ticker", pero sin el campo
                        # que esperábamos — guardamos las claves que SÍ trae,
                        # más útil para diagnosticar que el payload entero.
                        unparsed_samples[name] = {"claves_presentes": list(ticker.keys())}
                    continue

                mark_price_raw = ticker.get("mark_price")
                mark_price = (
                    float(mark_price_raw) / PRICE_SCALE if mark_price_raw is not None else None
                )

                # Ver nota "BUG REAL" en el docstring del módulo: open_interest
                # NO usa la escala de precio (PRICE_SCALE) — usa base_decimals,
                # por instrumento, tal cual lo confirma la documentación oficial
                # ("expressed in base asset decimal units"). Si este instrumento
                # concreto no trajo base_decimals en all_instruments, se deja
                # el OI en None en vez de adivinar un divisor.
                oi_raw = ticker.get("open_interest")
                base_decimals = base_decimals_by_instrument.get(name)
                oi_usd = None
                if oi_raw is not None and mark_price is not None and base_decimals is not None:
                    try:
                        oi_base_units = float(oi_raw) / (10 ** base_decimals)
                        oi_usd = oi_base_units * mark_price
                    except (TypeError, ValueError):
                        oi_usd = None

                # Mismo caso que open_interest, pero con quote_decimals (la
                # documentación oficial dice "expressed in quote asset decimal
                # units" para buy_volume_24h_q/sell_volume_24h_q — ver nota
                # "BUG REAL" en el docstring del módulo).
                buy_q_raw = ticker.get("buy_volume_24h_q")
                sell_q_raw = ticker.get("sell_volume_24h_q")
                quote_decimals = quote_decimals_by_instrument.get(name)
                volume_24h_usd = None
                if buy_q_raw is not None and sell_q_raw is not None and quote_decimals is not None:
                    try:
                        volume_24h_usd = (float(buy_q_raw) + float(sell_q_raw)) / (10 ** quote_decimals)
                    except (TypeError, ValueError):
                        volume_24h_usd = None

                base = name.split("_")[0] if "_" in name else name
                interval_hours = interval_by_instrument.get(name, FALLBACK_INTERVAL_HOURS)

                out.append(
                    FundingRate(
                        exchange="grvt",
                        venue_type=VenueType.DEX,
                        symbol=base,
                        raw_symbol=name,
                        funding_rate=float(rate_raw) * CENTIBEEPS_TO_DECIMAL,
                        interval_hours=interval_hours,
                        mark_price=mark_price,
                        next_funding_time=None,
                        open_interest_usd=oi_usd,
                        volume_24h_usd=volume_24h_usd,
                    )
                )

        if not out:
            # Igual que arriba: si TODOS los tickers fallaron o vinieron
            # vacíos, no es lo mismo que "GRVT no tiene funding rates" — que
            # suba el motivo real en vez de un "0 pares" mudo.
            #
            # Distinguimos dos causas posibles, porque son diagnósticos muy
            # distintos: peticiones que lanzaron una excepción real (errors)
            # frente a peticiones que respondieron 200 pero sin nada que
            # sepamos interpretar (unparsed_samples) — este segundo caso, si
            # afecta a TODOS los instrumentos a la vez como pasó en el primer
            # despliegue de este fix (0 errores, 0 pares), apunta a que
            # `TICKER_URL` responde con una forma distinta a la asumida
            # (quizás el payload de la petición debería usar otra clave que
            # "instrument", o el campo de respuesta no se llama "result"/
            # "funding_rate_curr") — y esta muestra del JSON crudo es la
            # única forma de verlo, ya que esta API no se puede probar ni con
            # WebFetch (solo GET) ni desde este sandbox (sin salida a
            # exchanges).
            error_sample = dict(list(errors.items())[:3])
            unparsed_sample = {
                name: str(payload)[:400] for name, payload in unparsed_samples.items()
            }
            raise RuntimeError(
                f"grvt: 0 pares válidos de {len(instrument_names)} instrumentos — "
                f"{len(errors)} peticiones fallaron con excepción (muestra: {error_sample}); "
                f"el resto respondió sin lanzar error pero sin datos reconocibles "
                f"(muestra de respuesta cruda, recortada a 400 car.: {unparsed_sample})"
            )
        elif errors:
            logger.warning(
                "grvt: %d/%d instrumentos fallaron al pedir su ticker (se omiten, no tiran el resto): %s",
                len(errors),
                len(instrument_names),
                errors,
            )

        return out


def grvt() -> GrvtConnector:
    return GrvtConnector()
