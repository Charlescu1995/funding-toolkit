"""
Conector para Variational — DEX de perpetuos (omnichain, RFQ + AMM híbrido).

Un solo endpoint público trae TODO de golpe: stats de la plataforma más un
array `listings[]` con funding rate, mark price y open interest por mercado.
No hace falta pool de hilos (como Extended, a diferencia de edgeX/GRVT).

Docs / endpoint: https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats
Rate limits documentados: 10 req/10s por IP, 1000 req/min global — de sobra
para una sola llamada por refresco.

Todos los campos numéricos vienen como STRING ("para preservar precisión
decimal", según la propia respuesta) — se convierten a float aquí.

--- Nota IMPORTANTE sobre la escala de `funding_rate` (asunción, no confirmada
    con la UI oficial de Variational) ---

El campo `funding_rate` de cada listing NO parece ser la tasa cruda del
intervalo (que es lo que espera `FundingRate.funding_rate` — ver
core/normalize.py), sino que todo indica que ya viene ANUALIZADA (APY como
fracción decimal). Evidencia:

  - Valores en vivo observados (ticker, funding_rate, funding_interval_s):
    IMX -0.430567 @ 14400s (4h), AERO -0.162022 @ 14400s, CATI 0.1095 @ 14400s.
    Si esto fuera la tasa cruda POR CADA 4h, serían tasas del orden de
    ±43% cada 4 horas — imposible, dado que la propia documentación de
    Variational indica un límite de funding del 2%/hora (lo que acota
    cualquier ventana de 4h a un máximo de ±8%).
  - Se comprobaron en vivo los 5 valores de mayor y menor magnitud sobre
    los 553 mercados: el máximo observado fue REZ en -1.333404 (14400s).
    El techo teórico de APY bajo el límite "2%/hora" es 0.02 × 8760 = 17.52
    (1752%) — un valor de -1.333 (-133%) encaja perfectamente como APY,
    pero sería absurdo como tasa de un solo intervalo de 4h.
  - Se contrastó contra https://loris.tools/funding/exchange/variational
    (uno de los dos trackers en los que se inspira este proyecto, según la
    primera línea del README) — Loris muestra las tasas de Variational en
    bps/8h + APY% derivado, y su propia matemática bps→APY es consistente
    (ej. 15.94 bps/8h → 174.56% APY vía bps/10000 × (8760/8) × 100).

Por tanto, este conector CONVIERTE HACIA ATRÁS el APY reportado a una tasa
por intervalo antes de construir el FundingRate, para que
`core/normalize.py` (que re-anualiza asumiendo que recibe la tasa cruda del
intervalo) no anualice dos veces:

    interval_hours   = funding_interval_s / 3600
    periods_per_year = 8760 / interval_hours
    rate_per_interval = apy_fraction / periods_per_year

Si esta asunción resultara equivocada (por ejemplo, si `funding_rate` fuera
en realidad ya la tasa por intervalo y las cifras observadas fueran
simplemente tasas altas reales), el síntoma sería APRs de Variational
absurdamente altos/bajos comparados con el resto de exchanges — comprobar
esto es lo primero que habría que revisar si Variational aparece con
valores disparatados en producción.

--- Nota sobre `open_interest` (asunción, no confirmada) ---

Cada listing trae `open_interest.long_open_interest` y `.short_open_interest`
como strings. Se comprobó en vivo BTC (mark_price ≈ 77396.70,
long_open_interest ≈ 80,660,431.93) y PEOPLE (mark_price ≈ 0.00795824,
long_open_interest ≈ 3,696.99).

Interpretar estas cifras como CANTIDAD DEL ACTIVO BASE no es físicamente
plausible: 80.66 millones de BTC de open interest es imposible (el supply
total de BTC ronda los 19.5 millones). En cambio, ~80.66 millones de USD de
open interest en BTC es una cifra perfectamente normal para una plataforma
de perpetuos de tamaño medio. Por tanto, este conector asume que
`open_interest` ya viene EN USD (igual que `openInterest` en Extended,
distinto de `openInterestBase`) y usa `long_open_interest + short_open_interest`
directamente como `open_interest_usd`, sin multiplicar por mark_price.

Si esta asunción fuera errónea, se vería como cifras de OI absurdamente
bajas para activos caros (BTC, ETH) comparado con otros exchanges — es lo
segundo que habría que comprobar si algo no cuadra en producción.
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

STATS_URL = "https://omni-client-api.prod.ap-northeast-1.variational.io/metadata/stats"

HOURS_PER_YEAR = 8760.0


class VariationalConnector:
    name = "variational"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(STATS_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        listings = payload.get("listings", [])

        if not listings:
            raise RuntimeError(
                "variational: /metadata/stats no devolvió ningún listing — "
                "probablemente cambió la forma de la respuesta "
                f"(claves de nivel superior recibidas: {list(payload.keys())})"
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for row in listings:
            ticker = row.get("ticker")
            apy_raw = row.get("funding_rate")
            interval_s_raw = row.get("funding_interval_s")

            if ticker is None or apy_raw is None or interval_s_raw is None:
                skipped[str(ticker)] = "faltan campos (ticker/funding_rate/funding_interval_s)"
                continue

            try:
                apy_fraction = float(apy_raw)
                interval_hours = float(interval_s_raw) / 3600.0
            except (TypeError, ValueError) as exc:
                skipped[ticker] = f"valor no numérico: {exc}"
                continue

            if interval_hours <= 0:
                skipped[ticker] = f"funding_interval_s inválido ({interval_s_raw})"
                continue

            # Ver docstring: funding_rate viene como APY, hay que "desanualizarlo"
            # a la tasa cruda del intervalo antes de guardarlo.
            periods_per_year = HOURS_PER_YEAR / interval_hours
            rate_per_interval = apy_fraction / periods_per_year

            mark_price_raw = row.get("mark_price")
            mark_price = float(mark_price_raw) if mark_price_raw is not None else None

            oi_obj = row.get("open_interest") or {}
            long_oi_raw = oi_obj.get("long_open_interest")
            short_oi_raw = oi_obj.get("short_open_interest")
            open_interest_usd = None
            if long_oi_raw is not None and short_oi_raw is not None:
                try:
                    # Ver docstring: se asume que ya viene en USD.
                    open_interest_usd = float(long_oi_raw) + float(short_oi_raw)
                except (TypeError, ValueError):
                    open_interest_usd = None

            out.append(
                FundingRate(
                    exchange="variational",
                    venue_type=VenueType.DEX,
                    symbol=ticker,
                    raw_symbol=ticker,
                    funding_rate=rate_per_interval,
                    interval_hours=interval_hours,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=open_interest_usd,
                )
            )

        if not out:
            sample = dict(list(skipped.items())[:3])
            raise RuntimeError(
                f"variational: {len(skipped)}/{len(listings)} listings se saltaron y no quedó "
                f"ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "variational: %d/%d listings se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(listings),
                skipped,
            )

        return out


def variational() -> VariationalConnector:
    return VariationalConnector()
