"""
Conector para KuCoin Futures — CEX de derivados.

No soportado por ccxt: `ccxt.kucoinfutures.fetch_funding_rates()` lanza
`NotSupported` (comprobado leyendo el código fuente de ccxt, no solo la
bandera `has[...]`) — por eso este exchange necesita conector propio, igual
que ya pasó con los DEX que no estaban en ccxt (edgeX, GRVT...).

Un único endpoint público trae TODO de golpe: metadata del contrato, funding
rate vigente, mark price, index price y open interest — a diferencia de la
mayoría de los otros CEX (que necesitan ccxt precisamente porque no existe
un endpoint tan completo), aquí ni siquiera hace falta una segunda llamada
ni pool de hilos.

    GET https://api-futures.kucoin.com/api/v1/contracts/active

Docs: https://www.kucoin.com/docs/rest/futures-trading/market-data/get-all-tickers

Nota sobre el entorno de investigación: este endpoint concreto está en la
lista de hosts bloqueados por la política de red de ESTE sandbox de
desarrollo (`curl` directo da 403 del propio proxy de egress, no del
exchange — confirmado vía `/__agentproxy/status`), así que toda la
investigación de este conector se hizo con WebFetch en vez de con peticiones
directas, pero SIGUE siendo contra la API en vivo real, no solo contra
documentación — no debería fallar por esto en Streamlit Cloud, que no tiene
esta restricción.

--- Confirmado en vivo (WebFetch directo) ---

Ejemplo real, contrato XBTUSDTM (BTC-USDT perpetuo):

    {
      "symbol": "XBTUSDTM", "baseCurrency": "XBT", "quoteCurrency": "USDT",
      "settleCurrency": "USDT",
      "fundingFeeRate": 3.6E-5, "fundingRateGranularity": 28800000,
      "markPrice": 77925.3, "indexPrice": 77954.35,
      "openInterest": "10419591", "multiplier": 0.001,
      "status": "Open", ...
    }

--- Nota sobre "XBT" en vez de "BTC" (bug real de cruce de símbolos, no una
    curiosidad) ---

KuCoin Futures usa la nomenclatura heredada "XBT" para Bitcoin en
`baseCurrency` (igual que Kraken) — ningún otro exchange de este proyecto usa
ese nombre para BTC. Sin corregirlo, KuCoin aparecería con un activo "XBT"
separado de "BTC" en el resto de exchanges y nunca cruzaría en el ranking.
Se normaliza con un diccionario de alias explícito (`_SYMBOL_ALIASES`), no
con una heurística — es el único caso conocido en todo el universo de
contratos de KuCoin, no hace falta nada más genérico.

--- Nota sobre `fundingFeeRate` (CONFIRMADO en vivo, tasa cruda del
    intervalo) ---

fundingFeeRate=3.6E-5 con fundingRateGranularity=28800000ms (=8h) da un APR
de ≈3.94% — una cifra normal, confirma que es la tasa cruda del intervalo de
8h, no algo ya anualizado.

    interval_hours = fundingRateGranularity / 1000 / 3600

--- Nota sobre `openInterest` (CONFIRMADO en vivo, contrastado con mark
    price y multiplier) ---

`openInterest` viene en NÚMERO DE CONTRATOS (string), no en el activo base ni
en USD directamente. Cada contrato de KuCoin representa `multiplier`
unidades del activo base (ej. 0.001 XBT/contrato para XBTUSDTM) — aplicando
la conversión da un notional de orden de magnitud plausible (~$812M para
BTC, coherente con un exchange grande):

    open_interest_usd = float(openInterest) * multiplier * markPrice

--- Nota sobre `status` (solo un valor visto en vivo, mismo criterio de
    precaución que el resto del proyecto) ---

Solo se ha observado "Open" en el contrato de mayor volumen consultado — se
acepta únicamente ese valor exacto, igual que con `orderBookState` en
Backpack o `trading_status` en Nado, por si existen otros estados (pausado,
deslistado...) no vistos todavía en este único ejemplo.

--- Nota sobre `openInterest` NEGATIVO (bug real en producción, 2026-09-19,
    símbolo SOL — investigación abierta, todavía sin causa raíz confirmada)
    ---

El usuario reportó, dos veces en la misma sesión de trabajo (con ~10-15
minutos de diferencia), que SOLUSDTM daba un `open_interest_usd` calculado
imposible: −$616.481.571 la primera vez, −$618.445.974,96 la segunda —
mismo orden de magnitud, pero NO el mismo valor exacto, así que no es un
dato congelado/cacheado, es corrupción real y recurrente en vivo. En ambos
casos el valor negativo acabó descartando la oportunidad del Ranking por
pura coincidencia (el piso de liquidez mínima — ver más abajo en el README
— descarta cualquier valor por debajo de $1.000, y cualquier negativo
cumple eso trivialmente), pero eso tapaba el síntoma sin explicar la causa.

Investigado en vivo (WebFetch, mismo método que el resto de este
conector): en el momento de la investigación SOLUSDTM venía sano
(`openInterest="12632439"`, `multiplier=0.1`, `markPrice=113.765` →
~$143,7M positivo) — la anomalía no es permanente, no se pudo reproducir
a demanda. Se intentó también un escaneo de TODO el array buscando
multiplicador/precio negativo en cualquier otro contrato, pero salió
metodológicamente poco fiable: WebFetch resumió/truncó el array completo
(confirmado pidiéndole que contara el total: devolvió 36 símbolos de los
varios cientos que tiene `/contracts/active` en realidad) — así que un
"no se encontró ningún negativo" de ese escaneo NO es evidencia real de
nada y se descartó como conclusión.

`kucoinfutures` NO está en `CEX_FACTORY_BY_NAME` (ver
`connectors/cex_ccxt.py`), así que este valor no pasa por ningún
enriquecimiento aparte — sale tal cual de la fórmula de abajo
(`openInterest × multiplier × markPrice`) a partir del mismo payload del
fetch masivo, sin caché ni historial de por medio. Para que el producto dé
negativo, al menos uno de esos tres factores tuvo que llegar YA negativo
desde KuCoin en esa consulta concreta — no hay forma de que nuestro propio
parseo (conversiones `float()` directas) invierta un signo.

**Mientras no se capture el payload crudo en el momento exacto en que
esto vuelve a pasar, no hay evidencia suficiente para señalar cuál de los
tres factores es el culpable** — así que, siguiendo el mismo criterio de
este proyecto (nunca inventar una causa sin datos reales), NO se intentó
arreglar el campo concreto. En su lugar: (1) se añadió una guardia que
descarta a `None` cualquier `open_interest_usd` calculado negativo, en vez
de dejarlo pasar y depender de la coincidencia del piso de liquidez, y (2)
se guarda el payload crudo completo (`openInterest`, `multiplier`,
`markPrice`, tal cual llegaron) de cualquier contrato que dispare esto, en
un `logger.warning` — para que la PRÓXIMA vez que se repita (y ya van
dos), el log de despliegue real traiga por fin los tres valores crudos y
se pueda cerrar esto con una causa confirmada en vez de una guardia
genérica.

--- Nota sobre `turnoverOf24h` (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El mismo objeto ya trae el volumen de 24h directamente en USD, sin hacer
falta ninguna llamada aparte ni conversión: `turnoverOf24h` (ej. XBTUSDTM:
3.701053670146E8 ≈ $370.1M) es el turnover en la moneda de cotización
(USDT), a diferencia de `volumeOf24h` (4724.686), que viene en unidades del
activo base (XBT) y por tanto necesitaría multiplicarse por el precio —
`turnoverOf24h` ya hace ese trabajo, así que se usa tal cual.
"""

