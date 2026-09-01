"""
Capa fina que usa la interfaz Streamlit para pedir datos, reutilizando todo
lo construido en los Pasos 1-6. Ninguna lógica de negocio nueva vive aquí:
solo orquesta connectors -> normalize -> filtros, con cache para no golpear
los exchanges en cada re-render de Streamlit.
"""

from __future__ import annotations

import logging

from connectors.base import FundingRate, VenueType
from core.normalize import NormalizedRate, normalize_all

logger = logging.getLogger(__name__)


def build_connectors(offline: bool) -> list:
    if offline:
        from connectors.offline import binance_offline, bybit_offline, hyperliquid_offline

        return [binance_offline(), bybit_offline(), hyperliquid_offline()]

    from connectors.cex_ccxt import ALL_CEX_FACTORIES
    from connectors.dex_hyperliquid import HyperliquidConnector

    # Varios CEX a la vez, no solo Binance/Bybit: si uno bloquea la IP del
    # servidor (Binance Futures lo hace con bastantes proveedores cloud), los
    # demás siguen respondiendo en vez de dejar la tabla vacía.
    return [factory() for factory in ALL_CEX_FACTORIES] + [HyperliquidConnector()]


def fetch_normalized_rates(
    offline: bool,
) -> tuple[list[NormalizedRate], dict[str, int], dict[str, str]]:
    """
    Devuelve (tasas normalizadas, pares por exchange, errores por exchange).

    Un exchange que falla NO tira abajo a los demás — si Binance está caído o
    bloqueado, seguimos enseñando lo que sí trajeron Bybit e Hyperliquid — pero
    el motivo del fallo se guarda en `errors` para que se pueda enseñar en la
    interfaz en vez de desaparecer en un log que nadie ve.
    """
    connectors = build_connectors(offline)
    raw_rates: list[FundingRate] = []
    counts: dict[str, int] = {}
    errors: dict[str, str] = {}

    for conn in connectors:
        try:
            rates = conn.fetch_funding_rates()
        except Exception as exc:  # noqa: BLE001 — queremos capturar cualquier fallo de red/API
            logger.exception("Fallo al pedir funding rates a %s", conn.name)
            counts[conn.name] = 0
            errors[conn.name] = f"{type(exc).__name__}: {exc}"
            continue

        counts[conn.name] = len(rates)
        raw_rates.extend(rates)

    return normalize_all(raw_rates), counts, errors


def filter_rates(
    rates: list[NormalizedRate], venue: str, exchanges: list[str] | None
) -> list[NormalizedRate]:
    out = rates
    if venue != "all":
        wanted = VenueType.CEX if venue == "cex" else VenueType.DEX
        out = [r for r in out if r.venue_type == wanted]
    if exchanges:
        wanted_ex = {e.lower() for e in exchanges}
        out = [r for r in out if r.exchange.lower() in wanted_ex]
    return out
