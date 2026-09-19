"""
Paso 3 — Normalización de intervalos y cálculo de APR.

Este es el problema que resuelve Loris y que ProFunding no explica: cada
exchange liquida el funding a un ritmo distinto (Hyperliquid cada 1h, Binance
cada 8h...). Comparar la tasa "en bruto" de dos exchanges sin tener esto en
cuenta es comparar peras con manzanas — una tasa de 0.01% cada 1h es MUCHO
más que una de 0.01% cada 8h (8 veces más, de hecho).

La solución: convertir todo a una misma base antes de comparar nada. Usamos
el APR anualizado como base común, que es lo que de verdad le importa a
alguien evaluando si le compensa la operación.

Fórmula: APR = tasa_del_intervalo × (horas_en_un_año / horas_del_intervalo)

Ej.: Binance, ETH al +0.10% cada 8h
     APR = 0.0010 × (8760 / 8) = 0.0010 × 1095 = 1.095 → 109.5% anual

     Hyperliquid, ETH al -0.0020% cada 1h
     APR = -0.0000200 × (8760 / 1) = -0.0000200 × 8760 = -0.1752 → -17.52% anual

Ahora sí son comparables entre sí, aunque un exchange liquide 8 veces más
seguido que el otro.
"""

from __future__ import annotations

from dataclasses import dataclass

from connectors.base import FundingRate, VenueType

HOURS_PER_YEAR = 8760  # 365 * 24

# Hallazgo #17 de la auditoría (2026-09-19), confirmado con evidencia real al
# verificar el export CSV del Ranking que pidió el usuario: PEPE (okx +10,9%/
# mexc +31,0%) y 1000PEPE (edgex +10,9%/extended +30,7%) son el mismo activo
# con APR casi idéntico en las dos piernas, pero como el símbolo no coincide
# nunca se emparejan en compute_opportunities() — una oportunidad real se
# pierde en silencio. "1000X" es una convención real y extendida en el sector
# (Binance, Bitget, MEXC, ApeX, edgeX, Extended...) para activos con precio
# unitario muy bajo (memecoins sobre todo): "1000PEPE" es un contrato de 1000
# PEPE, "1MBABYDOGE" de 1.000.000 — el propio universo de símbolos visto en
# producción en este proyecto (ver README) ya trae varios: 1000000MOG,
# 1000RATS, 1000PEPE, 1000BONK, 1MBABYDOGE, 1000CHEEMS, 1000SHIB, 1000FLOKI.
#
# Lista cerrada de prefijos conocidos a propósito, NO un regex genérico tipo
# "^\d+" — símbolos reales de ese mismo universo como "0G" (0G Labs) o "2Z"
# empiezan por dígito sin ser un multiplicador de contrato; un regex genérico
# los emparejaría con activos sin ninguna relación. Orden de mayor a menor
# longitud de prefijo a propósito, para que "1000000MOG" no se recorte mal
# como "1000" + "000MOG" antes de probar el prefijo "1000000" completo.
_SYMBOL_MULTIPLIER_PREFIXES: tuple[tuple[str, float], ...] = (
    ("1000000", 1_000_000.0),
    ("100000", 100_000.0),
    ("10000", 10_000.0),
    ("1000", 1_000.0),
    ("1M", 1_000_000.0),
)


def _canonical_symbol(raw_symbol: str) -> tuple[str, float]:
    """
    Separa un prefijo de multiplicador de contrato conocido (ver
    `_SYMBOL_MULTIPLIER_PREFIXES`) del resto del símbolo.

    Devuelve (símbolo_canónico, multiplicador) — (`raw_symbol`, 1.0) si no
    hay ningún prefijo reconocido. Solo recorta si lo que queda después del
    prefijo empieza por una letra (para no partir un símbolo que solo
    resulta tener más ceros por casualidad).
    """
    for prefix, multiplier in _SYMBOL_MULTIPLIER_PREFIXES:
        if raw_symbol.startswith(prefix) and len(raw_symbol) > len(prefix):
            rest = raw_symbol[len(prefix):]
            if rest[:1].isalpha():
                return rest, multiplier
    return raw_symbol, 1.0


@dataclass
class NormalizedRate:
    """Un FundingRate ya convertido a una base comparable entre exchanges."""

    exchange: str
    venue_type: VenueType
    symbol: str
    raw_symbol: str          # símbolo tal cual lo usa el exchange — hace falta para pedir el OI después
    raw_rate: float          # tasa tal cual la reportó el exchange, para su intervalo nativo
    interval_hours: float
    apr_pct: float           # tasa anualizada, en % → esto es lo que se compara entre exchanges
    rate_per_8h_pct: float   # tasa reescalada a "cada 8h", en % → referencia rápida, estilo Loris
    mark_price: float | None
    open_interest_usd: float | None
    volume_24h_usd: float | None
    # Hallazgo #17 — multiplicador de contrato ya integrado en el símbolo
    # original (ej. 1000.0 para "1000PEPE"), 1.0 si no tenía prefijo
    # conocido. Ver price_spread() en core/scoring.py: se usa para negarse a
    # comparar el mark_price de dos piernas con escalas distintas en vez de
    # inventar un Price Spread — el propio símbolo ya viene canonicalizado
    # (sin el prefijo) para que compute_opportunities() SÍ las empareje.
    # Default 1.0 (sin multiplicador) para no romper construcciones
    # existentes de NormalizedRate en tests que no conocen este campo.
    symbol_multiplier: float = 1.0

    @property
    def pays_longs(self) -> bool:
        """True si, con esta tasa, quien está LONG cobra (tasa negativa)."""
        return self.raw_rate < 0

    @property
    def pays_shorts(self) -> bool:
        """True si, con esta tasa, quien está SHORT cobra (tasa positiva)."""
        return self.raw_rate > 0


def normalize(rate: FundingRate) -> NormalizedRate:
    """Convierte un FundingRate crudo a su forma normalizada (APR anualizado)."""

    if rate.interval_hours <= 0:
        raise ValueError(
            f"interval_hours inválido ({rate.interval_hours}) para {rate.exchange}/{rate.symbol}"
        )

    periods_per_year = HOURS_PER_YEAR / rate.interval_hours
    apr = rate.funding_rate * periods_per_year * 100

    periods_per_8h = 8 / rate.interval_hours
    rate_per_8h = rate.funding_rate * periods_per_8h * 100

    # Hallazgo #17 — ver _canonical_symbol()/_SYMBOL_MULTIPLIER_PREFIXES
    # arriba. Se hace aquí, en el único funnel por el que pasan TODOS los
    # FundingRate de TODOS los conectores (ver normalize_all()), en vez de
    # en cada conector por separado — un solo sitio que mantener, y ningún
    # conector nuevo puede olvidarse de aplicarlo.
    canonical_symbol, symbol_multiplier = _canonical_symbol(rate.symbol)

    return NormalizedRate(
        exchange=rate.exchange,
        venue_type=rate.venue_type,
        symbol=canonical_symbol,
        raw_symbol=rate.raw_symbol,
        raw_rate=rate.funding_rate,
        interval_hours=rate.interval_hours,
        apr_pct=apr,
        rate_per_8h_pct=rate_per_8h,
        mark_price=rate.mark_price,
        open_interest_usd=rate.open_interest_usd,
        volume_24h_usd=rate.volume_24h_usd,
        symbol_multiplier=symbol_multiplier,
    )


def normalize_all(rates: list[FundingRate]) -> list[NormalizedRate]:
    return [normalize(r) for r in rates]
