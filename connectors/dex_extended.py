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

--- Mercados fantasma: bug real, y luego el primer intento de arreglo
    también estaba mal (2026-09-16) ---

El usuario vio en el ranking "CAKE" (mexc/extended), "BERA" (lighter/extended)
y "APEX" (lighter/extended) con la pierna de Extended mostrando un Open
Interest mínimo y Vol 24h EXACTAMENTE $0, y confirmó contra la interfaz real
de Extended que "CAKE" ni siquiera aparece listado ahí. Primer intento de
arreglo: descartar del ranking cualquier oportunidad con Vol 24h == $0
confirmado en una pierna (`has_zero_volume_leg`, ahora RETIRADO — ver
core/opportunities.py). Se añadió también un diagnóstico que volcaba la fila
CRUDA completa de `row` para los mercados con Vol 24h = $0, para buscar la
causa raíz en vez de quedarse con el parche corriente abajo.

**Ese diagnóstico, con datos reales, demostró que el parche corriente abajo
estaba MAL** — no simplemente incompleto, sino que descartaba mercados
completamente legítimos. La fila cruda trajo dos casos reales y muy
distintos entre sí:

    {"name": "INTU-USD", "category": "RWA", "subCategory": "Equity",
     "active": true, "status": "ACTIVE", "isOffHours": true,
     "tradingHours": "NO_OVERNIGHT",
     "marketStats": {"dailyVolume": "0.000000", ...}}

    {"name": "NOW_24_5-USD", "category": "RWA", "subCategory": "Equity",
     "active": true, "status": "DELISTED", "tradingHours": "WEEKDAYS",
     "marketStats": {"dailyVolume": "0", ...}}

INTU (Intuit, una acción tokenizada real) tiene `status: "ACTIVE"` — es un
mercado real y operable, solo que ahora mismo está `isOffHours: true`
("NO_OVERNIGHT": fuera del horario de mercado de la bolsa real, como
cualquier acción de EEUU fuera de las 9:30-16:00 ET) — Vol 24h = $0 en ese
momento es exactamente lo esperable, NO un mercado fantasma. NOW_24_5
(ServiceNow) sí tiene `status: "DELISTED"` — ese SÍ es el mercado muerto de
verdad. La prueba de fuego: revisando el aviso de "65 oportunidades
descartadas" que generó `has_zero_volume_leg()`, ahí aparecía "INTU" junto a
una veintena más de tickers de acciones reales (ABNB, ADSK, AXON, BKNG,
DDOG, GILD, GPS, HIMS, JCI, LIN, MCHP, MELI, MPWR, MRNA, REGN, RIOT, TEAM,
TMUS, VRTX...) — el filtro de volumen estaba tirando del ranking mercados
RWA perfectamente legítimos solo por estar fuera de su horario de bolsa,
que es su estado normal la mayor parte del día.

**Fix correcto, en el origen**: usar el campo `status` que la propia API ya
trae — igual patrón que `active` en ccxt para Aster (ver cex_ccxt.py) — en
vez de inferir "fantasma" por un síntoma downstream (volumen) que también
lo produce una causa perfectamente legítima (horario de mercado). Se
descartan aquí los mercados con `status` distinto de "ACTIVE" (de momento
solo se ha visto "DELISTED" en producción, pero cualquier estado que no sea
"ACTIVE" se trata igual); un `status` ausente NO se descarta (no hay
evidencia de que signifique nada malo, se deja pasar). `has_zero_volume_leg()`
se retiró — un mercado ACTIVE fuera de horario es una operación real que
ahora mismo no tiene volumen, no una fila fantasma.
"""

from __future__ import annotations

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

        # Ver docstring del módulo, sección "Mercados fantasma": confirmado
        # con datos reales que `status` (no el volumen) es la señal fiable
        # de mercado muerto — "DELISTED" en producción para NOW_24_5-USD,
        # frente a "ACTIVE" para un mercado real simplemente fuera de
        # horario de bolsa (INTU-USD, Vol 24h = $0 ahí mismo pero legítimo).
        # Un `status` ausente no se descarta -- no hay evidencia de que
        # signifique nada malo.
        ghost_symbols: list[str] = []

        out: list[FundingRate] = []
        for row in rows:
            if row.get("type") not in (None, "PERPETUAL"):
                continue  # nos saltamos mercados spot si el endpoint los mezclara

            status = row.get("status")
            if status is not None and status != "ACTIVE":
                ghost_symbols.append(f"{row.get('name')} (status={status})")
                continue

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

        if ghost_symbols:
            logger.warning(
                "extended: %d mercado(s) descartado(s) por status distinto de ACTIVE (delistado, "
                "ver README/docstring de este módulo): %s",
                len(ghost_symbols),
                ghost_symbols[:10],
            )

        return out


def extended() -> ExtendedConnector:
    return ExtendedConnector()
