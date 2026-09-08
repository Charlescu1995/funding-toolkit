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

import ccxt

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

# Intervalo de liquidación por defecto de cada CEX, en horas.
# ccxt no siempre expone el intervalo real por símbolo (algunos exchanges lo
# variaron por par en 2024-2025), así que partimos del intervalo "clásico" del
# exchange y lo iremos refinando por símbolo en el Paso 3 (normalización) si
# ccxt trae el dato (`fundingInterval` o similar) en la respuesta cruda.
DEFAULT_INTERVAL_HOURS = {
    "binance": 8,
    "binanceusdm": 8,
    "bybit": 8,
    "okx": 8,
    "bitget": 8,
    "kucoinfutures": 8,
    "gate": 8,
    "mexc": 8,
    "htx": 8,
    # Aster (ver aster() más abajo): arquitectura de DEX, pero ccxt ya lo
    # soporta igual que un CEX (fetch_funding_rates() bulk funciona). 8h es
    # el intervalo "clásico" tipo Binance — Aster expone `fundingIntervalHours`
    # por símbolo en otro endpoint (no en el bulk que usamos aquí), así que
    # esto es la misma simplificación que ya aplicamos al resto: partimos del
    # valor por defecto del exchange, sin refinar por símbolo.
    "aster": 8,
}


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

        interval_default = DEFAULT_INTERVAL_HOURS.get(self.ccxt_id, 8)
        out: list[FundingRate] = []

        for market_symbol, entry in raw.items():
            if not market_symbol.endswith(f":{self.quote}") and f"/{self.quote}" not in market_symbol:
                continue

            rate = entry.get("fundingRate")
            if rate is None:
                continue

            base = entry.get("symbol", market_symbol).split("/")[0]
            next_funding = entry.get("fundingDatetime")

            out.append(
                FundingRate(
                    exchange=self.ccxt_id,
                    venue_type=self.venue_type,
                    symbol=base,
                    raw_symbol=market_symbol,
                    funding_rate=float(rate),
                    interval_hours=interval_default,
                    mark_price=entry.get("markPrice"),
                    next_funding_time=next_funding,
                    open_interest_usd=None,  # ccxt no lo trae en fetch_funding_rates; se añade en Paso 5
                )
            )

            if self.limit and len(out) >= self.limit:
                break

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


def kucoin() -> CexConnector:
    return CexConnector("kucoinfutures")


def gate() -> CexConnector:
    return CexConnector("gate")


def mexc() -> CexConnector:
    return CexConnector("mexc")


def htx() -> CexConnector:
    return CexConnector("htx")


def aster() -> CexConnector:
    # Aster es arquitectónicamente un DEX (su propia "Aster Chain"), pero
    # ccxt ya trae fetch_funding_rates() implementado de verdad para él —
    # comprobado leyendo el código fuente de ccxt (no solo la bandera
    # `has['fetchFundingRates']`, que a veces está mal declarada como en
    # GRVT: ver connectors/dex_grvt.py). Por eso se declara venue_type=DEX
    # aquí en vez de escribir un conector propio como con Lighter/Paradex/etc.
    return CexConnector("aster", venue_type=VenueType.DEX)


ALL_CEX_FACTORIES = [binance, bybit, okx, bitget, kucoin, gate, mexc, htx]

# exchange_name (el mismo que NormalizedRate.exchange) -> factory. Se usa para
# reconstruir un conector concreto cuando hace falta pedir OI Depth solo para
# las oportunidades que ya salieron arriba en el ranking (ver core/opportunities.py).
CEX_FACTORY_BY_NAME = {
    "binanceusdm": binance,
    "bybit": bybit,
    "okx": okx,
    "bitget": bitget,
    "kucoinfutures": kucoin,
    "gate": gate,
    "mexc": mexc,
    "htx": htx,
}
