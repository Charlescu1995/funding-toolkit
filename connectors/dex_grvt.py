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
respondieron 200 OK), pero NINGUNA trajo un ticker reconocible. Eso descarta
un problema de red/autenticación y apunta a un desajuste de forma: o bien el
body `{"instrument": "<nombre>"}` no es el que espera `/full/v1/ticker`, o la
respuesta no trae el resultado bajo la clave `"result"`, o no se llama
`funding_rate_curr`. Como esta API es POST (WebFetch solo hace GET) y el
sandbox de desarrollo no tiene salida a exchanges, no se ha podido confirmar
cuál de las tres es. En vez de seguir adivinando a ciegas, el conector ahora
guarda una muestra del JSON crudo de la respuesta cuando esto vuelve a pasar
(ver `unparsed_samples` en `fetch_funding_rates`) y la mete en el mensaje de
error — así el PRÓXIMO despliegue va a enseñar, directamente en el banner de
la interfaz, la forma real de la respuesta de GRVT, sin necesitar otra ronda
de "prueba y build".
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

                rate_raw = ticker.get("funding_rate_curr")
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

                oi_raw = ticker.get("open_interest")
                oi_usd = None
                if oi_raw is not None and mark_price is not None:
                    try:
                        # Ver nota de escala en el docstring del módulo: se
                        # asume el mismo punto fijo ÷ 1e9 que el resto de
                        # campos numéricos de GRVT, sin confirmar en vivo.
                        oi_base_units = float(oi_raw) / PRICE_SCALE
                        oi_usd = oi_base_units * mark_price
                    except (TypeError, ValueError):
                        oi_usd = None

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
