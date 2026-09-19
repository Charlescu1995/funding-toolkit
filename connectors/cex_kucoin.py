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

--- Nota sobre `openInterest` NEGATIVO — CAUSA RAÍZ CONFIRMADA (2026-09-19,
    símbolo SOL) ---

El usuario reportó un `open_interest_usd` imposible en el símbolo "SOL"
tres veces en la misma sesión de trabajo (−$616.481.571, −$618.445.974,96
y −$613.841.851,68 — mismo orden de magnitud pero no el mismo valor,
señal de que era algo recurrente en vivo, no un dato congelado). Con la
guardia + el log de diagnóstico añadidos más abajo, el propio log de
producción trajo el payload crudo culpable en el redespliegue siguiente:

    kucoinfutures DIAGNÓSTICO OI negativo (4 contrato(s)): {
      "ETHUSDM": {"openInterest_raw": "24974631", "multiplier_raw": -1.0, "markPrice_raw": 2608.06, ...},
      "SOLUSDM": {"openInterest_raw": "5424548",  "multiplier_raw": -1.0, "markPrice_raw": 113.326, ...},
      "XBTUSDM": {"openInterest_raw": "46529511", "multiplier_raw": -1.0, "markPrice_raw": 81148.8, ...},
      "XRPUSDM": {"openInterest_raw": "7629466",  "multiplier_raw": -1.0, "markPrice_raw": 1.3995,  ...}
    }

El símbolo real NO era "SOLUSDTM" (el contrato USDT-margined que se
investigó primero y que siempre vino sano) — es **"SOLUSDM"** (sin la
"T"), un contrato DISTINTO. Confirmado en vivo (WebFetch al endpoint de
detalle, `GET /api/v1/contracts/SOLUSDM`, no al listado — el listado
completo es demasiado grande y WebFetch lo resume/trunca sin avisar,
lección aprendida de un intento anterior que dio un falso "no existe"):

    {"symbol": "SOLUSDM", "baseCurrency": "SOL", "quoteCurrency": "USD",
     "settleCurrency": "SOL", "multiplier": -1.0, "isInverse": true,
     "status": "Open", "openInterest": "5417319", "markPrice": 113.16}

`isInverse: true` es el campo oficial de KuCoin que lo confirma: SOLUSDM
es un contrato INVERSO (coin-margined, se liquida en SOL, cotiza en USD)
— totalmente distinto de SOLUSDTM (lineal, USDT-margined). Ambos
comparten `baseCurrency="SOL"`, así que este conector los normalizaba al
MISMO `symbol="SOL"` y `compute_opportunities()` (ver `core/
opportunities.py`) los agrupaba y comparaba como si fueran el mismo
mercado — de ahí que "SOL" apareciera emparejado contra sí mismo en la
práctica (una pierna era el lineal, la otra intentaba ser el inverso).

La fórmula `openInterest × multiplier × markPrice` es correcta SOLO para
contratos lineales (donde `multiplier` es la cantidad de activo base por
contrato, ej. 0.1 SOL/contrato en SOLUSDTM). En un contrato inverso,
`multiplier=-1.0` no es "cantidad de SOL por contrato" — es el valor
centinela con el que KuCoin marca "esto es inverso", y la propia
documentación de la API (contenido renderizado por JS, WebFetch solo
pudo leer el esqueleto de navegación, no el cuerpo con las fórmulas) no
se pudo consultar para confirmar la conversión correcta de vuelta a USD
para este tipo de contrato — así que, siguiendo el mismo criterio de
siempre en este proyecto (nunca inventar un número que no se puede
verificar), NO se intentó adivinar esa fórmula.

**Fix aplicado**: en vez de intentar convertir un tipo de contrato para
el que no hay fórmula confirmada, se EXCLUYEN del todo los contratos
inversos (`isInverse=True`, con `multiplier<0` como señal de refuerzo)
de este conector — no entran ni al fetch masivo de funding rates ni,
por tanto, al emparejamiento de oportunidades. Es la decisión correcta
más allá del bug de OI: un contrato coin-margined (P&L y margen en SOL)
no es la misma operación que uno USDT-margined, así que tratarlos como
intercambiables bajo el mismo símbolo "SOL" ya era conceptualmente
incorrecto para esta herramienta, con o sin el bug de OI. La guardia
genérica de `open_interest_usd` negativo (ver más abajo) se mantiene
como red de seguridad por si aparece otra causa distinta en el futuro,
pero ya no debería dispararse para SOL/ETH/XBT/XRP.

