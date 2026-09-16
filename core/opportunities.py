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
from core.scoring import oi_depth, price_spread, volume_depth
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
    price_spread_pct: float | None
    consistency_pct: float | None
    consistency_samples: int
    oi_long_usd: float | None
    oi_short_usd: float | None
    oi_bottleneck_usd: float | None
    oi_bottleneck_side: str | None
    volume_long_usd: float | None
    volume_short_usd: float | None
    volume_bottleneck_usd: float | None
    volume_bottleneck_side: str | None


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
        vol_depth = volume_depth(long_leg, short_leg)
        pspread = price_spread(long_leg, short_leg)

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
                price_spread_pct=pspread.spread_pct,
                consistency_pct=cons_pct,
                consistency_samples=cons_samples,
                oi_long_usd=depth.long_oi_usd,
                oi_short_usd=depth.short_oi_usd,
                oi_bottleneck_usd=depth.bottleneck_usd,
                oi_bottleneck_side=depth.bottleneck_side,
                volume_long_usd=vol_depth.long_volume_usd,
                volume_short_usd=vol_depth.short_volume_usd,
                volume_bottleneck_usd=vol_depth.bottleneck_usd,
                volume_bottleneck_side=vol_depth.bottleneck_side,
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

    IMPORTANTE (bug real encontrado en producción, ver README — Aster/STORJ):
    a quién le hace falta esta llamada aparte NO es "quien sea CEX" — es
    "quien use CexConnector y por tanto no traiga OI en el fetch masivo de
    funding rates". Aster es un DEX (venue_type=VenueType.DEX) que en la
    práctica se comporta como un CEX en esto, porque reutiliza CexConnector
    (ver connectors/cex_ccxt.py). Filtrar aquí por venue_type==CEX dejaba a
    Aster sin pedir nunca su OI real — se quedaba en "—" (sin consultar) en
    vez de en "$0" (confirmado), así que `has_dead_liquidity()` nunca lo
    pillaba y los mercados fantasma de Aster seguían colándose en el
    ranking. El criterio correcto es "¿hay un conector con OI registrado
    para este exchange?" (`CEX_FACTORY_BY_NAME`), no el venue_type.
    """
    from connectors.cex_ccxt import CEX_FACTORY_BY_NAME

    targets: dict[OiTarget, float | None] = {}
    for opp in opportunities[:top_n]:
        for side in ("long", "short"):
            exchange = getattr(opp, f"{side}_exchange")
            if exchange in CEX_FACTORY_BY_NAME and getattr(opp, f"oi_{side}_usd") is None:
                key = (exchange, getattr(opp, f"{side}_raw_symbol"))
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


def has_dead_liquidity(opp: OpportunityRow) -> bool:
    """
    True si una de las dos piernas tiene Open Interest CONFIRMADO en $0 —
    no que no se haya podido consultar (eso es `None`, y se deja tal cual,
    como "—" en la interfaz), sino que el propio exchange respondió que
    ahora mismo no hay NINGUNA posición abierta en ese mercado.

    Encontrado en producción (ver README, sección Aster/STORJ): algunos
    exchanges (visto en Aster, vía ccxt) devuelven un funding rate normal
    para un símbolo en su endpoint masivo de funding rates aunque ese
    mercado no tenga ninguna actividad real — ni aparece en su propio
    buscador ni en su listado oficial de símbolos operables. Solo se
    descubre al pedir el Open Interest real de esa pierna en concreto
    (lo que aquí solo se hace para el top N de oportunidades — ver
    `collect_oi_targets`/`apply_oi_map`), que resulta ser exactamente $0.

    Un mercado sin ninguna posición abierta no es una oportunidad
    ejecutable por mucho que el spread de APR salga enorme — no hay nadie
    al otro lado de la operación ahí. Se usa para sacar del ranking esas
    filas "fantasma" en vez de dejar que parezcan la mejor oportunidad.
    """
    return opp.oi_long_usd == 0 or opp.oi_short_usd == 0


def has_zero_volume_leg(opp: OpportunityRow) -> bool:
    """
    True si el volumen de 24h de una de las dos piernas es CONFIRMADO
    exactamente $0 — no que no se haya podido consultar (eso es `None`, y
    se deja tal cual, como "—" en la interfaz), sino que el propio
    exchange respondió que en las últimas 24h no se movió absolutamente
    nada en ese mercado.

    Encontrado en producción (2026-09-16, mismo día que el bug de choque de
    símbolos de arriba): el usuario reportó "¿qué ha pasado con APEX?" y
    "he buscado CAKE en Extended y no sale" al ver la tabla de Ranking.
    Revisando los datos: "CAKE" (mexc/extended), "BERA" (lighter/extended)
    y "APEX" (lighter/extended) tenían, los tres, la pierna de Extended con
    un Open Interest mínimo pero NO exactamente $0 (41, 64 y 810 dólares
    respectivamente — por eso `has_dead_liquidity()` no los pillaba, ese
    filtro exige el $0 exacto) y Vol 24h EXACTAMENTE $0. El usuario
    confirmó contra la interfaz real de Extended que "CAKE" ni siquiera
    aparece listado ahí — no es un mercado real y operable, es el mismo
    patrón "fantasma" ya visto con Aster/STORJ (ver has_dead_liquidity),
    solo que aquí lo delata el volumen en vez del OI. A diferencia del OI
    Depth (que solo se pide para el top N por una llamada aparte), el
    volumen de 24h de Extended ya viene directo en el fetch masivo (ver
    connectors/dex_extended.py), así que este filtro se puede aplicar a
    TODAS las oportunidades sin gastar ninguna llamada extra.

    Un mercado sin ningún volumen en 24h no es una oportunidad ejecutable
    de verdad por mucho que el spread de APR salga enorme — nadie se ha
    movido ahí en todo un día. Se usa para sacar del ranking esas filas
    "fantasma", mismo criterio que has_dead_liquidity().
    """
    return opp.volume_long_usd == 0 or opp.volume_short_usd == 0


# Bug real encontrado en producción (2026-09-16): el panel de diagnóstico
# "Price Spread más alto" (pages/1_Funding_Rates.py) sacó a la luz que
# algunos pares que compute_opportunities() empareja por tener el mismo
# ticker corto normalizado NO son el mismo activo en las dos piernas —
# solo COINCIDEN en el nombre. Ejemplos reales, con raw_symbol/mark_price
# de cada pierna (ver README):
#
#   - "CAT": Caterpillar Inc. tokenizada en bitget (raw="CAT/USDT:USDT",
#     mark_price=$785.85) frente a un memecoin sin ninguna relación
#     también llamado "CAT" en mexc (raw="CAT_USDT",
#     mark_price=$0.000001946) — más de 400 millones de veces más barato.
#   - "RTX": Raytheon Technologies en gate ($197.64, el precio real de la
#     acción) frente a otra cosa completamente distinta en aster ($0.71).
#   - "HK50": el índice Hang Seng — mexc lo cotiza en 24638.7 (su nivel
#     real de mercado en esa fecha) mientras que gate lo cotiza en 3143.0.
#   - "XIAOMI" y "PURR" (ratios de 7-110x entre piernas) también entran en
#     este patrón, aunque con menos certeza sobre cuál es el activo real.
#
# Cuando pasa esto, TODA la fila es basura — el Spread APR también está
# comparando el funding rate de dos activos sin relación, no solo el
# Price Spread — así que no basta con ocultar una columna con
# IMPLAUSIBLE_SPREAD_PCT (core/scoring.py; ese umbral, 1000%, está
# calibrado para casos todavía más extremos, ver su propio docstring): hay
# que sacar la oportunidad ENTERA del ranking, igual que ya se hace con
# has_dead_liquidity() para el OI $0 confirmado — mismo patrón a propósito
# (una función que solo pregunta "¿se descarta?", y es quien llama —
# pages/1_Funding_Rates.py — quien filtra Y enseña por qué en un panel de
# diagnóstico, para no tirar datos en silencio).
#
# El umbral de abajo sale directamente de los datos reales de ese mismo
# panel de diagnóstico (ver README para la lista completa): TODO lo
# confirmado como choque de símbolos salió >= 87% (HK50, el caso más
# bajo); TODO lo que parece divergencia real del mismo activo en un
# mercado poco líquido salió <= 41% (CAKE, el caso más alto de esa otra
# categoría). 75% deja margen de sobra a los dos lados sin depender de un
# número pegado al límite de ninguno de los dos grupos.
IMPLAUSIBLE_PRICE_PAIR_PCT = 75.0


def has_implausible_price_pair(opp: OpportunityRow) -> bool:
    """
    True si el Price Spread de esta oportunidad es tan alto que, con la
    evidencia real de producción (ver el comentario de arriba de
    IMPLAUSIBLE_PRICE_PAIR_PCT), es mucho más probable que las dos piernas
    sean dos activos DISTINTOS que casualmente comparten el mismo ticker
    normalizado, que una divergencia de precio real del mismo activo entre
    dos exchanges.
    """
    return opp.price_spread_pct is not None and opp.price_spread_pct > IMPLAUSIBLE_PRICE_PAIR_PCT


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
