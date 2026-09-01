"""
Capa fina que usa la interfaz Streamlit para pedir datos, reutilizando todo
lo construido en los Pasos 1-6. Ninguna lógica de negocio nueva vive aquí:
solo orquesta connectors -> normalize -> filtros, con cache para no golpear
los exchanges en cada re-render de Streamlit.
"""

from __future__ import annotations

from connectors.base import FundingRate, VenueType
from core.normalize import NormalizedRate, normalize_all


def build_connectors(offline: bool) -> list:
    if offline:
        from connectors.offline import binance_offline, bybit_offline, hyperliquid_offline

        return [binance_offline(), bybit_offline(), hyperliquid_offline()]

    from connectors.cex_ccxt import binance, bybit
    from connectors.dex_hyperliquid import HyperliquidConnector

    return [binance(), bybit(), HyperliquidConnector()]


def fetch_normalized_rates(offline: bool) -> tuple[list[NormalizedRate], dict[str, int]]:
    """Devuelve las tasas normalizadas + cuántos pares trajo cada exchange (para diagnóstico)."""
    connectors = build_connectors(offline)
    raw_rates: list[FundingRate] = []
    counts: dict[str, int] = {}

    for conn in connectors:
        rates = conn.fetch_funding_rates()
        counts[conn.name] = len(rates)
        raw_rates.extend(rates)

    return normalize_all(raw_rates), counts


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
