"""
Conector para Hyperliquid (DEX de perpetuos).

Hyperliquid no está cubierto por ccxt de forma completa, así que hablamos
directo con su API pública "info" (no requiere autenticación para leer datos
de mercado — solo hará falta autenticación en el Paso 8, para ejecutar).

Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint

--- Nota sobre volumen 24h (CONFIRMADO en la documentación oficial, Paso 6
    punto 2 — Volumen; ver limitación de entorno más abajo) ---

`metaAndAssetCtxs` es un endpoint POST (body `{"type": "metaAndAssetCtxs"}`,
no acepta GET) — WebFetch, la única herramienta de red que funciona en este
sandbox (ver nota de entorno en connectors/cex_kucoin.py), solo hace GET, así
que no se pudo re-probar en vivo símbolo por símbolo como con el resto de
conectores de este lote. En su lugar se confirmó contra la documentación
oficial de Hyperliquid (gitbook), que expone el esquema completo del objeto
`AssetCtx` con un ejemplo real de payload: cada `AssetCtx` trae
`dayNtlVlm` (ej. "1169046.29406"), documentado explícitamente como "24-hour
notional trading volume in USD" — ya en USD, sin conversión, igual patrón
que `funding`, `markPx` y `openInterest` que este conector ya usa del mismo
objeto. Se usa directamente:

    volume_24h_usd = float(ctx["dayNtlVlm"])
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

INFO_URL = "https://api.hyperliquid.xyz/info"

# Hyperliquid liquida funding cada hora.
INTERVAL_HOURS = 1


class HyperliquidConnector:
    name = "hyperliquid"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        # Igual que en cex_ccxt.py: dejamos que la excepción suba en vez de
        # tragarla aquí, para que core/data_service.py pueda enseñar el
        # motivo real del fallo en vez de un simple "0 pares".
        resp = self._session.post(
            INFO_URL,
            json={"type": "metaAndAssetCtxs"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        meta, asset_ctxs = resp.json()

        universe = meta.get("universe", [])
        out: list[FundingRate] = []
        # Ver Hallazgo #13 de la auditoría (2026-09-19, severidad baja):
        # mismo guard defensivo que cex_kucoin.py/cex_mexc.py -- un
        # markPx de 0 explícito no se multiplica (daría oi_usd=0.0 en
        # silencio), se descarta y se guarda una muestra de diagnóstico.
        zero_mark_price_samples: dict[str, object] = {}

        for asset, ctx in zip(universe, asset_ctxs):
            symbol = asset.get("name")
            funding = ctx.get("funding")
            if symbol is None or funding is None:
                continue

            mark_price = ctx.get("markPx")
            open_interest = ctx.get("openInterest")
            oi_usd = None
            if open_interest is not None and mark_price is not None:
                try:
                    mark_price_val = float(mark_price)
                except (TypeError, ValueError):
                    mark_price_val = None
                if mark_price_val == 0:
                    zero_mark_price_samples[symbol] = mark_price
                elif mark_price_val is not None:
                    try:
                        oi_usd = float(open_interest) * mark_price_val
                    except (TypeError, ValueError):
                        oi_usd = None

            # Ver docstring: dayNtlVlm ya viene en USD, sin conversión.
            day_ntl_vlm = ctx.get("dayNtlVlm")
            volume_24h_usd = None
            if day_ntl_vlm is not None:
                try:
                    volume_24h_usd = float(day_ntl_vlm)
                except (TypeError, ValueError):
                    volume_24h_usd = None

            out.append(
                FundingRate(
                    exchange="hyperliquid",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=symbol,
                    funding_rate=float(funding),
                    interval_hours=INTERVAL_HOURS,
                    mark_price=float(mark_price) if mark_price is not None else None,
                    next_funding_time=self._next_hour_utc(),
                    open_interest_usd=oi_usd,
                    volume_24h_usd=volume_24h_usd,
                )
            )

        if zero_mark_price_samples:
            logger.warning(
                "hyperliquid DIAGNÓSTICO markPx == 0 (Hallazgo #13 de la auditoría, 2026-09-19): "
                "%d símbolo(s) con markPx explícito de 0 -- no se calculó open_interest_usd "
                "(habría dado 0.0 en silencio): %s",
                len(zero_mark_price_samples),
                zero_mark_price_samples,
            )

        return out

    @staticmethod
    def _next_hour_utc() -> datetime:
        now = datetime.now(timezone.utc)
        return now.replace(minute=0, second=0, microsecond=0).replace(
            hour=(now.hour + 1) % 24
        )
