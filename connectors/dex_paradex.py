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

            mark_price_raw = row.get("mark_price")
            mark_price = float(mark_price_raw) if mark_price_raw is not None else None

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

        return out


def paradex() -> ParadexConnector:
    return ParadexConnector()