--- Nota sobre `turnoverOf24h` (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El mismo objeto ya trae el volumen de 24h directamente en USD, sin hacer
falta ninguna llamada aparte ni conversión: `turnoverOf24h` (ej. XBTUSDTM:
3.701053670146E8 ≈ $370.1M) es el turnover en la moneda de cotización
(USDT), a diferencia de `volumeOf24h` (4724.686), que viene en unidades del
activo base (XBT) y por tanto necesitaría multiplicarse por el precio —
`turnoverOf24h` ya hace ese trabajo, así que se usa tal cual.

--- Nota sobre `markPrice == 0` (RESUELTO 2026-09-19, auditoría de bugs,
    Hallazgo #13 — severidad baja) ---

El cálculo de `open_interest_usd = openInterest * multiplier * markPrice`
solo comprobaba `mark_price is not None` antes de este fix. Un
`markPrice: 0` explícito (en vez de campo ausente) habría pasado ese
chequeo igual y producido `open_interest_usd = 0.0` — un mercado real
mostrado como si tuviera profundidad cero, indistinguible de un error real,
en vez de quedar fuera con un aviso. Un mark price de 0 no es físicamente
plausible para un contrato activo, así que se trata igual que un mark price
ausente: se descarta a `None` (no se calcula OI, `mark_price` tampoco se
publica como 0) y se guarda una muestra de diagnóstico, mismo patrón que la
guardia de OI negativo de más arriba. Nunca observado en vivo todavía — es
una guardia defensiva, no una causa raíz confirmada como la de SOL/ETH/XBT.
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
        # Ver docstring más abajo, sección "Nota sobre openInterest negativo
        # — CAUSA RAÍZ CONFIRMADA": guardia de seguridad para cualquier OI
        # negativo que se cuele por una causa DISTINTA a los contratos
        # inversos (que ya se excluyen explícitamente más abajo) — no
        # debería dispararse en circunstancias normales tras ese fix.
        oi_anomaly_samples: dict[str, dict] = {}
        inverse_excluded: list[str] = []
        # Ver docstring, "Nota sobre markPrice == 0" (Hallazgo #13 de la
        # auditoría, 2026-09-19): guardia defensiva, nunca vista en vivo.
        zero_mark_price_samples: dict[str, float] = {}

        for row in rows:
            if not isinstance(row, dict):
                skipped[str(row)] = "fila no es un objeto (formato inesperado)"
                continue

            raw_symbol = row.get("symbol")
            if raw_symbol is None:
                continue

            # Ver docstring, "Nota sobre openInterest negativo — CAUSA RAÍZ
            # CONFIRMADA": los contratos inversos/coin-margined (isInverse=
            # True, ej. SOLUSDM) comparten baseCurrency con su equivalente
            # lineal (ej. SOLUSDTM) y se normalizarían al mismo symbol="SOL"
            # — pero son una operación distinta (margen/P&L en el activo
            # base, no en USDT) y su fórmula de OI en USD no está
            # confirmada (multiplier=-1.0 en estos es un centinela de
            # "inverso", no "unidades de base por contrato" como en los
            # lineales). Se excluyen del todo en vez de adivinar la
            # conversión o tratarlos como el mismo mercado que el lineal.
            if row.get("isInverse") or (
                isinstance(row.get("multiplier"), (int, float)) and row.get("multiplier") < 0
            ):
                inverse_excluded.append(raw_symbol)
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
                else:
                    # Ver docstring, "Nota sobre markPrice == 0" (Hallazgo
                    # #13 de la auditoría, 2026-09-19): un mark price de 0
                    # no es físicamente plausible para un contrato activo —
                    # se descarta igual que si no hubiera venido, en vez de
                    # dejar que open_interest_usd salga 0.0 en silencio.
                    if mark_price == 0:
                        zero_mark_price_samples[raw_symbol] = mark_price_raw
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

        if inverse_excluded:
            logger.info(
                "kucoinfutures: %d contrato(s) inverso(s)/coin-margined excluidos a propósito "
                "(isInverse=True — ver docstring del módulo, causa raíz del bug de OI negativo "
                "de SOL): %s",
                len(inverse_excluded),
                sorted(inverse_excluded),
            )

        if oi_anomaly_samples:
            logger.warning(
                "kucoinfutures DIAGNÓSTICO OI negativo (%d contrato(s) — INESPERADO tras excluir "
                "los inversos, ver docstring del módulo: openInterest*multiplier*markPrice dio "
                "negativo en un contrato que NO se marcó isInverse, así que es una causa NUEVA, "
                "no la ya confirmada de SOLUSDM/ETHUSDM/XBTUSDM/XRPUSDM; se descartó a None en "
                "vez de propagarse; payload crudo de los tres factores para investigarla): %s",
                len(oi_anomaly_samples),
                json.dumps(oi_anomaly_samples, default=str)[:4000],
            )

        if zero_mark_price_samples:
            logger.warning(
                "kucoinfutures DIAGNÓSTICO markPrice == 0 (Hallazgo #13 de la auditoría, "
                "2026-09-19): %d contrato(s) con markPrice explícito de 0 -- se descartó a "
                "None (ni mark_price ni open_interest_usd se calculan/publican) en vez de "
                "dejar que open_interest_usd saliera 0.0 en silencio: %s",
                len(zero_mark_price_samples),
                zero_mark_price_samples,
            )

        return out


def kucoin() -> KucoinConnector:
    return KucoinConnector()
