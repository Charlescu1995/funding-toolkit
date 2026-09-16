"""
Paso 5 — Consistency Score y OI Depth.

Estas dos métricas son las que le robamos a John5Cripto: la pieza que falta
entre "este spread tiene un APR altísimo" y "esto es de fiar".

Consistency Score
------------------
Un spread puede tener un APR enorme ahora mismo y aun así ser una trampa si
lleva revirtiéndose cada pocas horas (un día pagan los longs, al siguiente
pagan los shorts). El score mide qué porcentaje del tiempo, en la ventana
elegida, la asignación long/short que recomendamos se habría mantenido a tu
favor (spread > 0).

    100%  -> siempre fue rentable en esa dirección durante la ventana
     50%  -> básicamente una moneda al aire
      0%  -> se lo llevó siempre el lado contrario

No es una garantía de futuro, es una foto del pasado — pero es infinitamente
mejor que decidir solo con la tasa de este instante.

OI Depth
--------
Cuánto open interest hay en cada pierna de la operación. No es exactamente
"cuánto puedes meter sin mover el precio" (para eso haría falta el libro de
órdenes, que es un dato más caro de conseguir), pero es la aproximación
barata estándar: si una de las dos piernas tiene un OI muy bajo comparado con
el tamaño que quieres mover, esa es la señal de alerta de que vas a sufrir
slippage entrando o saliendo.

Volume 24h (Paso 6, punto 2 de la comparativa con Kusi/Smartbitrage)
--------------------------------------------------------------------
El Open Interest dice cuánto hay abierto AHORA MISMO en cada pierna; el
volumen de 24h dice cuánto se ha estado MOVIENDO — un mercado puede tener un
OI decente pero estar prácticamente congelado (nadie entra ni sale), lo que
en la práctica significa que entrar o salir de tu propia posición va a mover
el precio más de lo que el OI por sí solo sugeriría. Son señales de liquidez
complementarias, no intercambiables, por eso se muestran las dos por
separado en vez de fundirlas en un único número.

Price Spread (Paso 6, punto 3 de la comparativa con Kusi/Smartbitrage)
------------------------------------------------------------------------
Un APR de spread altísimo no sirve de nada si para conseguirlo hay que
comprar la pierna long a un precio y vender la pierna short a otro precio
bastante distinto — la diferencia de precio (`mark_price`) entre exchanges se
come de un plumazo varios días de funding acumulado, porque es un coste que
se paga una sola vez, al entrar (y otra vez al salir, si los precios no han
vuelto a converger). Es habitual que sea pequeño (unas pocas décimas de %)
entre CEX grandes en el mismo símbolo, pero puede dispararse en símbolos poco
líquidos o en RWA (acciones/oro tokenizado), donde cada exchange puede llevar
su propio índice de precio. Por eso Kusi/Smartbitrage lo tratan como un dato
de riesgo aparte del spread de funding, no como parte del mismo número.

--- Guardia de cordura (BUG REAL encontrado en producción, Paso 6) ---
Un usuario reportó en producción un Price Spread de ~100.000.000.000% (cien
mil millones por ciento) en filas con GRVT como pierna, con el comentario
"no creo que sea cierto" — con razón: ningún par de precios reales del MISMO
activo entre dos exchanges se separa ni remotamente a ese orden de magnitud.
La causa raíz identificada fue un bug de escala en open_interest/volumen del
conector de GRVT (ver connectors/dex_grvt.py, sección "BUG REAL" del
docstring) — pero ese conector ya ha demostrado una vez que la documentación
de un exchange puede no coincidir con la realidad en vivo, así que en vez de
confiar ciegamente en que ningún conector (presente o futuro) vaya a repetir
un fallo de escala parecido en el propio mark_price, price_spread() aplica
una red de seguridad: por encima de IMPLAUSIBLE_SPREAD_PCT se descarta el
resultado (spread_pct=None, se ve como "—"/"s/d" en la interfaz) en vez de
enseñar un número que ni el propio proyecto se cree. No es un intento de
"corregir" el dato — es preferir no mostrar nada antes que mostrar basura,
mismo criterio que el resto del proyecto usa para OI/volumen ausente.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.normalize import NormalizedRate

DEFAULT_WINDOW_HOURS = 24 * 30  # 30 días, igual que la ventana más larga del histórico
MIN_SAMPLES_FOR_SCORE = 20      # con menos snapshots que esto, no publicamos un score

# Ver nota "Guardia de cordura" al final del docstring del módulo. 1000% ya
# es un long_price ~11 veces más barato que el short_price del mismo activo
# — inalcanzable entre dos precios reales del mismo mercado, así que por
# encima de esto se asume bug de datos (escala/decimales, símbolo mal
# emparejado) antes que un coste de entrada real.
IMPLAUSIBLE_SPREAD_PCT = 1000.0


@dataclass
class ConsistencyResult:
    score_pct: float | None   # None si no hay histórico suficiente
    samples: int
    enough_history: bool


def consistency_score(
    conn: sqlite3.Connection,
    long_exchange: str,
    short_exchange: str,
    symbol: str,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    min_samples: int = MIN_SAMPLES_FOR_SCORE,
    now: datetime | None = None,
) -> ConsistencyResult:
    """
    % de snapshots, en la ventana dada, donde short_exchange pagó más que
    long_exchange (es decir, donde ir long en `long_exchange` y short en
    `short_exchange` habría sido la asignación correcta).
    """
    since = ((now or datetime.now(timezone.utc)) - timedelta(hours=window_hours)).isoformat()

    cur = conn.execute(
        """
        SELECT a.apr_pct, b.apr_pct
        FROM funding_snapshots a
        JOIN funding_snapshots b
          ON a.captured_at = b.captured_at AND a.symbol = b.symbol
        WHERE a.exchange = ? AND b.exchange = ? AND a.symbol = ? AND a.captured_at >= ?
        """,
        (long_exchange, short_exchange, symbol, since),
    )
    rows = cur.fetchall()

    if len(rows) < min_samples:
        return ConsistencyResult(score_pct=None, samples=len(rows), enough_history=False)

    favorable = sum(1 for long_apr, short_apr in rows if short_apr > long_apr)
    score = (favorable / len(rows)) * 100
    return ConsistencyResult(score_pct=score, samples=len(rows), enough_history=True)


@dataclass
class OIDepth:
    long_oi_usd: float | None
    short_oi_usd: float | None
    bottleneck_usd: float | None  # la pierna más fina; el tamaño real que aguanta la operación

    @property
    def bottleneck_side(self) -> str | None:
        if self.long_oi_usd is None or self.short_oi_usd is None:
            return None
        return "long" if self.long_oi_usd <= self.short_oi_usd else "short"


def oi_depth(long_rate: NormalizedRate, short_rate: NormalizedRate) -> OIDepth:
    long_oi = long_rate.open_interest_usd
    short_oi = short_rate.open_interest_usd
    bottleneck = min(long_oi, short_oi) if long_oi is not None and short_oi is not None else None
    return OIDepth(long_oi_usd=long_oi, short_oi_usd=short_oi, bottleneck_usd=bottleneck)


@dataclass
class VolumeDepth:
    long_volume_usd: float | None
    short_volume_usd: float | None
    bottleneck_usd: float | None  # la pierna con menos volumen; el lado que de verdad limita cuánto puedes mover

    @property
    def bottleneck_side(self) -> str | None:
        if self.long_volume_usd is None or self.short_volume_usd is None:
            return None
        return "long" if self.long_volume_usd <= self.short_volume_usd else "short"


def volume_depth(long_rate: NormalizedRate, short_rate: NormalizedRate) -> VolumeDepth:
    """Mismo patrón que oi_depth(), pero con el volumen negociado en 24h de cada pierna."""
    long_vol = long_rate.volume_24h_usd
    short_vol = short_rate.volume_24h_usd
    bottleneck = min(long_vol, short_vol) if long_vol is not None and short_vol is not None else None
    return VolumeDepth(long_volume_usd=long_vol, short_volume_usd=short_vol, bottleneck_usd=bottleneck)


@dataclass
class PriceSpread:
    long_price: float | None
    short_price: float | None
    spread_pct: float | None  # diferencia relativa de precio entre las dos piernas, en % (siempre >= 0)


def price_spread(long_rate: NormalizedRate, short_rate: NormalizedRate) -> PriceSpread:
    """
    Diferencia de precio (mark_price) entre las dos piernas de una oportunidad,
    en % sobre el precio de la pierna long.

    Se expresa siempre en valor absoluto a propósito: lo que importa aquí no
    es qué lado está más caro (eso ya lo dice qué exchange es long/short),
    sino CUÁNTO cuesta en precio entrar en la operación — un coste que se
    paga una sola vez, a diferencia del funding que se cobra/paga cada
    intervalo.

    None cuando a cualquiera de las dos piernas le falta el mark_price (pasa,
    p.ej., si algún conector no lo trae), cuando el precio long es 0, o
    cuando el resultado supera IMPLAUSIBLE_SPREAD_PCT (ver nota "Guardia de
    cordura" en el docstring del módulo) — no se inventa ni se enseña un
    número que no nos creemos, se deja como "—" en la interfaz, igual que
    con OI/volumen ausente.
    """
    long_price = long_rate.mark_price
    short_price = short_rate.mark_price
    if long_price is None or short_price is None or long_price == 0:
        return PriceSpread(long_price=long_price, short_price=short_price, spread_pct=None)
    spread_pct = abs(short_price - long_price) / long_price * 100
    if spread_pct > IMPLAUSIBLE_SPREAD_PCT:
        return PriceSpread(long_price=long_price, short_price=short_price, spread_pct=None)
    return PriceSpread(long_price=long_price, short_price=short_price, spread_pct=spread_pct)
