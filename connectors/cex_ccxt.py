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

from .base import FundingRate, VenueType
from .cex_htx import htx
from .cex_kucoin import kucoin
from .cex_mexc import mexc

logger = logging.getLogger(__name__)

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
        raw = self._client.fetch_funding_rates()

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
        """
        out: dict[str, float] = {}
        errors: dict[str, str] = {}
        for raw_symbol, mark_price in requests:
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
                # Aproximación estándar (misma que usa el exchange para dar el
                # USD directamente): contratos × precio de marca del momento.
                value = amount * mark_price

            out[raw_symbol] = float(value)

        return out, errors


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
ALL_CEX_FACTORIES = [binance, bybit, okx, bitget, kucoin, gate, mexc, htx]

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
}
