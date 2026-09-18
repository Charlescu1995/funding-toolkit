"""
Capa fina que usa la interfaz Streamlit para pedir datos, reutilizando todo
lo construido en los Pasos 1-6. Ninguna lógica de negocio nueva vive aquí:
solo orquesta connectors -> normalize -> filtros, con cache para no golpear
los exchanges en cada re-render de Streamlit.
"""

from __future__ import annotations

import logging
from collections import Counter

from connectors.base import FundingRate, VenueType
from core.normalize import NormalizedRate, normalize_all

logger = logging.getLogger(__name__)


def build_connectors(offline: bool) -> list:
    if offline:
        from connectors.offline import binance_offline, bybit_offline, hyperliquid_offline

        return [binance_offline(), bybit_offline(), hyperliquid_offline()]

    from connectors.cex_ccxt import ALL_CEX_FACTORIES
    from connectors.dex_registry import ALL_DEX_FACTORIES

    # Varios CEX a la vez, no solo Binance/Bybit: si uno bloquea la IP del
    # servidor (Binance Futures lo hace con bastantes proveedores cloud), los
    # demás siguen respondiendo en vez de dejar la tabla vacía. Lo mismo con
    # los DEX: si Lighter o Paradex fallan, Hyperliquid y el resto siguen.
    return [factory() for factory in ALL_CEX_FACTORIES] + [factory() for factory in ALL_DEX_FACTORIES]


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

        # Investigando (2026-09-18, ver README): compute_opportunities() está
        # emparejando oportunidades donde long_exchange == short_exchange con
        # raw_symbol IDÉNTICO en las dos piernas — confirmado en producción,
        # de momento solo visto en bitget (128/128 casos con raw_symbol
        # idéntico, 0 con raw_symbol distinto). Para saber si el duplicado ya
        # viene así del propio fetch_funding_rates() del conector (ccxt
        # devolviendo el mismo símbolo dos veces) o se genera DESPUÉS en este
        # pipeline (algo acumulando entradas de más al fusionar varios
        # conectores), se comprueba aquí mismo, nada más salir de CADA
        # conector por separado, antes de fusionar nada. Solo diagnóstico —
        # no se descarta ni modifica ningún dato.
        raw_symbol_counts = Counter(r.raw_symbol for r in rates)
        dup_raw_symbols = {sym: n for sym, n in raw_symbol_counts.items() if n > 1}
        if dup_raw_symbols:
            logger.warning(
                "%s: su propio fetch_funding_rates() ya devolvió %d raw_symbol "
                "repetido(s) DENTRO de la misma llamada, antes de tocar nada de este "
                "pipeline — confirma que el duplicado viene del conector/exchange, no "
                "de aquí. Muestra (raw_symbol: nº de veces): %s",
                conn.name,
                len(dup_raw_symbols),
                dict(list(dup_raw_symbols.items())[:10]),
            )

        raw_rates.extend(rates)

    # Mismo chequeo, pero sobre el TOTAL ya fusionado de todos los conectores
    # — si un (exchange, raw_symbol) aparece 2+ veces aquí pero NO apareció
    # arriba en ningún conector individual, el duplicado se está generando en
    # este bucle (algo se está fusionando/repitiendo entre conectores), no
    # dentro de cada conector por separado.
    combined_counts = Counter((r.exchange, r.raw_symbol) for r in raw_rates)
    dup_combined = {k: n for k, n in combined_counts.items() if n > 1}
    if dup_combined:
        logger.warning(
            "TOTAL fusionado de todos los conectores: %d (exchange, raw_symbol) "
            "repetido(s) tras juntar todos los conectores — muestra: %s",
            len(dup_combined),
            dict(list(dup_combined.items())[:10]),
        )

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
