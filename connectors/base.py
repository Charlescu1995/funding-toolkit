"""
Modelo de datos común a todos los conectores.

Cada exchange (CEX o DEX) devuelve sus datos en un formato distinto — la idea
de esta capa es que, salga de donde salga el dato, todo termine con la misma
forma antes de llegar al resto del sistema (normalización, ranking, alertas...).

Esto es deliberadamente parecido a lo que hace Loris por dentro: sea cual sea
el exchange, todos los funding rates acaban comparables entre sí.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Protocol


class VenueType(str, Enum):
    CEX = "CEX"
    DEX = "DEX"


@dataclass
class FundingRate:
    """Un funding rate crudo, tal cual lo reporta un exchange, sin normalizar."""

    exchange: str                  # "binance", "bybit", "hyperliquid"...
    venue_type: VenueType
    symbol: str                    # símbolo normalizado, ej. "BTC", "ETH", "XAU"
    raw_symbol: str                # símbolo tal cual lo usa el exchange, ej. "BTCUSDT"
    funding_rate: float            # tasa para SU intervalo nativo (no anualizada), ej. 0.0001 = 0.01%
    interval_hours: float          # cada cuánto liquida este exchange/par: 1, 4 u 8
    mark_price: Optional[float] = None
    next_funding_time: Optional[datetime] = None
    open_interest_usd: Optional[float] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        # Normalizamos el símbolo a mayúsculas y sin sufijos raros desde el origen,
        # así el resto del pipeline nunca tiene que volver a pensar en esto.
        self.symbol = self.symbol.upper().strip()


class FundingConnector(Protocol):
    """
    Contrato que debe cumplir cualquier conector, sea CEX vía ccxt o DEX vía API
    directa. Mientras cumplan esto, el resto del sistema no necesita saber nada
    específico de cada exchange.
    """

    name: str
    venue_type: VenueType

    def fetch_funding_rates(self) -> list[FundingRate]:
        """Devuelve el snapshot actual de funding rates para todos los pares soportados."""
        ...
