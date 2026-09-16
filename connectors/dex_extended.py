"""
Conector para Extended — DEX de perpetuos sobre Starknet.

Un solo endpoint trae todos los mercados con sus stats anidadas en
`marketStats` (funding rate, mark price, open interest...).

Docs: https://api.docs.extended.exchange/ (GET /api/v1/info/markets)

Nota sobre unidades: a diferencia de Hyperliquid/Lighter/Paradex, Extended
documenta explícitamente DOS campos de open interest — `openInterest` (en el
activo de colateral, es decir ya en USD) y `openInterestBase` (en el activo
base). Usamos `openInterest` directamente, sin multiplicar por mark price.

--- Nota sobre volumen 24h (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El mismo `marketStats` sigue el mismo patrón que con open interest: trae DOS
campos de volumen de 24h — `dailyVolume` (ej. XRP-USD en vivo:
"35071518.026000", en el activo de colateral, es decir ya en USD) y
`dailyVolumeBase` (ej. XRP-USD: "25462558", en el activo base). Igual que con
`openInterest`, se usa `dailyVolume` DIRECTAMENTE, sin multiplicar por mark
price:

    volume_24h_usd = dailyVolume   (directo, sin conversión)

--- Mercados fantasma sospechados (bug real reportado en producción, 2026-09-16) ---

El usuario vio en el ranking "CAKE" (mexc/extended), "BERA" (lighter/extended)
y "APEX" (lighter/extended) con la pierna de Extended mostrando un Open
Interest mínimo (decenas/cientos de $) y Vol 24h EXACTAMENTE $0, y confirmó
contra la interfaz real de Extended que "CAKE" ni siquiera aparece listado
ahí — parece el mismo patrón de mercado "fantasma" ya conocido con Aster/
STORJ (ver README y core/opportunities.py::has_dead_liquidity), solo que aquí
el OI no llega a ser exactamente $0 así que solo el volumen lo delata. Ya se
descartan del ranking (ver has_zero_volume_leg en core/opportunities.py), pero
eso es un parche corriente abajo, no arregla la causa raíz: puede que este
mismo endpoint bulk traiga un campo de estado (algo tipo "status"/"active"/
"tradingEnabled" en `row` o en `marketStats`) que señale que el mercado está
inactivo/delistado, igual que ccxt expone `active` para Aster — y que
simplemente no se esté mirando todavía. Se añadió un diagnóstico
(`logger.warning`, ver más abajo) que vuelca la fila CRUDA completa (todos
los campos, no solo los que ya se usan) para hasta 6 mercados con Vol 24h
calculado = $0, pendiente de un despliegue más para confirmarlo con datos
reales antes de filtrar en el origen en vez de corriente abajo.
"""

from __future__ import annotations

import json
import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

MARKETS_URL = "https://api.starknet.extended.exchange/api/v1/info/markets"

# Extended liquida funding cada hora (aunque la tasa se calcula sobre una
# ventana de 8h — ver docs.extended.exchange/extended-resources/trading/funding-payments).
INTERVAL_HOURS = 1


class ExtendedConnector:
    name = "extended"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        resp = self._session.get(MARKETS_URL, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", [])

        # Diagnóstico (2026-09-16): el usuario reportó "¿qué ha pasado con
        # APEX?" y "he buscado CAKE en Extended y no sale" — CAKE/BERA/APEX
        # traían, los tres, OI mínimo-pero-no-cero y Vol 24h exactamente $0
        # en la pierna de Extended (ver has_zero_volume_leg en
        # core/opportunities.py, ya se descartan del ranking). El usuario
        # confirmó contra la interfaz real de Extended que "CAKE" ni
        # siquiera aparece listado ahí -- mismo patrón "fantasma" que
        # Aster/STORJ (README), pero para ese caso el conector de ccxt SÍ
        # tiene un campo `active`/estado que lo detecta en origen (ver
        # cex_ccxt.py). Extended es un conector propio (no ccxt) y hasta
        # ahora no comprobaba ningún campo de estado -- puede que
        # `marketStats`/`row` sí traiga uno (ej. "status"/"active"/
        # "tradingEnabled") y simplemente no se estuviera mirando. Se
        # vuelca aquí la fila CRUDA completa (todos los campos, no solo los
        # que ya usamos) para hasta 6 mercados con esta firma sospechosa
        # (Vol 24h calculado = 0), para poder comparar contra la respuesta
        # real y encontrar el campo correcto en vez de seguir adivinando
        # solo por el síntoma downstream.
        ghost_diagnostic_samples: list[dict] = []

        out: list[FundingRate] = []
        for row in rows:
            if row.get("type") not in (None, "PERPETUAL"):
                continue  # nos saltamos mercados spot si el endpoint los mezclara

            raw_symbol = row.get("name")  # ej. "BTC-USD"
            stats = row.get("marketStats") or {}
            rate = stats.get("fundingRate")
            if raw_symbol is None or rate is None:
                continue

            symbol = raw_symbol.split("-")[0]
            mark_price_raw = stats.get("markPrice")
            mark_price = float(mark_price_raw) if mark_price_raw is not None else None

            oi_raw = stats.get("openInterest")  # ya en USD (activo de colateral)
            oi_usd = float(oi_raw) if oi_raw is not None else None

            # Ver docstring: dailyVolume ya viene en USD (activo de colateral),
            # igual patrón que openInterest — sin conversión.
            volume_24h_raw = stats.get("dailyVolume")
            volume_24h_usd = float(volume_24h_raw) if volume_24h_raw is not None else None

            if volume_24h_usd == 0 and len(ghost_diagnostic_samples) < 6:
                ghost_diagnostic_samples.append(row)

            out.append(
                FundingRate(
                    exchange="extended",
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

        if ghost_diagnostic_samples:
            logger.warning(
                "extended DIAGNÓSTICO %d mercado(s) con Vol 24h calculado = $0 (posible fantasma, "
                "ver docstring más arriba y has_zero_volume_leg en core/opportunities.py) — fila "
                "CRUDA completa tal cual la API, para buscar un campo de estado que no se esté "
                "mirando todavía: %s",
                len(ghost_diagnostic_samples),
                json.dumps(ghost_diagnostic_samples)[:4000],
            )

        return out


def extended() -> ExtendedConnector:
    return ExtendedConnector()
