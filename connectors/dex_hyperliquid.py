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

--- Nota sobre Hallazgo #16 de la auditoría (2026-09-19, RESUELTO) ---

Dos problemas reales encontrados al estudiarlo:

  - `float(funding)` no tenía try/except -- un solo activo con un valor no
    numérico en `funding` tiraba el conector ENTERO (sin excepción alguna
    capturada en ningún sitio de esta función). Ahora se descarta solo ese
    símbolo y se registra en diagnóstico.
  - `mark_price` se convertía DOS VECES por separado: una vez protegida
    (`mark_price_val`, solo para el cálculo de OI) y otra sin proteger,
    directamente en el campo `FundingRate.mark_price` de más abajo -- y de
    paso, esa segunda conversión ignoraba el guard de `mark_price == 0` del
    Hallazgo #13, así que un markPx explícito de 0 sí se publicaba como
    precio (aunque no se usara para calcular OI). Ahora hay una sola
    conversión protegida y su resultado (ya sin el caso `== 0`) se usa para
    ambas cosas.
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
        # Ver Hallazgo #16 de la auditoría (2026-09-19): `funding` no tenía
        # try/except (un solo activo con valor no numérico tiraba el
        # conector ENTERO), y `mark_price` se recalculaba una SEGUNDA vez
        # sin protección (línea que rellena FundingRate.mark_price) aparte
        # del `mark_price_val` de arriba, que solo protegía el cálculo de
        # OI -- las dos conversiones duplicadas ahora comparten el mismo
        # valor ya protegido, en vez de reconvertir por separado.
        non_numeric_samples: dict[str, dict] = {}

        for asset, ctx in zip(universe, asset_ctxs):
            symbol = asset.get("name")
            funding = ctx.get("funding")
            if symbol is None or funding is None:
                continue

            try:
                funding_rate_value = float(funding)
            except (TypeError, ValueError):
                if len(non_numeric_samples) < 6:
                    non_numeric_samples[symbol] = {"funding_raw": funding}
                continue

            mark_price = ctx.get("markPx")
            mark_price_val = None
            if mark_price is not None:
                try:
                    mark_price_val = float(mark_price)
                except (TypeError, ValueError):
                    mark_price_val = None
                    if len(non_numeric_samples) < 6:
                        non_numeric_samples.setdefault(symbol, {})["markPx_raw"] = mark_price

            open_interest = ctx.get("openInterest")
            oi_usd = None
            if open_interest is not None and mark_price_val is not None:
                if mark_price_val == 0:
                    zero_mark_price_samples[symbol] = mark_price
                else:
                    try:
                        oi_usd = float(open_interest) * mark_price_val
                    except (TypeError, ValueError):
                        oi_usd = None

            # mark_price_val ya es 0 en vez de None cuando markPx llegó como
            # 0 explícito -- se descarta igual que en el resto del proyecto
            # (Hallazgo #13), no se publica un precio de 0.
            mark_price_final = None if mark_price_val == 0 else mark_price_val

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
                    funding_rate=funding_rate_value,
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price_final,
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

        if non_numeric_samples:
            logger.warning(
                "hyperliquid DIAGNÓSTICO funding/markPx no numérico (Hallazgo #16 de la "
                "auditoría, 2026-09-19): %d símbolo(s) afectado(s) -- un funding no numérico "
                "descarta el símbolo entero; un markPx no numérico solo descarta el precio "
                "(el símbolo se sigue publicando sin mark_price/OI). Antes de este fix, un "
                "funding no numérico habría tirado el conector ENTERO: %s",
                len(non_numeric_samples),
                non_numeric_samples,
            )

        return out

    @staticmethod
    def _next_hour_utc() -> datetime:
        now = datetime.now(timezone.utc)
        return now.replace(minute=0, second=0, microsecond=0).replace(
            hour=(now.hour + 1) % 24
        )
