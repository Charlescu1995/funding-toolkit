"""
Conector para Paradex — DEX de perpetuos sobre Starknet.

Un solo endpoint público trae funding rate, mark price y open interest para
todos los mercados a la vez (parecido a Hyperliquid, a diferencia de Lighter
que necesita cruzar dos llamadas).

Docs: https://docs.paradex.trade/api/prod/markets/get-markets-summary

Nota sobre unidades: al igual que en Hyperliquid/Lighter, `open_interest` se
trata como unidades del activo base (no está documentado explícitamente que
sea así, pero es la convención más común en DEX de perpetuos con order book)
y se convierte a USD multiplicando por `mark_price`. Si al desplegar los
números de OI salen claramente desproporcionados, es la primera sospecha a
revisar — puede que Paradex ya lo dé directamente en USD.

--- Nota sobre volumen 24h (Paso 6 punto 2 — Volumen; mismo criterio de
    duda que con `open_interest` de arriba, NO confirmado como USD) ---

El mismo objeto ya trae `volume_24h` (ej. BTC-USD-PERP en el ejemplo de la
documentación oficial: "47041.0424" con mark_price="29799.70877478") y
`total_volume` (acumulado, no de 24h — se descarta). La documentación de
Paradex NO especifica explícitamente la unidad de `volume_24h`. Se probó en
vivo (WebFetch) contra `/v1/markets/summary`, pero el endpoint devuelve TODO
el universo de mercados (perpetuos + opciones) en un único payload enorme
que WebFetch trunca antes de llegar a BTC-USD-PERP, y el parámetro
`market=BTC-USD-PERP` no filtra la respuesta (se comprobó: sigue devolviendo
el listado completo) — así que no se pudo aislar un ejemplo real de BTC para
contrastar magnitudes. Con el ejemplo real que SÍ se pudo leer completo
(SUI-USD-PERP en vivo: volume_24h="1087.9657900000002",
mark_price="0.72072513", open_interest="28628.6"), tratar `volume_24h` como
unidades del activo base (1087.97 SUI ≈ $784 de notional) da una cifra baja
pero no descartable para un mercado de bajo volumen en un DEX todavía
pequeño como Paradex — no hay evidencia suficiente para decidir entre "ya en
USD" y "en activo base" con ese único dato. Se aplica el MISMO criterio que
ya usa este conector para `open_interest` (unidades base, convertir con
mark_price) por consistencia y porque es la convención más común — si al
desplegar los números de volumen salen claramente desproporcionados (muy
por debajo o muy por encima de lo esperado), es la primera sospecha a
revisar, igual que con OI:

    volume_24h_usd = float(volume_24h) * mark_price

--- Nota sobre la escala de `funding_rate` -- SOSPECHOSO, sin verificar en
    vivo (Hallazgo #14 de la auditoría, 2026-09-19) ---

A diferencia de OI y volumen (arriba), `funding_rate` se usa tal cual
(`float(rate)`, sin ningún escalado) sin que se haya citado nunca un valor
real confirmado para contrastar. Investigado en esta ronda:

  - `docs.paradex.trade/risk/funding-mechanism` (la página que explica el
    MECANISMO, no el endpoint) trae un ejemplo trabajado con Raw Rate =
    0.0003 = 0.03% por periodo de 8h -- decimal sin escalar, coincide con
    lo que hace el código ahora mismo.
  - Pero la página de referencia del propio endpoint
    (`/api/prod/markets/get-markets-summary`) describe el campo como
    "Current funding rate **percentage**", y su ejemplo de respuesta trae
    `"funding_rate": "0.3"` -- el MISMO objeto BTC-USD-PERP citado arriba
    para volume_24h/mark_price. Ojo: ese ejemplo tiene toda la pinta de ser
    un placeholder autogenerado del esquema OpenAPI, no una captura real —
    `ask_iv`, `delta`, `gamma`, `theta`, `risk_free_rate` y varios más
    vienen todos con números redondos poco creíbles como "0.2", "0.05" o
    "1". Esto no solo deja sin confirmar la escala de `funding_rate`: también
    debilita la confianza en el propio ejemplo que se usó arriba para
    razonar sobre `volume_24h`/`open_interest` -- puede que tampoco sea un
    dato real.
  - Se intentó llamar en vivo a `api.prod.paradex.trade/v1/markets/summary`
    con WebFetch para sacar un valor real de contraste: esta vez el propio
    sandbox bloqueó la petición pidiendo una aprobación que no llegó a
    tiempo (modo de fallo distinto al truncamiento de arrays ya conocido
    con MEXC/KuCoin, pero mismo resultado práctico: sin dato en vivo).

No se aplicó ningún factor de escala sin poder confirmarlo (mismo criterio
de siempre: mejor no tocar que adivinar). En su lugar, `fetch_funding_rates()`
deja un log de diagnóstico (`paradex DIAGNÓSTICO escala de funding_rate`)
con los primeros valores crudos de cada ciclo, para poder contrastar la
magnitud real contra lo esperado en el próximo log de producción.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

MARKETS_SUMMARY_URL = "https://api.prod.paradex.trade/v1/markets/summary"

# Paradex liquida funding cada 8h ("Funding Period: 8h" en la documentación
# de riesgo — docs.paradex.trade/risk/funding-mechanism).
INTERVAL_HOURS = 8


class ParadexConnector:
    name = "paradex"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        # Igual que en el resto de conectores: la excepción sube sin
        # tragársela, para que el motivo real del fallo se pueda enseñar.
        resp = self._session.get(
            MARKETS_SUMMARY_URL, params={"market": "ALL"}, timeout=self._timeout
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("results", [])

        out: list[FundingRate] = []
        # Ver Hallazgo #13 de la auditoría (2026-09-19, severidad baja).
        zero_mark_price_samples: dict[str, object] = {}
        # Ver docstring, "Nota sobre la escala de funding_rate SIN
        # verificar (Hallazgo #14 de la auditoría, 2026-09-19)": muestra de
        # los primeros valores crudos vistos, para poder confirmar/
        # descartar la escala con el próximo log real de producción.
        funding_rate_samples: dict[str, object] = {}
        for row in rows:
            raw_symbol = row.get("symbol")  # ej. "BTC-USD-PERP"
            rate = row.get("funding_rate")
            if raw_symbol is None or rate is None:
                continue

            # Solo nos interesan los perpetuos (Paradex también lista
            # opciones bajo el mismo endpoint, con símbolos que no siguen
            # este patrón "-PERP").
            if not raw_symbol.endswith("-PERP"):
                continue
            symbol = raw_symbol.split("-")[0]

            if len(funding_rate_samples) < 15:
                funding_rate_samples[raw_symbol] = {
                    "funding_rate_raw": rate,
                    "mark_price_raw": row.get("mark_price"),
                }

            mark_price_raw = row.get("mark_price")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None
                else:
                    # Ver Hallazgo #13 de la auditoría (2026-09-19,
                    # severidad baja): un mark_price de 0 no se propaga
                    # (daría oi_usd/volume_24h_usd = 0.0 en silencio).
                    if mark_price == 0:
                        zero_mark_price_samples[raw_symbol] = mark_price_raw
                        mark_price = None

            open_interest_raw = row.get("open_interest")
            oi_usd = None
            if open_interest_raw is not None and mark_price is not None:
                try:
                    oi_usd = float(open_interest_raw) * mark_price
                except (TypeError, ValueError):
                    oi_usd = None

            # Ver docstring: volume_24h no está confirmado como USD, se
            # trata como unidades base (mismo criterio que open_interest).
            volume_24h_raw = row.get("volume_24h")
            volume_24h_usd = None
            if volume_24h_raw is not None and mark_price is not None:
                try:
                    volume_24h_usd = float(volume_24h_raw) * mark_price
                except (TypeError, ValueError):
                    volume_24h_usd = None

            out.append(
                FundingRate(
                    exchange="paradex",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=raw_symbol,
                    funding_rate=float(rate),
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=oi_usd,
                    volume_24h_usd=volume_24h_usd,
                )
            )

        if zero_mark_price_samples:
            logger.warning(
                "paradex DIAGNÓSTICO mark_price == 0 (Hallazgo #13 de la auditoría, 2026-09-19): "
                "%d símbolo(s) con mark_price explícito de 0 -- no se calculó "
                "open_interest_usd/volume_24h_usd: %s",
                len(zero_mark_price_samples),
                zero_mark_price_samples,
            )

        if funding_rate_samples:
            logger.info(
                "paradex DIAGNÓSTICO escala de funding_rate (Hallazgo #14 de la auditoría, "
                "2026-09-19 -- SOSPECHOSO, sin verificar en vivo, ver docstring del módulo): "
                "muestra de los primeros %d valores crudos recibidos este ciclo, para "
                "contrastar magnitud contra lo esperado (un funding típico ronda fracciones "
                "de 0.01%%-0.05%% por periodo de 8h -- si estos valores salen ~100x más "
                "grandes o más pequeños, la escala asumida en el código es incorrecta): %s",
                len(funding_rate_samples),
                funding_rate_samples,
            )

        return out


def paradex() -> ParadexConnector:
    return ParadexConnector()
