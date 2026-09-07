"""
Lista de todos los conectores DEX disponibles, en un solo sitio — igual que
`ALL_CEX_FACTORIES` en cex_ccxt.py, para que cli.py y core/data_service.py
no tengan que mantener la lista duplicada cada vez que se añade un exchange.

Cada uno habla directo con la API pública del DEX correspondiente (ninguno
está cubierto por ccxt), así que son conectores propios en vez de instancias
de una misma clase genérica como pasa con los CEX.
"""

from __future__ import annotations

from .dex_extended import extended
from .dex_hyperliquid import HyperliquidConnector
from .dex_lighter import lighter
from .dex_pacifica import pacifica
from .dex_paradex import paradex


def hyperliquid() -> HyperliquidConnector:
    return HyperliquidConnector()


ALL_DEX_FACTORIES = [hyperliquid, lighter, paradex, extended, pacifica]
