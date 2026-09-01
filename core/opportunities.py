"""
Cálculo de oportunidades compartido entre el CLI y la interfaz Streamlit —
para no tener la misma lógica de "mejor long/short + consistency + OI depth"
escrita dos veces en dos sitios que se puedan desincronizar.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from connectors.base import VenueType
from core.normalize import NormalizedRate
from core.scoring import oi_depth
from core.scoring import consistency_score as _consistency_score

logger = logging.getLogger(__name__)


@dataclass
class OpportunityRow:
    symbol: str
    long_exchange: str
    long_venue: VenueType
    long_raw_symbol: str
    long_apr: float
    long_mark_price: float | None
    short_exchange: str
    short_venue: VenueType
    short_raw_symbol: str
    short_apr: float
    short_mark_price: float | None
    spread_apr: float
    consistency_pct: float | None
    consistency_samples: int
    oi_long_usd: float | None
    oi_short_usd: float | None
    oi_bottleneck_usd: float | None
    oi_bottleneck_side: str | None


def compute_opportunities(
    rates: list[NormalizedRate], history_conn: sqlite3.Connection | None
) -> list[OpportunityRow]:
    by_symbol: dict[str, list[NormalizedRate]] = defaultdict(list)
    for r in rates:
        by_symbol[r.symbol].append(r)

    rows: list[OpportunityRow] = []
    for symbol, group in by_symbol.items():
        if len(group) < 2:
            continue

        long_leg = min(group, key=lambda r: r.apr_pct)
        short_leg = max(group, key=lambda r: r.apr_pct)
        spread = short_leg.apr_pct - long_leg.apr_pct

        cons_pct: float | None = None
        cons_samples = 0
        if history_conn is not None:
            cons = _consistency_score(history_conn, long_leg.exchange, short_leg.exchange, symbol)
            cons_pct = cons.score_pct
            cons_samples = cons.samples

        depth = oi_depth(long_leg, short_leg)

        rows.append(
            OpportunityRow(
                symbol=symbol,
                long_exchange=long_leg.exchange,
                long_venue=long_leg.venue_type,
                long_raw_symbol=long_leg.raw_symbol,
                long_apr=long_leg.apr_pct,
                long_mark_price=long_leg.mark_price,
                short_exchange=short_leg.exchange,
                short_venue=short_leg.venue_type,
                short_raw_symbol=short_leg.raw_symbol,
                short_apr=short_leg.apr_pct,
                short_mark_price=short_leg.mark_price,
                spread_apr=spread,
                consistency_pct=cons_pct,
                consistency_samples=cons_samples,
                oi_long_usd=depth.long_oi_usd,
                oi_short_usd=depth.short_oi_usd,
                oi_bottleneck_usd=depth.bottleneck_usd,
                oi_bottleneck_side=depth.bottleneck_side,
            )
        )

    rows.sort(key=lambda r: r.spread_apr, reverse=True)
    return rows


OiTarget = tuple[str, str]  # (exchange, raw_symbol) — clave para aplicar el resultado sobre una oportunidad
OiRequest = tuple[str, str, float | None]  # (exchange, raw_symbol, mark_price) — lo que de verdad se pide


def collect_oi_targets(opportunities: list[OpportunityRow], top_n: int = 10) -> tuple[OiRequest, ...]:
    """
    Qué piernas de las `top_n` mejores oportunidades todavía no tienen OI
    (típicamente los CEX — Hyperliquid ya lo trae en el fetch original).

    Deliberadamente limitado a `top_n`: pedir OI símbolo a símbolo para las
    miles de oportunidades sería lento y quemaría el rate limit para nada —
    solo importa la profundidad de las pocas que ya decidiste mirar.

    Se lleva también el mark_price de cada pierna (ya lo tenemos del fetch de
    funding rates) porque algún exchange (bitget, por ejemplo) responde el
    open interest en contratos pero sin resolver a USD — ahí hace falta el
    precio para poder calcularlo nosotros mismos (contratos × precio).

    Devuelve una tupla (hashable) a propósito, para poder cachear el fetch
    en la capa que lo llame (la página Streamlit) sin tener que hacer
    hashable un dataclass mutable.
    """
    targets: dict[OiTarget, float | None] = {}
    for opp in opportunities[:top_n]:
        for side in ("long", "short"):
            if getattr(opp, f"{side}_venue") == VenueType.CEX and getattr(opp, f"oi_{side}_usd") is None:
                key = (getattr(opp, f"{side}_exchange"), getattr(opp, f"{side}_raw_symbol"))
                targets.setdefault(key, getattr(opp, f"{side}_mark_price"))
    return tuple(sorted((exchange, raw_symbol, price) for (exchange, raw_symbol), price in targets.items()))


def fetch_oi_for_targets(
    requests: tuple[OiRequest, ...],
) -> tuple[dict[OiTarget, float], dict[OiTarget, str]]:
    """
    Pide el OI real a cada exchange CEX para la lista de (exchange, raw_symbol,
    mark_price) dada. Entrada y salida son hashable/serializables a propósito,
    para que quien llame (la página Streamlit) pueda envolver esto en su
    propia caché sin arrastrar objetos de conexión.

    Devuelve (oi_por_target, errores_por_target) — igual que
    fetch_normalized_rates() en core/data_service.py, el motivo de un fallo
    se expone en vez de tragárselo, para poder ver en la interfaz POR QUÉ
    bitget o gate no dan profundidad en vez de solo ver un "—" sin explicar.
    """
    from connectors.cex_ccxt import CEX_FACTORY_BY_NAME

    by_exchange: dict[str, list[tuple[str, float | None]]] = defaultdict(list)
    for exchange, raw_symbol, mark_price in requests:
        by_exchange[exchange].append((raw_symbol, mark_price))

    result: dict[OiTarget, float] = {}
    errors: dict[OiTarget, str] = {}
    for exchange, symbol_requests in by_exchange.items():
        factory = CEX_FACTORY_BY_NAME.get(exchange)
        if factory is None:
            for raw_symbol, _ in symbol_requests:
                errors[(exchange, raw_symbol)] = "exchange sin conector OI registrado (CEX_FACTORY_BY_NAME)"
            continue
        try:
            oi_map, oi_errors = factory().fetch_open_interest_usd(symbol_requests)
        except Exception as exc:
            logger.debug("Sin OI disponible para %s", exchange, exc_info=True)
            for raw_symbol, _ in symbol_requests:
                errors[(exchange, raw_symbol)] = f"{type(exc).__name__}: {exc}"
            continue
        for raw_symbol, value in oi_map.items():
            result[(exchange, raw_symbol)] = value
        for raw_symbol, msg in oi_errors.items():
            errors[(exchange, raw_symbol)] = msg

    return result, errors


def apply_oi_map(opportunities: list[OpportunityRow], oi_map: dict[OiTarget, float], top_n: int = 10) -> None:
    """Aplica el resultado de fetch_oi_for_targets() sobre las oportunidades — MUTA in-place, sin red."""
    for opp in opportunities[:top_n]:
        for side in ("long", "short"):
            oi_attr = f"oi_{side}_usd"
            if getattr(opp, oi_attr) is not None:
                continue
            key = (getattr(opp, f"{side}_exchange"), getattr(opp, f"{side}_raw_symbol"))
            if key in oi_map:
                setattr(opp, oi_attr, oi_map[key])

        if opp.oi_long_usd is not None and opp.oi_short_usd is not None:
            opp.oi_bottleneck_usd = min(opp.oi_long_usd, opp.oi_short_usd)
            opp.oi_bottleneck_side = "long" if opp.oi_long_usd <= opp.oi_short_usd else "short"


def enrich_oi_depth(opportunities: list[OpportunityRow], top_n: int = 10) -> dict[OiTarget, str]:
    """
    Atajo sin caché: collect + fetch + apply en un solo paso. Pensado para el
    CLI (proceso de un solo uso). Devuelve los errores por target, por si
    quien llama quiere mostrarlos (el CLI los imprime; ver cli.py).
    """
    targets = collect_oi_targets(opportunities, top_n)
    oi_map, errors = fetch_oi_for_targets(targets)
    apply_oi_map(opportunities, oi_map, top_n)
    return errors