from __future__ import annotations

import json
import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://api-futures.kucoin.com"
CONTRACTS_URL = f"{BASE_URL}/api/v1/contracts/active"

MS_PER_HOUR = 1000.0 * 3600.0

# Ver docstring: único alias conocido en todo el universo de contratos de
# KuCoin Futures — no hace falta nada más genérico que esto.
_SYMBOL_ALIASES = {"XBT": "BTC"}


class KucoinConnector:
    name = "kucoinfutures"
    venue_type = VenueType.CEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(CONTRACTS_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()

        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(
                "kucoinfutures: /api/v1/contracts/active no devolvió una lista en 'data' — "
                f"claves de nivel superior recibidas: "
                f"{list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}"
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}
        # Ver docstring más abajo, sección "Nota sobre openInterest negativo":
        # bug real visto en producción (2026-09-19, símbolo SOL) — el propio
        # KuCoin devolvió, al menos dos veces, un openInterest*multiplier*
        # markPrice negativo (físicamente imposible). No tenemos capturado
        # todavía CUÁL de los tres factores viene mal, así que en vez de
        # adivinarlo se guarda el payload crudo completo de cualquier
        # contrato que dé negativo, para verlo en el próximo log real.
        oi_anomaly_samples: dict[str, dict] = {}

        for row in rows:
            if not isinstance(row, dict):
                skipped[str(row)] = "fila no es un objeto (formato inesperado)"
                continue

            raw_symbol = row.get("symbol")
            if raw_symbol is None:
                continue

            # Ver docstring: solo se acepta el estado confirmado en vivo.
            if row.get("status") != "Open":
                continue

            rate_raw = row.get("fundingFeeRate")
            granularity_raw = row.get("fundingRateGranularity")
            if rate_raw is None or granularity_raw is None:
                skipped[raw_symbol] = "faltan campos (fundingFeeRate/fundingRateGranularity)"
                continue

            try:
                rate = float(rate_raw)
                interval_hours = float(granularity_raw) / MS_PER_HOUR
            except (TypeError, ValueError) as exc:
                skipped[raw_symbol] = f"valor no numérico: {exc}"
                continue

            if interval_hours <= 0:
                skipped[raw_symbol] = f"fundingRateGranularity inválido ({granularity_raw})"
                continue

            mark_price_raw = row.get("markPrice")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            oi_contracts_raw = row.get("openInterest")
            multiplier_raw = row.get("multiplier")
            open_interest_usd = None
            if oi_contracts_raw is not None and multiplier_raw is not None and mark_price is not None:
                try:
                    # Ver docstring: openInterest viene en Nº de contratos.
                    open_interest_usd = float(oi_contracts_raw) * float(multiplier_raw) * mark_price
                except (TypeError, ValueError):
                    open_interest_usd = None

                # Ver docstring, "Nota sobre openInterest negativo (bug real,
                # 2026-09-19)": un OI negativo es físicamente imposible — no
                # se propaga tal cual (se descarta a None, igual que si no
                # se hubiera podido calcular), y se guarda el payload crudo
                # de los tres factores para diagnosticar cuál viene mal.
                if open_interest_usd is not None and open_interest_usd < 0:
                    oi_anomaly_samples[raw_symbol] = {
                        "openInterest_raw": oi_contracts_raw,
                        "multiplier_raw": multiplier_raw,
                        "markPrice_raw": mark_price_raw,
                        "mark_price_calculado": mark_price,
                        "open_interest_usd_calculado_DESCARTADO": open_interest_usd,
                    }
                    open_interest_usd = None

            base_currency = row.get("baseCurrency") or raw_symbol
            symbol = _SYMBOL_ALIASES.get(base_currency, base_currency)

            # Ver docstring: turnoverOf24h ya viene en USDT (moneda de
            # cotización), no hace falta convertir como con openInterest.
            volume_24h_raw = row.get("turnoverOf24h")
            volume_24h_usd = None
            if volume_24h_raw is not None:
                try:
                    volume_24h_usd = float(volume_24h_raw)
                except (TypeError, ValueError):
                    volume_24h_usd = None

            out.append(
                FundingRate(
                    exchange="kucoinfutures",
                    venue_type=VenueType.CEX,
                    symbol=symbol,
                    raw_symbol=raw_symbol,
                    funding_rate=rate,
                    interval_hours=interval_hours,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=open_interest_usd,
                    volume_24h_usd=volume_24h_usd,
                )
            )

        if not out:
            sample = dict(list(skipped.items())[:3])
            raise RuntimeError(
                f"kucoinfutures: {len(skipped)}/{len(rows)} contratos se saltaron (o no "
                f"estaban 'Open') y no quedó ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "kucoinfutures: %d/%d contratos se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(rows),
                skipped,
            )

        if oi_anomaly_samples:
            logger.warning(
                "kucoinfutures DIAGNÓSTICO OI negativo (%d contrato(s), ver docstring del módulo — "
                "openInterest*multiplier*markPrice dio negativo, físicamente imposible, se descartó "
                "a None en vez de propagarse; payload crudo de los tres factores para averiguar "
                "cuál viene mal): %s",
                len(oi_anomaly_samples),
                json.dumps(oi_anomaly_samples, default=str)[:4000],
            )

        return out


def kucoin() -> KucoinConnector:
    return KucoinConnector()
