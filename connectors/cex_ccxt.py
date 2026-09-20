"""
Conector genérico para exchanges centralizados (CEX), usando ccxt.

ccxt ya unifica la mayoría de exchanges grandes (Binance, Bybit, OKX, Bitget,
KuCoin, Gate.io, MEXC, HTX...) bajo la misma interfaz `fetch_funding_rates()`.
Por eso un solo conector nos sirve para todos los CEX: no hace falta escribir
uno distinto por exchange, solo instanciar la clase con el id de ccxt que
corresponda.

Esto es justo el punto fuerte de Loris que queríamos robar: cobertura amplia
de CEX con poco código propio.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import ccxt
import requests

from .base import FundingRate, VenueType
from .cex_htx import htx
from .cex_kucoin import kucoin
from .cex_mexc import mexc

logger = logging.getLogger(__name__)

# Ver CexConnector._fetch_gate_open_interest_usd más abajo — endpoint REST
# propio de gate.io (bypass de ccxt) para el Open Interest en USD.
GATE_CONTRACT_STATS_URL = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats"

# Intervalo de liquidación por defecto de cada CEX, en horas — SOLO se usa
# como fallback cuando ccxt no trae un intervalo real por símbolo en la
# respuesta de fetch_funding_rates() (ver _parse_interval_hours() más abajo,
# arreglado en la auditoría de bugs, Hallazgo #5, 2026-09-19).
#
# CONFIRMADO leyendo el código fuente de ccxt instalado (4.5.76,
# parse_funding_rate() de cada exchange): binance, bybit, okx, gate y aster
# SÍ rellenan una clave 'interval' (string tipo "8h", "4h"...) en cada fila
# de fetch_funding_rates(), calculada por ccxt a partir del dato real del
# exchange (fundingIntervalHours en binance/aster, fundingInterval en
# bybit —desde la metadata de mercado—, la diferencia entre fundingTime/
# nextFundingTime en okx, funding_interval en gate). Cuando esa clave viene
# con un valor válido, se usa DIRECTAMENTE en vez de este diccionario fijo.
#
# bitget es la EXCEPCIÓN confirmada: su fetch_funding_rates() usa por
# defecto el endpoint publicMixGetV2MixMarketTickers, cuya respuesta bulk
# (ver docstring de parse_funding_rate() en bitget.py) NO incluye
# ratePeriod/fundingRateInterval — solo lo trae el endpoint alternativo de
# fetchFundingInterval (publicMixGetV2MixMarketCurrentFundRate), que este
# conector no llama. Así que para bitget 'interval' siempre sale None con
# el código actual y se sigue usando este valor fijo — no es un descuido,
# es el límite real de la llamada que hacemos.
DEFAULT_INTERVAL_HOURS = {
    "binance": 8,
    "binanceusdm": 8,
    "bybit": 8,
    "okx": 8,
    "bitget": 8,
    "gate": 8,
    # kucoinfutures/mexc/htx YA NO usan este diccionario — tienen conector
    # propio (cex_kucoin.py/cex_mexc.py/cex_htx.py) que lee el intervalo real
    # por símbolo de cada API en vez de asumir un valor fijo.
    #
    # Aster (ver aster() más abajo): arquitectura de DEX, pero ccxt ya lo
    # soporta igual que un CEX (fetch_funding_rates() bulk funciona). 8h es
    # el intervalo "clásico" tipo Binance — Aster expone `fundingIntervalHours`
    # por símbolo en otro endpoint (no en el bulk que usamos aquí), así que
    # esto es la misma simplificación que ya aplicamos al resto: partimos del
    # valor por defecto del exchange, sin refinar por símbolo.
    "aster": 8,
    # bingx y phemex (ampliación de CEX, 2026-09-20): comprobado leyendo el
    # código fuente de ccxt instalado (4.5.76) que, a diferencia de
    # binance/bybit/okx/gate/aster, NINGUNO de los dos rellena la clave
    # 'interval' en parse_funding_rate() -- sale None siempre, literal en el
    # código (mismo caso que bitget, ver comentario de arriba). El intervalo
    # de 8h no se ha inventado: viene confirmado por la documentación
    # oficial de cada exchange, leída en vivo con WebFetch --
    #   BingX (bingx.com/en/support/articles/14857605906575): "the standard
    #   settlement interval is 8 hours (occurring 3 times daily) for most
    #   trading pairs", aunque avisan que varía por símbolo para tokens
    #   volátiles (4h/1h) -- ccxt no expone ese dato por símbolo, así que
    #   aquí se aplica el mismo límite ya asumido para bitget: partimos del
    #   valor por defecto sin refinar.
    #   Phemex (phemex.com/help-center/Introduction-to-phemex-futures-funding-rate):
    #   "the default funding settlement interval for Phemex perpetual
    #   futures is 8 hours", con la misma salvedad de que Phemex se reserva
    #   ajustarlo en volatilidad extrema.
    "bingx": 8,
    "phemex": 8,
}


# Ver DEFAULT_INTERVAL_HOURS arriba (Hallazgo #5 de la auditoría,
# 2026-09-19): formato confirmado leyendo ccxt (binance/bybit/okx/gate/aster
# usan siempre un entero + "h" literal, ej. "8h", "4h", "16h", "24h" — nunca
# fracciones de hora). Se parsea con regex en vez de asumir que siempre
# termina en "h" a pelo, por si algún exchange devuelve el número solo.
_INTERVAL_STRING_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*h?\s*$", re.IGNORECASE)


def _parse_interval_hours(raw_interval: object) -> float | None:
    """Convierte el campo 'interval' de ccxt (ej. "8h") a horas (8.0).

    Devuelve None si el campo no vino, no es un string, o no matchea el
    formato esperado — nunca lanza, para no tirar toda la fila por un campo
    que en la práctica es solo un refinamiento sobre el valor por defecto.
    """
    if not isinstance(raw_interval, str):
        return None
    match = _INTERVAL_STRING_RE.match(raw_interval)
    if match is None:
        return None
    try:
        hours = float(match.group(1))
    except (TypeError, ValueError):
        return None
    return hours if hours > 0 else None


class CexConnector:
    """
    Conector genérico basado en ccxt. Un objeto = un exchange de ccxt.

    El nombre es CexConnector por herencia histórica (empezó siendo solo para
    CEX), pero también sirve para DEX que ya están bien soportados por ccxt
    (como Aster) — para esos casos se pasa `venue_type=VenueType.DEX` al
    construirlo, en vez de escribir un conector propio desde cero como
    hicimos con Lighter/Paradex/Extended/Pacifica/edgeX/GRVT.
    """

    def __init__(
        self,
        ccxt_id: str,
        quote: str = "USDT",
        limit: int | None = None,
        venue_type: VenueType = VenueType.CEX,
    ):
        """
        ccxt_id:    id de ccxt para el exchange, ej. "binanceusdm", "bybit", "aster".
        quote:      moneda de cotización a la que restringimos los pares (evita
                    mezclar USDT-margined con COIN-margined en el MVP).
        limit:      número máximo de pares a traer (útil para pruebas rápidas;
                    None = todos).
        venue_type: CEX por defecto; DEX para exchanges como Aster que son
                    arquitectura DEX pero ya están cubiertos por ccxt.
        """
        self.ccxt_id = ccxt_id
        self.name = ccxt_id
        self.quote = quote
        self.limit = limit
        self.venue_type = venue_type
        self._client = getattr(ccxt, ccxt_id)({"enableRateLimit": True})

    def fetch_funding_rates(self) -> list[FundingRate]:
        # Ojo: aquí ya NO se traga la excepción con un try/except silencioso.
        # Antes lo hacía y devolvía [] — lo cual, visto desde fuera, es
        # indistinguible de "este exchange no tiene datos ahora mismo" cuando
        # en realidad puede ser que Binance esté bloqueando la IP del
        # servidor (muy típico en Binance Futures desde IPs de EEUU/cloud).
        # Dejamos que la excepción suba para que quien llame pueda decidir
        # qué hacer con el motivo real del fallo (core/data_service.py lo
        # captura y lo enseña en la interfaz).
        raw = self._fetch_raw_funding_rates()

        # ccxt ya ha cargado (o cacheado) el listado oficial de mercados
        # operables como efecto secundario de fetch_funding_rates() — lo
        # usamos para descartar símbolos "fantasma" (ver más abajo).
        known_markets = self._client.markets

        # Volumen 24h (Paso 6, punto 2): fetch_funding_rates() de ccxt NO trae
        # volumen (solo campos de funding: tasa, mark price, próximo pago) —
        # hace falta una segunda llamada bulk a fetch_tickers(), que sí expone
        # `quoteVolume` (volumen de 24h en la moneda de cotización, USDT en
        # todos los mercados de este proyecto → ya es USD) de forma unificada
        # en ccxt para prácticamente cualquier exchange soportado. Es una
        # llamada bulk (todos los símbolos de golpe), no símbolo a símbolo, así
        # que no pesa nada extra en el rate limit comparado con el fetch
        # principal. Se trata como opcional a propósito (try/except): si un
        # exchange no soporta fetch_tickers() en bulk para derivados, o falla
        # por cualquier motivo, se pierde el volumen para ese exchange pero NO
        # se tira el resto del fetch — el funding rate (el dato principal) ya
        # se obtuvo arriba y no depende de esto.
        volume_by_symbol: dict[str, float] = {}
        try:
            tickers = self._client.fetch_tickers()
            for market_symbol, ticker in tickers.items():
                quote_volume = ticker.get("quoteVolume")
                if quote_volume is not None:
                    volume_by_symbol[market_symbol] = float(quote_volume)
        except Exception as exc:
            logger.warning(
                "%s: no se pudo obtener volumen 24h vía fetch_tickers() (%s: %s) — se sigue "
                "sin volumen para este exchange, el resto del fetch no se ve afectado",
                self.ccxt_id,
                type(exc).__name__,
                exc,
            )

        interval_default = DEFAULT_INTERVAL_HOURS.get(self.ccxt_id, 8)
        out: list[FundingRate] = []
        ghost_symbols: list[str] = []
        # Ver Hallazgo #5 de la auditoría: cuántos símbolos usaron el
        # intervalo real de ccxt frente a los que cayeron al valor fijo de
        # DEFAULT_INTERVAL_HOURS por no venir el campo — diagnóstico simple
        # para confirmar en producción que el refinamiento por símbolo
        # funciona de verdad (o que, como bitget, nunca dispara).
        interval_real_count = 0
        interval_fallback_count = 0

        for market_symbol, entry in raw.items():
            if not market_symbol.endswith(f":{self.quote}") and f"/{self.quote}" not in market_symbol:
                continue

            # Bug real encontrado en producción (Aster/STORJ — ver README):
            # ccxt puede devolver, dentro de fetch_funding_rates(), un
            # symbol_id que NO está en el listado oficial de mercados
            # operables del exchange (`self._client.markets`, cargado por
            # ccxt internamente) — o que SÍ está, pero marcado como inactivo.
            # ccxt incluye en `self.markets` TODOS los símbolos que trae
            # exchangeInfo, tengan o no `status == "TRADING"` — a los que no
            # lo tienen los marca con `active: False` en vez de excluirlos
            # (confirmado leyendo `parse_market()` de ccxt para Aster). Un
            # símbolo así no aparece en el buscador del propio exchange ni es
            # operable — es un resto/símbolo "fantasma" al que ccxt igual le
            # sintetiza una tasa de funding. No depender de Open Interest
            # para detectarlo (algunos exchanges, como Aster, ni siquiera lo
            # soportan vía ccxt): se descarta aquí mismo, en el origen, tanto
            # si falta del listado como si está pero inactivo.
            market_info = known_markets.get(market_symbol) if known_markets else None
            is_ghost = known_markets and (market_info is None or market_info.get("active") is False)
            if is_ghost:
                ghost_symbols.append(market_symbol)
                continue

            rate = entry.get("fundingRate")
            if rate is None:
                continue

            base = entry.get("symbol", market_symbol).split("/")[0]

            # Ver Hallazgo #5 de la auditoría (2026-09-19): usar el
            # intervalo real por símbolo que ccxt ya calcula (campo
            # 'interval', ej. "8h") cuando viene, en vez de asumir siempre
            # el valor fijo del exchange — confirmado leyendo el código
            # fuente de ccxt que binance/bybit/okx/gate/aster SÍ lo
            # rellenan con el dato real (bitget no, ver DEFAULT_INTERVAL_HOURS).
            interval_real = _parse_interval_hours(entry.get("interval"))
            if interval_real is not None:
                interval_hours = interval_real
                interval_real_count += 1
            else:
                interval_hours = interval_default
                interval_fallback_count += 1

            # Ver Hallazgo #4 de la auditoría (2026-09-19): FundingRate.next_funding_time
            # está tipado Optional[datetime] (connectors/base.py) pero aquí se
            # guardaba el string ISO8601 crudo de ccxt (fundingDatetime) tal
            # cual, sin convertir — inerte hoy (ningún consumidor del
            # pipeline lee este campo, ni siquiera core/normalize.py lo
            # traslada a NormalizedRate, confirmado con grep), pero un campo
            # mal tipado es un bug latente si algún día se usa. Se convierte
            # con el propio parser ISO8601 de ccxt (Exchange.parse8601, el
            # mismo que usa internamente para construir 'fundingDatetime' a
            # partir del timestamp en ms) para no reinventar el parseo.
            next_funding_raw = entry.get("fundingDatetime")
            next_funding_time = None
            if next_funding_raw is not None:
                try:
                    next_funding_ms = self._client.parse8601(next_funding_raw)
                except (TypeError, ValueError):
                    next_funding_ms = None
                if next_funding_ms is not None:
                    next_funding_time = datetime.fromtimestamp(
                        next_funding_ms / 1000, tz=timezone.utc
                    )

            out.append(
                FundingRate(
                    exchange=self.ccxt_id,
                    venue_type=self.venue_type,
                    symbol=base,
                    raw_symbol=market_symbol,
                    funding_rate=float(rate),
                    interval_hours=interval_hours,
                    mark_price=entry.get("markPrice"),
                    next_funding_time=next_funding_time,
                    open_interest_usd=None,  # ccxt no lo trae en fetch_funding_rates; se añade en Paso 5
                    volume_24h_usd=volume_by_symbol.get(market_symbol),
                )
            )

            if self.limit and len(out) >= self.limit:
                break

        if ghost_symbols:
            logger.warning(
                "%s: %d símbolo(s) descartado(s) por no estar en el listado oficial de "
                "mercados operables (fantasma/delistado, ver README): %s",
                self.ccxt_id,
                len(ghost_symbols),
                ghost_symbols[:10],
            )

        if interval_real_count or interval_fallback_count:
            logger.info(
                "%s: intervalo real de ccxt usado en %d/%d símbolos (Hallazgo #5 de la "
                "auditoría); %d cayeron al valor fijo DEFAULT_INTERVAL_HOURS=%sh por no "
                "traer el campo 'interval' (normal en bitget, ver docstring del módulo)",
                self.ccxt_id,
                interval_real_count,
                interval_real_count + interval_fallback_count,
                interval_fallback_count,
                interval_default,
            )

        return out

    def _fetch_raw_funding_rates(self) -> dict[str, dict]:
        """
        La inmensa mayoría de exchanges soportan fetch_funding_rates() bulk
        de ccxt directamente (confirmado con `.has['fetchFundingRates']`).
        Phemex es la única excepción entre los CEX que usan esta clase
        genérica (ampliación de CEX, 2026-09-20:
        `ccxt.phemex().has['fetchFundingRates']` es `False`) -- ver
        _fetch_phemex_funding_rates_bulk() más abajo para el bypass.

        BingX SÍ soporta fetch_funding_rates() bulk de verdad (`.has` en
        `True`, confirmado en vivo) -- no necesita entrar aquí, cae directo
        al caso general.
        """
        if self.ccxt_id == "phemex":
            return self._fetch_phemex_funding_rates_bulk()
        return self._client.fetch_funding_rates()

    def _fetch_phemex_funding_rates_bulk(self) -> dict[str, dict]:
        """
        Bypass para Phemex (ampliación de CEX, 2026-09-20, confirmado leyendo
        el código fuente de ccxt instalado 4.5.76 -- sin acceso a red al
        exchange desde este sandbox, igual que el resto de investigación de
        este proyecto, ver nota en cex_kucoin.py): `ccxt.phemex().has
        ['fetchFundingRates']` es `False` -- no hay bulk implementado para
        este exchange (a diferencia de `fetchFundingRate()` singular, que sí
        existe pero supondría una llamada de red por símbolo, incompatible
        con el patrón de este proyecto de "scan barato del universo
        completo" -- ver docstring de fetch_open_interest_usd()).

        Sin embargo, `Exchange.fetch_tickers()` de Phemex (bulk de verdad,
        `.has['fetchTickers']` en `True`) usa internamente, para swap lineal
        (USDT-margined), el método implícito de bajo nivel
        `v2GetMdV2Ticker24hrAll` -- confirmado leyendo
        `inspect.getsource(ccxt.phemex().fetch_tickers)`. No se puede
        reutilizar fetch_tickers() tal cual porque parse_tickers()/
        parse_ticker() no exponen el funding rate en el ticker unificado (se
        pierde por el camino) -- pero cada fila cruda de la respuesta
        ("result") tiene la MISMA forma que espera
        `Exchange.parse_funding_rate()` (confirmado leyendo su código
        fuente: los ejemplos del propio docstring de parse_funding_rate
        muestran literalmente las claves fundingRateRr/markPriceRp/symbol
        de esta respuesta), así que se llama al endpoint implícito
        directamente y se reutiliza el parser real de ccxt en vez de
        escribir uno propio desde cero.

        Solo el endpoint LINEAR (v2, USDT-margined) -- se excluye a
        propósito el endpoint INVERSE/USD-margined
        (`v1GetMdTicker24hrAll`, contratos con sufijo Ep/Er de precisión
        escalada) siguiendo el mismo criterio ya aplicado en este proyecto
        para MEXC (Hallazgo #12 de la auditoría, ver cex_mexc.py): mezclar
        contratos coin-margined descuadra la fórmula de Open Interest/
        tamaño de posición del resto del pipeline, que asume USDT-margined
        en todas partes. Como `self.quote` es siempre "USDT" en este
        proyecto, el endpoint linear ya trae exactamente el universo que
        interesa -- no hace falta ni llamar al endpoint inverse para
        descartarlo después (y el filtro por `self.quote` de
        fetch_funding_rates() lo descartaría de todas formas, así que
        llamarlo sería una llamada de red desperdiciada).

        Devuelve un dict {símbolo_unificado: fila_parseada} con la MISMA
        forma que devuelve `self._client.fetch_funding_rates()` para
        cualquier otro exchange -- así el resto de fetch_funding_rates() de
        más arriba no necesita saber que Phemex es un caso especial.
        """
        self._client.load_markets()
        response = self._client.v2GetMdV2Ticker24hrAll({})
        rows = response.get("result") or []
        out: dict[str, dict] = {}
        skipped = 0
        for row in rows:
            try:
                parsed = self._client.parse_funding_rate(row)
            except Exception as exc:
                skipped += 1
                logger.warning(
                    "phemex: fila descartada al parsear funding rate (%s: %s) -- symbol crudo=%s",
                    type(exc).__name__,
                    exc,
                    row.get("symbol"),
                )
                continue
            symbol = parsed.get("symbol")
            if not symbol:
                skipped += 1
                continue
            out[symbol] = parsed

        if skipped:
            logger.warning(
                "phemex: %d fila(s) del endpoint bulk v2GetMdV2Ticker24hrAll descartada(s) "
                "por no poder resolver un símbolo unificado (de %d totales)",
                skipped,
                len(rows),
            )

        return out

    def fetch_open_interest_usd(
        self, requests: list[tuple[str, float | None]]
    ) -> tuple[dict[str, float], dict[str, str]]:
        """
        Trae el open interest (en USD) para una lista concreta de símbolos.

        Deliberadamente NO se llama para los miles de pares de golpe — ccxt no
        siempre expone un endpoint "bulk" de open interest, y pedirlo símbolo
        a símbolo para todo el universo sería lento y quemaría el rate limit.
        Se usa solo sobre el puñado de oportunidades que ya nos interesan
        (las que salen arriba en el ranking), que es cuando de verdad hace
        falta saber la profundidad.

        `requests` es una lista de (raw_symbol, mark_price) — el mark_price
        viene del fetch de funding rates y es el fallback para exchanges como
        bitget: ccxt les responde `openInterestAmount` (contratos) pero no
        `openInterestValue` (USD). En vez de dejarlo sin resolver, se calcula
        contratos × precio de marca — es justo el cálculo que haría el propio
        exchange para dar el USD directamente.

        Devuelve (oi_por_símbolo, errores_por_símbolo). Igual que con
        fetch_funding_rates(): no nos tragamos la excepción en silencio — la
        exponemos por símbolo para que se pueda ver en la interfaz POR QUÉ un
        exchange concreto no da profundidad (ccxt sin soportar el endpoint
        para ese exchange, símbolo mal formado, rate limit...), en vez de un
        "—" mudo que no dice si es un fallo real o simplemente no hay dato.

        Nota sobre `mark_price == 0` (RESUELTO 2026-09-19, auditoría de bugs,
        Hallazgo #13 — severidad baja; la propia auditoría citó las líneas
        233-244 de este archivo para este hallazgo, pero ese rango es en
        realidad el fix del Hallazgo #4/#5 [next_funding_time/interval] —
        el hueco real de mark_price==0 siempre estuvo aquí, en el fallback
        contratos×mark_price, no en fetch_funding_rates()): antes de este
        fix, un mark_price de 0 pasaba el chequeo `is None` y daba
        value=0.0 en silencio. Se trata ahora como un error explícito por
        símbolo (mismo criterio que cex_kucoin.py/cex_mexc.py), en vez de
        publicar una profundidad cero para un mercado que en realidad no
        se pudo calcular.
        """
        out: dict[str, float] = {}
        errors: dict[str, str] = {}
        for raw_symbol, mark_price in requests:
            # Ver _fetch_gate_open_interest_usd — gate es un caso especial
            # descubierto verificando este mismo export CSV (2026-09-19):
            # ccxt.gate().has['fetchOpenInterest'] es False (mismo hueco que
            # Aster, ver README), así que fetch_open_interest() de ccxt
            # SIEMPRE lanza NotSupported para gate, sea cual sea el símbolo
            # — nunca es un fallo puntual. A diferencia de Aster (donde el
            # bypass REST directo también falló, 400 en BTCUSDT), el REST
            # propio de gate SÍ expone el Open Interest ya resuelto en USD.
            if self.ccxt_id == "gate":
                try:
                    out[raw_symbol] = self._fetch_gate_open_interest_usd(raw_symbol)
                except Exception as exc:
                    errors[raw_symbol] = f"{type(exc).__name__}: {exc}"
                continue

            try:
                info = self._client.fetch_open_interest(raw_symbol)
            except Exception as exc:
                errors[raw_symbol] = f"{type(exc).__name__}: {exc}"
                continue

            value = info.get("openInterestValue")
            if value is None:
                amount = info.get("openInterestAmount")
                if amount is None:
                    errors[raw_symbol] = "sin openInterestValue ni openInterestAmount en la respuesta"
                    continue
                if mark_price is None:
                    errors[raw_symbol] = "solo trae openInterestAmount (sin USD) y no hay mark price para estimarlo"
                    continue
                # Ver Hallazgo #13 de la auditoría (2026-09-19, severidad
                # baja): un mark_price de 0 explícito habría pasado el
                # chequeo `is None` de arriba y dado value=0.0 -- un
                # mercado real mostrado como si tuviera profundidad cero.
                # No es plausible para un contrato activo, así que se
                # trata como si no hubiera mark price (mismo criterio que
                # cex_kucoin.py/cex_mexc.py).
                if mark_price == 0:
                    errors[raw_symbol] = (
                        f"mark_price recibido es 0 (DIAGNÓSTICO Hallazgo #13) -- no se "
                        f"calcula openInterestAmount({amount}) * 0"
                    )
                    continue
                # Aproximación estándar (misma que usa el exchange para dar el
                # USD directamente): contratos × precio de marca del momento.
                value = amount * mark_price

            out[raw_symbol] = float(value)

        return out, errors

    def _fetch_gate_open_interest_usd(self, raw_symbol: str) -> float:
        """
        Bypass de ccxt para el Open Interest de gate (CONFIRMADO en vivo,
        2026-09-19, verificando por qué el export CSV del Ranking nunca
        traía OI para ninguna pierna en `gate` pese a estar en
        CEX_FACTORY_BY_NAME): `ccxt.gate().has['fetchOpenInterest']` es
        `False` — no es un fallo intermitente ni un símbolo concreto, ccxt
        genuinamente no lo implementa para este exchange (mismo hueco que
        Aster, ver README). `fetch_open_interest_usd()` de arriba llamaría
        a `self._client.fetch_open_interest()`, que para gate SIEMPRE
        lanza `NotSupported`.

        A diferencia de Aster (donde el bypass REST directo también se
        probó y falló con 400 incluso en BTCUSDT — ver README), el REST
        propio de gate SÍ expone el Open Interest ya resuelto en USD:
        `GET /api/v4/futures/usdt/contract_stats?contract=<id>&interval=5m&limit=1`
        confirmado en vivo contra EMBER_USDT devolviendo
        `open_interest_usd=185664.336` (y de paso, `last_funding_rate:
        "-0.02"` — el mismo -2%/hora tope que ya se veía capado a
        -17.520% de APR en ese mismo export CSV, confirmación cruzada de
        que este endpoint es el dato real del mismo contrato).

        `raw_symbol` llega en formato unificado de ccxt (ej.
        "EMBER/USDT:USDT"), pero el endpoint de gate quiere su id nativo
        con guion bajo (ej. "EMBER_USDT", el campo `name` crudo de la API
        de gate — confirmado leyendo `parse_contract_market()` en el
        propio código fuente de ccxt, que asigna `market['id'] = name` sin
        transformar). Se resuelve con `self._client.market(raw_symbol)['id']`.

        CORREGIDO (2026-09-20, confirmado en vivo con el panel de
        diagnóstico de la app en producción): la suposición original de
        que `self._client.markets` ya estaría cacheado tras un
        `fetch_funding_rates()` previo era FALSA para esta ruta de
        llamada en concreto. `core/opportunities.py::fetch_oi_for_targets()`
        no reutiliza el conector que trajo los funding rates -- construye
        uno NUEVO desde cero solo para pedir OI (`factory()`, ver
        `CEX_FACTORY_BY_NAME`), así que su `ccxt.gate()` interno nunca
        había llamado a `load_markets()`. El resultado en producción fue
        el error real capturado en el panel "OI Depth no disponible":
        `ExchangeError: gate markets not loaded` -- el mismo error que
        lanza ccxt internamente cuando `Exchange.market()` se llama con
        `self.markets` vacío. Se corrige llamando a
        `self._client.load_markets()` antes de `self._client.market()`.
        No añade una llamada de red por símbolo: `load_markets(reload=False)`
        (el valor por defecto) es idempotente -- comprobado leyendo
        `Exchange.load_markets()` en el propio ccxt instalado, que
        devuelve `self.markets` directamente si ya está poblado, sin
        volver a pedir nada a la red. Solo la primera llamada dentro de
        este batch de OI hace la petición real; el resto de símbolos de
        gate en el mismo ciclo la reutilizan gratis.
        """
        self._client.load_markets()
        contract_id = self._client.market(raw_symbol)["id"]
        resp = requests.get(
            GATE_CONTRACT_STATS_URL,
            params={"contract": contract_id, "interval": "5m", "limit": 1},
            timeout=10.0,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            raise RuntimeError(f"gate contract_stats sin filas para {contract_id}")
        value = rows[0].get("open_interest_usd")
        if value is None:
            raise RuntimeError(f"gate contract_stats sin open_interest_usd para {contract_id}")
        return float(value)


def binance() -> CexConnector:
    return CexConnector("binanceusdm")


def bybit() -> CexConnector:
    return CexConnector("bybit")


def okx() -> CexConnector:
    return CexConnector("okx")


def bitget() -> CexConnector:
    return CexConnector("bitget")


def gate() -> CexConnector:
    return CexConnector("gate")


def bingx() -> CexConnector:
    # Ampliación de CEX, 2026-09-20 (uno de los 3 CEX que tenía Loris y
    # nosotros no -- ver comparativa-competidores.md del proyecto KUSI).
    # Confirmado en vivo (sin red al exchange, vía ccxt instalado):
    # ccxt.bingx().has['fetchFundingRates'] es True -- soporta el bulk
    # directamente, no hace falta ningún bypass (a diferencia de phemex más
    # abajo). El Open Interest del top N también cae en el caso genérico de
    # fetch_open_interest_usd(): ccxt.bingx().has['fetchOpenInterest'] es
    # True y, para swap lineal (USDT-margined, el único que nos interesa
    # aquí -- ver quote="USDT"), parse_open_interest() de ccxt YA rellena
    # openInterestValue directamente en USD (confirmado leyendo su código
    # fuente) -- no necesita el fallback contratos×mark_price.
    return CexConnector("bingx")


def phemex() -> CexConnector:
    # Ampliación de CEX, 2026-09-20 (los otros 2 de los 3 CEX de Loris que
    # nos faltaban -- ver comparativa-competidores.md del proyecto KUSI).
    # A diferencia de bingx, Phemex SÍ necesita un bypass para el bulk de
    # funding rates -- ver CexConnector._fetch_phemex_funding_rates_bulk()
    # para el porqué y la evidencia completa. El Open Interest del top N sí
    # cae en el caso genérico de fetch_open_interest_usd() sin bypass
    # propio: ccxt.phemex().has['fetchOpenInterest'] es True, y aunque su
    # parse_open_interest() deja openInterestValue siempre en None
    # (confirmado leyendo el código fuente), el fallback ya existente de
    # fetch_open_interest_usd() (contratos × mark_price, el mismo que ya se
    # usa para bitget) lo resuelve sin código nuevo: openInterestAmount
    # viene en la moneda base (ej. BTC) y mark_price en la de cotización
    # (USDT), el producto da el USD real.
    return CexConnector("phemex")


def aster() -> CexConnector:
    # Aster es arquitectónicamente un DEX (su propia "Aster Chain"), pero
    # ccxt ya trae fetch_funding_rates() implementado de verdad para él —
    # comprobado leyendo el código fuente de ccxt (no solo la bandera
    # `has['fetchFundingRates']`, que a veces está mal declarada como en
    # GRVT: ver connectors/dex_grvt.py). Por eso se declara venue_type=DEX
    # aquí en vez de escribir un conector propio como con Lighter/Paradex/etc.
    return CexConnector("aster", venue_type=VenueType.DEX)


# kucoinfutures/mexc/htx YA NO son CexConnector(ccxt_id=...): ccxt no tiene
# implementado fetch_funding_rates() para ninguno de los tres (lanza
# NotSupported, comprobado leyendo su código fuente) — se sustituyeron por
# conectores propios (connectors/cex_kucoin.py, cex_mexc.py, cex_htx.py, cada
# uno con su propia investigación documentada en su docstring), importados
# arriba y reexportados aquí para que cli.py/core/data_service.py sigan sin
# tener que saber qué exchanges usan ccxt y cuáles no — mismo patrón que ya
# se sigue con el resto de conectores propios del proyecto (edgeX, GRVT...).
ALL_CEX_FACTORIES = [binance, bybit, okx, bitget, kucoin, gate, mexc, htx, bingx, phemex]

# exchange_name (el mismo que NormalizedRate.exchange) -> factory. Se usa para
# reconstruir un conector concreto cuando hace falta pedir OI Depth solo para
# las oportunidades que ya salieron arriba en el ranking (ver core/opportunities.py).
#
# kucoinfutures/mexc/htx NO están aquí (aunque SÍ están en ALL_CEX_FACTORIES):
# esta tabla es solo para exchanges cuyo fetch_funding_rates() masivo NO trae
# OI y por tanto necesitan una llamada aparte símbolo a símbolo
# (fetch_open_interest_usd(), método propio de CexConnector/ccxt). Los tres
# conectores propios de KuCoin/MEXC/HTX (ver cex_kucoin.py/cex_mexc.py/
# cex_htx.py) ya traen open_interest_usd resuelto en el fetch masivo —
# mismo motivo por el que Backpack/Nado/Hibachi tampoco están aquí.
CEX_FACTORY_BY_NAME = {
    "binanceusdm": binance,
    "bybit": bybit,
    "okx": okx,
    "bitget": bitget,
    "gate": gate,
    # Aster está aquí a propósito aunque se declare venue_type=DEX arriba: la
    # clave de este diccionario es "¿este exchange usa CexConnector (ccxt) y
    # por tanto necesita un fetch_open_interest() aparte, símbolo a símbolo,
    # porque su fetch_funding_rates() masivo no trae OI?" — no "¿es un CEX?".
    # Aster es un DEX que, en la práctica, se comporta exactamente como un
    # CEX en este sentido (ver la clase CexConnector). Sin esta entrada,
    # collect_oi_targets()/fetch_oi_for_targets() en core/opportunities.py
    # nunca llegan a pedirle el OI real a Aster — es justo el bug que dejaba
    # colarse en el ranking mercados "fantasma" de Aster con Open Interest
    # real $0 pero nunca CONFIRMADO como tal (se quedaba en "—", no en "$0"),
    # así que has_dead_liquidity() no los descartaba. Ver README.
    "aster": aster,
    # bingx/phemex (ampliación de CEX, 2026-09-20): igual que el resto de
    # esta tabla, su fetch_funding_rates() masivo no trae Open Interest, así
    # que necesitan la llamada aparte símbolo a símbolo de
    # fetch_open_interest_usd() para el top N del ranking -- ver bingx()/
    # phemex() más arriba para la evidencia de que caen en el caso genérico
    # sin bypass propio.
    "bingx": bingx,
    "phemex": phemex,
}
