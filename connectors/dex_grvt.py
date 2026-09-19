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

Historia de las asunciones de escala que se fueron probando (mantenida por
contexto — el bloque **"ESTADO ACTUAL CONFIRMADO"** al final del docstring
es la versión vigente, con datos en vivo reales, y anula todo lo de abajo
que la contradiga):

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

**Sobre el Price Spread disparatado en sí**: en esa ronda se asumió que el
mark_price no estaba mal (porque la documentación decía que su escala ÷1e9
era uniforme) — ver más abajo por qué esa asunción también era falsa. Se
dejó de todos modos, como red de seguridad permanente (no solo mientras
esto se depuraba), una guardia de cordura en `price_spread()`
(`core/scoring.py`): por encima de un umbral ya imposible entre dos precios
reales del mismo activo, el resultado se descarta a `None` en vez de
enseñar un número que no nos creemos.

--- BUG REAL, tercera vuelta: la corrección de arriba (base_decimals/
    quote_decimals) tampoco resolvió nada en producción — CONFIRMADO con
    datos EN VIVO, no con documentación ---

El usuario desplegó el fix de `base_decimals`/`quote_decimals`, reinició la
app dos veces, y los mismos síntomas seguían idénticos. Eso descartó "está
desplegando código viejo" y dejó una sola explicación: la propia
documentación de GRVT (la fuente de la que salió TODA la lógica de escala
de este módulo, arriba) no coincide con lo que la API responde de verdad.
Ya nos había pasado una vez con el nombre del campo de funding rate — esta
vez pasaba con la escala numérica de varios campos a la vez.

Para no seguir adivinando, se añadió diagnóstico (`logger.warning` con el
JSON crudo de `all_instruments` y de los tickers que salían sospechosamente
cerca de cero) y se le pidió al usuario que redesplegara una vez más y
pegara el log. Los datos reales (2026-09-16, instrumentos como AAOI, AAVE,
AMAT, ARB, AMZN, AAPL — acciones tokenizadas y cripto reales de GRVT) fueron
inequívocos:

    "mark_price": "95.586640085"          (AAOI — acción real ~$95)
    "mark_price": "331.868861219"         (AAPL — acción real ~$332)
    "mark_price": "0.150444379"           (ARB — cripto real ~$0.15)
    "open_interest": "3218.0"             (AAVE — cantidad de tokens, no un entero gigante)
    "open_interest": "2287449.6"          (ARB — cantidad de tokens, plausible para un token barato)
    "buy_volume_24h_q": "51208.1677"      (AAOI — ya en USD, un volumen de 24h creíble)

Ningún campo trae un entero de punto fijo — TODOS vienen como el número
decimal humano directo, ya en su unidad final (USD para precios y volumen,
unidades del activo base para open_interest). Cruzando 7 instrumentos
reales de una sola vez (acciones y cripto, precios que van de $0.15 a
$422), cada uno da un valor PLAUSIBLE sin dividir por nada — la prueba más
fuerte que se puede pedir sin abrir la interfaz de GRVT a mano. La
documentación oficial ("expressed in `9` decimals") no significaba "punto
fijo, divide por mil millones" como se asumió en las dos rondas anteriores
— significaba "hasta 9 decimales de precisión en el propio número", y el
ejemplo cacheado del SDK oficial que sí parecía un entero de punto fijo
("59373870996065") era, con esta luz, o bien de una versión distinta de la
API, o bien nunca representó lo que se asumió. Sea como sea, el criterio de
este proyecto es la evidencia en vivo más reciente por encima de cualquier
documentación o ejemplo de SDK, y esta es inequívoca.

**Corrección aplicada (reemplaza TODO lo de las dos rondas anteriores)**:
ninguno de estos tres campos se escala — se usan tal cual, convertidos a
`float`:

    mark_price       = float(mark_price_raw)
    oi_usd           = float(open_interest_raw) * mark_price
    volume_24h_usd   = float(buy_volume_24h_q_raw) + float(sell_volume_24h_q_raw)

`base_decimals`/`quote_decimals` y `PRICE_SCALE` ya NO se usan para nada de
esto (se deja `PRICE_SCALE` como constante sin uso por si algún día aparece
un campo que sí lo necesite, para no perder el nombre). El diagnóstico
(`logger.warning`) SE MANTIENE activo — más vale un log de más que otra
ronda a ciegas si algo vuelve a no cuadrar.

