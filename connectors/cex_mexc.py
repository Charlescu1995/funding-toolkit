"""
Conector para MEXC Futures — CEX de derivados.

No soportado por ccxt: `ccxt.mexc.fetch_funding_rates()` lanza `NotSupported`
(comprobado leyendo el código fuente de ccxt) — el spot de MEXC sí funciona
en ccxt, pero el módulo de futuros/swap no expone ese método.

MEXC reparte la información en TRES endpoints públicos, todos bulk (sin pool
de hilos):

    GET https://contract.mexc.com/api/v1/contract/detail
        -> metadata de todos los contratos: contractSize, operable (state),
           linear vs inverso (quoteCoin/settleCoin)
    GET https://contract.mexc.com/api/v1/contract/funding_rate
        -> funding rate, intervalo (collectCycle) y precios, de todos los contratos
    GET https://contract.mexc.com/api/v1/contract/ticker
        -> holdVol (open interest en Nº de contratos) y amount24 (volumen de
           24h ya en USD), de todos los contratos

Todo comprobado en vivo (WebFetch directo — ver nota de entorno en
connectors/cex_kucoin.py: estos hosts están bloqueados por la política de
red de ESTE sandbox de desarrollo, así que la investigación se hizo con
WebFetch en vez de curl directo, pero contra la API real, no solo contra
documentación).

--- Nota sobre el universo de símbolos: MEXC mezcla cripto con activos RWA,
    y aquí se dejan ambos a propósito (a diferencia del bug real de ApeX) ---

`/api/v1/contract/detail` trae, en la misma lista, perpetuos cripto normales
(BTC_USDT, ETH_USDT...) y perpetuos sobre acciones/materias primas
tokenizadas (ej. "XAU_USDT", "GOOGLSTOCK_USDT", "NVIDIA_USDT", "USOIL_USDT").
A diferencia del bug real de ApeX (que mezclaba cripto con mercados de
predicción que SÍ rompían con 403 — ver connectors/dex_apex.py), aquí los RWA
de MEXC responden con datos de funding reales y válidos, con el mismo
esquema que el resto — no hay nada que arreglar ni filtrar. Como este
proyecto cubre "cripto + RWA" por diseño (ver README), se dejan sin filtrar:
son oportunidades legítimas más, no ruido.

--- Nota sobre `state` como filtro de operable (RESUELTO 2026-09-19,
    auditoría de bugs, Hallazgo #11 — antes se usaba `apiAllowed`) ---

Versión anterior de este conector: se usaba `apiAllowed` como filtro de
"¿este contrato está vivo?" porque su nombre parecía autoexplicativo, y se
dejaba `state` sin usar porque su mapeo "no estaba documentado" (solo se
había visto un ejemplo en vivo, BTC_USDT, con `state=0`/`apiAllowed=true`).
La auditoría de bugs (Hallazgo #11) señaló que esto era el mismo patrón
exacto que el bug real ya arreglado en `dex_extended.py`: un campo con
nombre prometedor que no es el que de verdad marca "delistado".

**Confirmado leyendo la documentación oficial de MEXC**
(mexcdevelop.github.io/apidocs/contract_v1_en/, 2026-09-19):

    "state": 0, "status, 0:enabled,1:delivery, 2:completed, 3:offline, 4:pause"
    "apiAllowed": bool, "whether support api"

`state` ES el campo de estado de listado (el equivalente exacto al
`status`/`orderBookState` de KuCoin/Extended) — `apiAllowed` es, según la
propia doc, un flag de si la API soporta ese contrato, no un estado de
listado. Como este proyecto solo lee endpoints públicos de solo lectura
(no opera vía API), lo que importa es si el contrato está listado y
operable (`state`), no ese flag de soporte de API cuyo alcance exacto
sigue sin aclarar del todo la doc.

Se intentó confirmar EN VIVO si algún contrato real diverge (`state` != 0
con `apiAllowed=true`, o viceversa) con dos llamadas WebFetch a
`/api/v1/contract/detail` — misma limitación de truncamiento en arrays
grandes ya documentada varias veces en este proyecto (KuCoin, Lighter):
solo se ve una porción parcial del array (probablemente >1000 contratos),
y en esa porción todos traían `state=0` y `apiAllowed=true`. No hay, de
momento, ningún caso real observado donde diverjan.

**Fix**: se usa `state == 0` como filtro principal de operable en vez de
`apiAllowed` — un `state` ausente NO descarta el símbolo (mismo criterio
de "sin evidencia de que esté mal" ya usado en el resto del proyecto).
`apiAllowed` se sigue capturando, pero solo para un diagnóstico: si algún
día un contrato trae `state` y `apiAllowed` en desacuerdo, queda registrado
en un log en vez de perderse en silencio — igual que el diagnóstico ya
existente para el campo `funding_rate` sin confirmar de GRVT (Hallazgo #8).
No se exige que los dos campos coincidan para incluir un contrato: eso
añadiría una restricción sin evidencia que la respalde, en la dirección
contraria al criterio de "no inventar" — podría esconder una oportunidad
real basándose en una suposición sin confirmar sobre qué mide `apiAllowed`.

**Confirmado en producción real (2026-09-19, primer despliegue de este
fix)**: el diagnóstico SÍ disparó, con 10 contratos reales
(`LONG_USDT`, `MUSEBOOK_USDT`, `MCAT_USDT`, `ORBIO_USDT`, `PAID_USDT`,
`HOOKR_USDT`, `ROBIN_USDT`, `COOL_USDT`, `GSTOCK_USDT`, `PAIR_USDT`) con
`state=0` pero `apiAllowed=False`. Esto valida con datos reales la
decisión de no exigir también `apiAllowed`: si se hubiera exigido,
estos 10 contratos operables se habrían excluido del Ranking por error.

--- Nota sobre contratos inversos/coin-margined (RESUELTO 2026-09-19,
    auditoría de bugs, Hallazgo #12 — mismo hueco que causó el bug de SOL
    en KuCoin) ---

La auditoría de bugs (Hallazgo #12) señaló que este conector solo recorta
el sufijo de la cotización al normalizar el símbolo (`symbol.split("_")[0]`)
sin ningún equivalente al `isInverse`/`multiplier<0` que se añadió a
`cex_kucoin.py` tras el bug real de SOLUSDM. Si MEXC lista algún contrato
margined-en-moneda-base (coin-margined / "Coin-M") junto a uno
USDT-margined para el mismo activo base, se normalizarían al mismo símbolo
y, como su fórmula de OI difiere, el número saldría mal en silencio.

**Confirmado que MEXC sí tiene una línea de producto "Coin-M" separada**
(blog/glosario oficial de MEXC, y una página de trading real para
`BTC_USD` en `mexc.com/futures/coin-m/BTC_USD`, distinta de `BTC_USDT`).
La documentación oficial de la API confirma que `/api/v1/contract/detail`
trae `baseCoin`, `quoteCoin` y `settleCoin` por contrato — campos que solo
tienen sentido si la API mezcla contratos linear (quote=settle=USDT) e
inverse (settle en el activo base) en el mismo endpoint bulk.

**Confirmado en producción real (2026-09-19, primer despliegue de este
fix)**: `/api/v1/contract/detail` SÍ trae contratos Coin-M en el mismo
array bulk que lee este conector — el guard nuevo excluyó 10 contratos
reales: `ADA_USD`, `AVAX_USD`, `BTC_USD`, `DOGE_USD`, `ETH_USD`,
`LINK_USD`, `LTC_USD`, `SOL_USD`, `SUI_USD`, `XRP_USD`. Todos estos
activos ya tienen su contrato linear (`_USDT`) en el Ranking, así que sin
este fix habrían colisionado de verdad en el símbolo normalizado
(`"BTC"`, `"ETH"`, etc.) con su hermano linear — el riesgo pasa de
sospechado (solo por documentación) a confirmado con datos reales.

**Fix**: se excluyen ENTEROS los contratos donde `settleCoin != quoteCoin`
(ambos presentes) — mismo criterio que KuCoin con `isInverse`, campo real
de la API en vez de adivinar por el nombre del símbolo. `quoteCoin`/
`settleCoin` ausentes NO se tratan como inverso — sin evidencia de que lo
sean. Se registra un log con los símbolos excluidos si esto llega a pasar
alguna vez, para poder confirmarlo con datos reales de producción.

--- Nota sobre `collectCycle` (CONFIRMADO en vivo — SÍ varía por símbolo,
    a diferencia del resto de CEX del proyecto) ---

El resto de CEX vía ccxt (ver DEFAULT_INTERVAL_HOURS en cex_ccxt.py) usa 8h
fijo como aproximación porque ccxt no siempre expone el intervalo real. Aquí
MEXC SÍ lo expone por contrato (`collectCycle`, en horas) y de verdad varía:
BTC_USDT trae 8, XAU_USDT trae 4 (confirmado en vivo) — se usa este campo
directamente en vez de asumir un valor fijo para todos.

--- Nota sobre `fundingRate` (CONFIRMADO en vivo, tasa cruda del intervalo) ---

BTC_USDT en vivo: fundingRate=0.000024 con collectCycle=8 -> APR ≈ 2.63%,
cifra normal — confirma que es la tasa cruda del intervalo, no anualizada.

--- Nota sobre `holdVol` (CONFIRMADO en vivo, contrastado con contractSize
    y fairPrice) ---

`holdVol` (de /contract/ticker) viene en Nº DE CONTRATOS, no en el activo
base ni en USD. Cada contrato representa `contractSize` unidades del activo
base (0.0001 BTC/contrato para BTC_USDT, confirmado en /contract/detail):

    open_interest_usd = holdVol * contractSize * fairPrice

--- Nota sobre `amount24` (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El mismo /contract/ticker trae, junto a holdVol, dos campos de volumen:
`volume24` (545,348,505 en el ejemplo real de BTC_USDT) y `amount24`
(4,265,182,792.82). `volume24` está en Nº DE CONTRATOS, igual que holdVol
(comprobado por consistencia interna: volume24 × contractSize × fairPrice ≈
amount24 — ambos lados dan ≈$4.25-4.27B, la pequeña diferencia es normal
porque el precio se mueve entre el cálculo de cada campo en el propio
exchange). `amount24` ya es el turnover de 24h en USD directamente, así que
se usa tal cual, sin repetir la conversión que sí hace falta para holdVol.

--- Nota sobre `fairPrice == 0` (RESUELTO 2026-09-19, auditoría de bugs,
    Hallazgo #13 — severidad baja, mismo fix que cex_kucoin.py) ---

`open_interest_usd = holdVol * contractSize * fairPrice` solo comprobaba
`mark_price is not None`. Un `fairPrice: 0` explícito habría pasado ese
chequeo igual y dado `open_interest_usd = 0.0` -- un contrato operable
mostrado como si tuviera profundidad cero. Se descarta igual que un
fairPrice ausente (mark_price a None, no se calcula OI) y se registra una
muestra de diagnóstico. Guardia defensiva, nunca observada en vivo.

--- Nota sobre símbolos ausentes de contract/detail (RESUELTO 2026-09-19,
    auditoría de bugs, Hallazgo #18) ---

`detail_by_symbol.get(symbol, (None, False))` trataba IGUAL dos casos muy
distintos: un símbolo presente en `contract/detail` pero con `operable =
False` (un contrato real, simplemente no operable ahora mismo -- el
descarte esperado y normal) y un símbolo que no aparece EN ABSOLUTO en
`contract/detail` (la metadata ni siquiera lo conoce -- algo mucho más raro
y potencialmente indicio de un desajuste entre los dos endpoints). El
segundo caso caía por el mismo default `(None, False)` y se perdía sin
pasar nunca por `skipped`, a diferencia de cada otro motivo de descarte en
esta misma función. Ahora se distingue explícitamente: un símbolo ausente
de `contract/detail` se registra en `skipped` (y por tanto aparece en el
log), el caso "presente pero no operable" sigue igual que siempre.
"""