**Funding rate — CONFIRMADO y corregido (2026-09-18)**: la sospecha de
arriba (funding sistemáticamente en "+0.0%" mientras las otras piernas del
mismo par muestran cifras normales) se confirmó con TRES despliegues
independientes, separados varios minutos entre sí, guardando el valor crudo
de `funding_rate_8h_curr` para una muestra de instrumentos:

    AAVE_USDT_Perp:       raw="0.01"   (visto 2 veces)
    ADA_USDT_Perp:        raw="0.01"   (visto 2 veces)
    ANTHROPIC_USDT_Perp:  raw="0.005"  (visto 2 veces)
    ARB_USDT_Perp:        raw="0.01"
    AMAT_USDT_Perp:       raw="0.013"
    AMZN_USDT_Perp:       raw="0.0129"
    AAOI_USDT_Perp:       raw="0.0"    (visto 3 veces)
    AAPL_USDT_Perp:       raw="0.0"    (visto 2 veces)
    AI16Z/AMD/ARM_USDT_Perp: raw="0.0"

Con `× CENTIBEEPS_TO_DECIMAL` (1e-6), AAVE (raw=0.01) da una fracción de
1e-8 → APR ≈0.001%, indistinguible de cero — exactamente lo que se veía en
el ranking. Pero la magnitud de los valores no-cero (0.005 a 0.013) es
justo la de un funding rate expresado YA como porcentaje directo del
periodo de 8h — el mismo patrón que mark_price/open_interest/volumen de
GRVT, que también vinieron "ya humanos" sin ninguna escala oculta (ver
abajo). Con `÷ 100` en vez de `× 1e-6`, esos mismos valores dan: AAVE→
10.95% APR, ADA→10.95%, ARB→10.95%, AMAT→14.235%, AMZN→14.1255%,
ANTHROPIC→5.475% — cifras de funding normales, ni absurdamente altas ni
cerca de cero, consistentes entre sí y con lo que se ve en otros exchanges
para activos similares.

**Fix aplicado**: `funding_rate = float(rate_raw) / 100.0` en vez de
`× CENTIBEEPS_TO_DECIMAL`. `CENTIBEEPS_TO_DECIMAL` se deja definida pero sin
uso (mismo criterio que `PRICE_SCALE` más abajo: no perder el nombre por si
algún día aparece un campo que sí la necesite). El diagnóstico
`funding_rate_raw`/`funding_rate_calculado` SE MANTIENE activo en el log —
si esta interpretación también resultara estar mal, hace falta poder verlo
con el próximo despliegue en vez de descubrirlo a ciegas otra vez. Verificado
con un test que usa los valores EXACTOS de los tres despliegues de arriba.

--- ESTADO ACTUAL CONFIRMADO (2026-09-18) ---

  - `mark_price` / `index_price`: número decimal humano directo, SIN
    escalar. Confirmado con 7 instrumentos reales en producción.
  - `open_interest`: número decimal humano directo (cantidad del activo
    base), SIN escalar — se multiplica por `mark_price` para obtener USD.
    Confirmado igual que arriba.
  - `buy_volume_24h_q` / `sell_volume_24h_q`: número decimal humano directo
    en USD, SIN escalar — se suman ambos. Confirmado igual que arriba.
  - `funding_rate_8h_curr`: número decimal humano directo EXPRESADO COMO
    PORCENTAJE del periodo de 8h — se divide entre 100 para obtener la
    fracción, NO se multiplica por `CENTIBEEPS_TO_DECIMAL`. Confirmado con
    tres despliegues independientes (ver arriba).
  - Intervalo de liquidación (8h) y el nombre del campo de funding rate:
    siguen confirmados de rondas anteriores, sin cambios.
"""

from __future__ import annotations

import json
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
        # Diagnóstico (ver más abajo, tras el bucle de tickers, y el docstring
        # del módulo — sección "BUG REAL, segunda vuelta"): guardamos el JSON
        # crudo de unas pocas filas de all_instruments TAL CUAL, sin filtrar
        # por nombre de campo, para poder ver en los logs si base_decimals/
        # quote_decimals realmente se llaman así en la respuesta real o si
        # (como ya pasó una vez con funding_rate_curr) la documentación no
        # coincide con lo que devuelve la API en producción.
        raw_instrument_samples: list[dict] = []
        for row in instruments:
            name = row.get("instrument")
            if name is None:
                continue
            instrument_names.append(name)
            if len(raw_instrument_samples) < 3 or "BTC" in name:
                if len(raw_instrument_samples) < 6:
                    raw_instrument_samples.append(row)
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

        logger.warning(
            "grvt DIAGNÓSTICO all_instruments: %d instrumentos, %d con base_decimals reconocido, "
            "%d con quote_decimals reconocido. Muestra cruda (hasta 6 filas, JSON tal cual la API, "
            "para comparar el nombre real de los campos contra la documentación): %s",
            len(instrument_names),
            len(base_decimals_by_instrument),
            len(quote_decimals_by_instrument),
            json.dumps(raw_instrument_samples, default=str)[:4000],
        )

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
        # Ver comentario más abajo, dentro del bucle, sobre cuándo se rellena.
        ticker_diagnostic_samples: dict[str, dict] = {}

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
                # `funding_rate` (la documentación dice "expressed in
                # centibeeps", pero ver "CONFIRMADO y corregido" en el
                # docstring del módulo: los valores reales en producción no
                # encajan con esa unidad — encajan con un % humano directo del
                # periodo de 8h, de ahí la división ÷100.0 de abajo, NO
                # ×CENTIBEEPS_TO_DECIMAL). Se prueba el nuevo campo primero y
                # se cae a los antiguos por compatibilidad, en vez de esperar
                # a que GRVT retire el campo deprecated y rompa el conector
                # sin avisar.
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

                # Ver "ESTADO ACTUAL CONFIRMADO" en el docstring del módulo:
                # confirmado con datos EN VIVO (2026-09-16, 7 instrumentos
                # reales) que mark_price/open_interest/buy_volume_24h_q/
                # sell_volume_24h_q vienen YA como el número humano directo —
                # NADA de esto se escala. PRICE_SCALE/base_decimals/
                # quote_decimals dejaron de usarse aquí (dos rondas anteriores
                # de este mismo bug estaban aplicando un divisor que no hacía
                # falta).
                mark_price_raw = ticker.get("mark_price")
                mark_price = float(mark_price_raw) if mark_price_raw is not None else None

                oi_raw = ticker.get("open_interest")
                oi_usd = None
                if oi_raw is not None and mark_price is not None:
                    try:
                        oi_usd = float(oi_raw) * mark_price
                    except (TypeError, ValueError):
                        oi_usd = None

                buy_q_raw = ticker.get("buy_volume_24h_q")
                sell_q_raw = ticker.get("sell_volume_24h_q")
                volume_24h_usd = None
                if buy_q_raw is not None and sell_q_raw is not None:
                    try:
                        volume_24h_usd = float(buy_q_raw) + float(sell_q_raw)
                    except (TypeError, ValueError):
                        volume_24h_usd = None

                base = name.split("_")[0] if "_" in name else name
                interval_hours = interval_by_instrument.get(name, FALLBACK_INTERVAL_HOURS)

                # Diagnóstico (ver el warning consolidado tras el bucle, y la
                # nota "Funding rate — sospecha sin confirmar" en el docstring
                # del módulo): se sigue guardando una muestra acotada, ahora
                # también con el valor crudo/calculado del funding rate, para
                # poder confirmar (o descartar) con un log real si ese campo
                # tiene el mismo problema de escala que ya tuvieron precio/OI/
                # volumen en las dos rondas anteriores — y, de paso, para
                # verificar con el próximo despliegue que este fix sí dio
                # números con sentido (no cerca de cero, no absurdamente
                # grandes) en vez de asumirlo a ciegas otra vez.
                if len(ticker_diagnostic_samples) < 6:
                    ticker_diagnostic_samples[name] = {
                        "mark_price_raw": mark_price_raw,
                        "mark_price_calculado": mark_price,
                        "open_interest_raw": oi_raw,
                        "oi_usd_calculado": oi_usd,
                        "buy_volume_24h_q_raw": buy_q_raw,
                        "sell_volume_24h_q_raw": sell_q_raw,
                        "volume_24h_usd_calculado": volume_24h_usd,
                        "funding_rate_raw": rate_raw,
                        "funding_rate_calculado": float(rate_raw) / 100.0,
                    }

                out.append(
                    FundingRate(
                        exchange="grvt",
                        venue_type=VenueType.DEX,
                        symbol=base,
                        raw_symbol=name,
                        funding_rate=float(rate_raw) / 100.0,
                        interval_hours=interval_hours,
                        mark_price=mark_price,
                        next_funding_time=None,
                        open_interest_usd=oi_usd,
                        volume_24h_usd=volume_24h_usd,
                    )
                )

        if ticker_diagnostic_samples:
            logger.warning(
                "grvt DIAGNÓSTICO ticker (%d instrumentos, muestra tras el fix de escala de "
                "precio/OI/volumen — valores crudos Y calculados, incluido funding_rate_8h_curr "
                "crudo/calculado, para confirmar si ESE campo necesita el mismo tipo de arreglo): %s",
                len(ticker_diagnostic_samples),
                json.dumps(ticker_diagnostic_samples, default=str)[:4000],
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