from __future__ import annotations

import json
import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://contract.mexc.com"
DETAIL_URL = f"{BASE_URL}/api/v1/contract/detail"
FUNDING_RATE_URL = f"{BASE_URL}/api/v1/contract/funding_rate"
TICKER_URL = f"{BASE_URL}/api/v1/contract/ticker"


class MexcConnector:
    name = "mexc"
    venue_type = VenueType.CEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        detail_resp = self._session.get(DETAIL_URL, timeout=self._timeout)
        detail_resp.raise_for_status()
        detail_payload = detail_resp.json()
        detail_rows = detail_payload.get("data")
        if not isinstance(detail_rows, list) or not detail_rows:
            raise RuntimeError(
                "mexc: /api/v1/contract/detail no devolvió una lista en 'data' — "
                f"claves de nivel superior: "
                f"{list(detail_payload.keys()) if isinstance(detail_payload, dict) else type(detail_payload).__name__}"
            )

        # symbol -> (contractSize, operable)
        detail_by_symbol: dict[str, tuple[float | None, bool]] = {}
        # Hallazgo #11 de la auditoría (2026-09-19), ver README/docstring:
        # diagnóstico de divergencia entre state (confirmado por la doc
        # oficial como filtro de operable) y apiAllowed (el filtro viejo,
        # cuyo significado real sigue sin confirmar del todo) -- si algún
        # día un contrato trae los dos campos en desacuerdo, se registra en
        # vez de perderse en silencio.
        state_apiallowed_mismatch: dict[str, dict] = {}
        # Hallazgo #12 de la auditoría (2026-09-19), ver README/docstring:
        # contratos inversos/coin-margined ("Coin-M" en MEXC, ej. BTC_USD
        # frente al BTC_USDT linear que ya cubre este conector) se detectan
        # con settleCoin != quoteCoin (confirmado por la doc oficial de MEXC
        # como campos reales del endpoint) -- misma idea que isInverse en
        # KuCoin. La fórmula de OI (holdVol × contractSize × fairPrice) solo
        # está confirmada para contratos linear (settleCoin == quoteCoin);
        # para uno inverso daría un número sin sentido, mismo bug real que
        # KuCoin ya tuvo con SOLUSDM.
        inverse_excluded: list[str] = []
        for row in detail_rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            if symbol is None:
                continue

            # Ver docstring (Hallazgo #12): un contrato inverso/coin-margined
            # se descarta ENTERO -- la fórmula de OI confirmada no aplica, y
            # no hay (todavía) ningún ejemplo real observado para poder
            # confirmar la fórmula correcta con evidencia, así que se
            # excluye en vez de adivinar. quoteCoin/settleCoin ausentes NO
            # se tratan como inverso -- sin evidencia de que lo sean.
            quote_coin = row.get("quoteCoin")
            settle_coin = row.get("settleCoin")
            if quote_coin is not None and settle_coin is not None and quote_coin != settle_coin:
                inverse_excluded.append(symbol)
                continue

            contract_size_raw = row.get("contractSize")
            try:
                contract_size = float(contract_size_raw) if contract_size_raw is not None else None
            except (TypeError, ValueError):
                contract_size = None
            # Ver docstring: se usa state (confirmado por la doc oficial de
            # MEXC como el campo de estado de listado, 0=enabled), no
            # apiAllowed, como filtro de operable. Un state AUSENTE no
            # descarta el símbolo -- mismo criterio que el resto del
            # proyecto ("sin evidencia de que esté mal").
            state_raw = row.get("state")
            operable = state_raw is None or state_raw == 0

            api_allowed_raw = row.get("apiAllowed")
            api_allowed = bool(api_allowed_raw)
            if operable != api_allowed and len(state_apiallowed_mismatch) < 10:
                state_apiallowed_mismatch[symbol] = {
                    "state": state_raw,
                    "apiAllowed": api_allowed_raw,
                    "operable_segun_state": operable,
                }

            detail_by_symbol[symbol] = (contract_size, operable)

        if inverse_excluded:
            logger.warning(
                "mexc: %d contrato(s) inverso(s)/coin-margined excluido(s) por "
                "settleCoin != quoteCoin (Hallazgo #12 de la auditoría, 2026-09-19 -- ver "
                "README/docstring, la fórmula de OI confirmada solo aplica a contratos "
                "linear): %s",
                len(inverse_excluded),
                sorted(inverse_excluded),
            )

        if state_apiallowed_mismatch:
            logger.warning(
                "mexc DIAGNÓSTICO state vs apiAllowed (Hallazgo #11 de la auditoría, "
                "2026-09-19): %d contrato(s) con los dos campos en desacuerdo -- se usó "
                "'state' (confirmado por la doc oficial como filtro de operable), no "
                "'apiAllowed'. Muestra: %s",
                len(state_apiallowed_mismatch),
                state_apiallowed_mismatch,
            )

        funding_resp = self._session.get(FUNDING_RATE_URL, timeout=self._timeout)
        funding_resp.raise_for_status()
        funding_payload = funding_resp.json()
        funding_rows = funding_payload.get("data")
        if not isinstance(funding_rows, list) or not funding_rows:
            raise RuntimeError(
                "mexc: /api/v1/contract/funding_rate no devolvió una lista en 'data' — "
                f"claves de nivel superior: "
                f"{list(funding_payload.keys()) if isinstance(funding_payload, dict) else type(funding_payload).__name__}"
            )

        ticker_resp = self._session.get(TICKER_URL, timeout=self._timeout)
        ticker_resp.raise_for_status()
        ticker_payload = ticker_resp.json()
        ticker_rows = ticker_payload.get("data") or []
        if not ticker_rows:
            # Hallazgo #10 de la auditoría (2026-09-19), ver README: a
            # diferencia de detail/funding_rate (que SÍ lanzan RuntimeError
            # si vienen vacíos), este endpoint antes se quedaba en silencio
            # con `or []` -- si /api/v1/contract/ticker responde vacío o
            # cambia de forma, holdVol_by_symbol/volume_24h_by_symbol quedan
            # vacíos y TODO el exchange pierde OI/volumen sin ningún aviso,
            # indistinguible de "MEXC simplemente no reporta esto". No se
            # convierte en RuntimeError (el funding_rate en sí sigue siendo
            # válido y útil sin estos campos, a diferencia del filtro de
            # trading_status de Nado, donde dejar pasar sin filtrar sí
            # colaba datos falsos) -- se deja constancia explícita para que
            # deje de ser un fallo silencioso.
            logger.warning(
                "mexc: /api/v1/contract/ticker no trajo ninguna fila en 'data' -- "
                "open_interest_usd y volume_24h_usd quedarán en None para TODO el "
                "exchange este ciclo (claves de nivel superior recibidas: %s)",
                list(ticker_payload.keys()) if isinstance(ticker_payload, dict) else type(ticker_payload).__name__,
            )
        hold_vol_by_symbol = {
            row["symbol"]: row.get("holdVol")
            for row in ticker_rows
            if isinstance(row, dict) and row.get("symbol")
        }
        # Ver docstring: amount24 ya viene en USD, no hace falta convertir.
        volume_24h_by_symbol = {
            row["symbol"]: row.get("amount24")
            for row in ticker_rows
            if isinstance(row, dict) and row.get("symbol")
        }

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}
        # Ver auditoría de bugs (Hallazgo #3, 2026-09-19): mismo guard
        # defensivo que ya tiene connectors/cex_kucoin.py — un OI negativo
        # es físicamente imposible, así que no se propaga tal cual, se
        # descarta a None y se deja el payload crudo para diagnosticar.
        # Aquí no hay (todavía) ninguna causa raíz confirmada como la de
        # KuCoin (contratos inversos) — es puramente defensivo.
        oi_anomaly_samples: dict[str, dict] = {}
        # Ver Hallazgo #13 de la auditoría (2026-09-19, severidad baja):
        # mismo criterio que zero_mark_price_samples de cex_kucoin.py.
        zero_mark_price_samples: dict[str, float] = {}

        for row in funding_rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            if symbol is None:
                continue

            # Ver Hallazgo #18 de la auditoría (2026-09-19): a diferencia de
            # un símbolo presente en detail_by_symbol pero con operable=False
            # (un contrato real, simplemente no operable ahora mismo -- se
            # sigue descartando en silencio, es el filtro normal), un
            # símbolo AUSENTE del todo de contract/detail es una situación
            # distinta -- antes cogía el mismo camino (`.get(..., (None,
            # False))` hacía operable=False por el valor por defecto) y se
            # perdía sin aparecer nunca en `skipped`, indistinguible de un
            # descarte esperado. Ahora se registra explícitamente.
            if symbol not in detail_by_symbol:
                skipped[symbol] = (
                    "Hallazgo #18: presente en funding_rate pero ausente en "
                    "contract/detail (metadata) -- no se pudo confirmar si es operable"
                )
                continue

            contract_size, operable = detail_by_symbol[symbol]
            if not operable:
                continue

            rate_raw = row.get("fundingRate")
            interval_raw = row.get("collectCycle")
            if rate_raw is None or interval_raw is None:
                skipped[symbol] = "faltan campos (fundingRate/collectCycle)"
                continue

            try:
                rate = float(rate_raw)
                interval_hours = float(interval_raw)
            except (TypeError, ValueError) as exc:
                skipped[symbol] = f"valor no numérico: {exc}"
                continue

            if interval_hours <= 0:
                skipped[symbol] = f"collectCycle inválido ({interval_raw})"
                continue

            mark_price_raw = row.get("fairPrice")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None
                else:
                    # Ver Hallazgo #13 de la auditoría (2026-09-19): un
                    # fairPrice de 0 no es plausible para un contrato
                    # operable -- se descarta igual que si no hubiera
                    # venido, en vez de dejar que open_interest_usd salga
                    # 0.0 en silencio (mismo criterio que cex_kucoin.py).
                    if mark_price == 0:
                        zero_mark_price_samples[symbol] = mark_price_raw
                        mark_price = None

            hold_vol_raw = hold_vol_by_symbol.get(symbol)
            open_interest_usd = None
            if hold_vol_raw is not None and contract_size is not None and mark_price is not None:
                try:
                    # Ver docstring: holdVol viene en Nº de contratos.
                    open_interest_usd = float(hold_vol_raw) * contract_size * mark_price
                except (TypeError, ValueError):
                    open_interest_usd = None

                # Ver Hallazgo #3 de la auditoría: guard defensivo, mismo
                # patrón que cex_kucoin.py — un OI negativo no se propaga.
                if open_interest_usd is not None and open_interest_usd < 0:
                    oi_anomaly_samples[symbol] = {
                        "holdVol_raw": hold_vol_raw,
                        "contractSize": contract_size,
                        "fairPrice_raw": mark_price_raw,
                        "mark_price_calculado": mark_price,
                        "open_interest_usd_calculado_DESCARTADO": open_interest_usd,
                    }
                    open_interest_usd = None

            volume_24h_raw = volume_24h_by_symbol.get(symbol)
            volume_24h_usd = None
            if volume_24h_raw is not None:
                try:
                    volume_24h_usd = float(volume_24h_raw)
                except (TypeError, ValueError):
                    volume_24h_usd = None

            base_symbol = symbol.split("_")[0] if "_" in symbol else symbol

            out.append(
                FundingRate(
                    exchange="mexc",
                    venue_type=VenueType.CEX,
                    symbol=base_symbol,
                    raw_symbol=symbol,
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
                f"mexc: {len(skipped)} símbolos con datos incompletos de {len(funding_rows)} "
                f"totales, y ningún par operable/válido quedó — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "mexc: %d/%d símbolos se saltaron por datos incompletos (se omiten, no tiran el resto): %s",
                len(skipped),
                len(funding_rows),
                skipped,
            )

        if oi_anomaly_samples:
            logger.warning(
                "mexc DIAGNÓSTICO OI negativo (%d contrato(s) — guard defensivo del Hallazgo #3 "
                "de la auditoría, sin causa raíz confirmada todavía a diferencia de KuCoin/SOL; "
                "se descartó a None en vez de propagarse; payload crudo de los tres factores "
                "para investigarla): %s",
                len(oi_anomaly_samples),
                json.dumps(oi_anomaly_samples, default=str)[:4000],
            )

        if zero_mark_price_samples:
            logger.warning(
                "mexc DIAGNÓSTICO fairPrice == 0 (Hallazgo #13 de la auditoría, 2026-09-19): "
                "%d contrato(s) con fairPrice explícito de 0 -- se descartó a None en vez de "
                "dejar que open_interest_usd saliera 0.0 en silencio: %s",
                len(zero_mark_price_samples),
                zero_mark_price_samples,
            )

        return out


def mexc() -> MexcConnector:
    return MexcConnector()
