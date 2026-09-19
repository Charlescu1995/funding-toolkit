"""
Conector para Nado — DEX de perpetuos (fork/familia Vertex Protocol).

--- Bug real de investigación: el endpoint "bulk" documentado no devuelve lo
    que documenta, en vivo — no confiar en la doc a ciegas (ver README) ---

La doc de Nado describe un endpoint GET `/gateway/v1/query?type=all_products`
(o `type=edge_all_products`) que en teoría trae TODOS los mercados con
funding/precio/OI de golpe. Probado en vivo, este endpoint IGNORA el
parámetro `type` por completo y siempre devuelve el mismo payload que
`type=symbols` (solo especificaciones de contrato: tick size, min size,
trading_status... nada de precio/funding/OI en vivo), sea cual sea el
`type=` que se le pida (se probaron: all_products, edge_all_products,
market_snapshots, product_snapshots, perp_prices, funding_rate,
funding_rates, all_perp_prices, market_info, risk_params — todos "wrong
shape", ninguno tiró error, todos devolvieron silenciosamente el payload de
symbols). Además, los endpoints que SÍ traen datos en vivo por producto
(funding-rate, price, product-snapshots) están documentados como POST con
cuerpo JSON — inviable de verificar con las herramientas de investigación
disponibles en este momento (solo permiten GET).

**La solución real** no estaba en `/gateway/v1/*` sino en una superficie de
API totalmente distinta y más nueva: `/archive/v2/*` (documentada en
docs.nado.xyz/developer-resources/api/v2/). Confirmado en vivo:

    GET https://api.prod.nado.xyz/archive/v2/contracts

... devuelve TODOS los mercados (spot y perp mezclados) en un único objeto
`{ticker_id: {...}}`, cada entrada YA con mark price, funding rate y open
interest (en base Y en USD, precalculado) — sin necesidad de POST ni de
pool de hilos. Ejemplo real (BTC-PERP_USDT0):

    {
      "product_id": 2, "ticker_id": "BTC-PERP_USDT0",
      "base_currency": "BTC-PERP", "quote_currency": "USDT0",
      "product_type": "perpetual",
      "mark_price": 76797.58061948708,
      "open_interest": 249.401, "open_interest_usd": 19142885.98545,
      "funding_rate": 0.000230091043874675,
      "next_funding_rate_timestamp": 1789297200, ...
    }

A diferencia de `/gateway/v1/*` (que usa strings "_x18" con precisión fija
de 18 decimales, ej. "1000000000000000000"), aquí TODOS los campos numéricos
ya vienen como decimales normales — no hace falta dividir entre 1e18.

--- Nota sobre `trading_status` (se sigue necesitando un segundo endpoint) ---

`/archive/v2/contracts` no trae ningún campo de estado/operable — solo
precios y métricas. Para filtrar mercados no operables (el mismo problema ya
visto con Aster/RiseX: listados bulk que mezclan mercados fantasma/pausados/
no lanzados con los reales) hace falta cruzar con:

    GET https://api.prod.nado.xyz/gateway/v1/query?type=symbols

... que SÍ funciona en vivo tal cual está documentado (a diferencia de
`type=all_products`) y devuelve `data.symbols.{symbol}.trading_status`, con
valores observados en vivo: "live", "post_only", "not_tradable". Se cruza
por símbolo (`base_currency` en contracts, ej. "BTC-PERP", coincide con la
clave de `symbols`) y solo se aceptan mercados con `trading_status == "live"`.

--- Nota sobre la escala de `funding_rate` (documentada, PENDIENTE DE
    CONFIRMAR EL VALOR EXACTO EN PRODUCCIÓN REAL — ver razonamiento) ---

La doc de mecánica de funding de Nado (docs.nado.xyz/core/funding-rates)
indica liquidación HORARIA, pero calculada a partir de una tasa de 8h
(F = premium + clamp(...)), y que el `funding_rate` que expone la API es la
tasa EQUIVALENTE A 24 HORAS (3 veces la tasa de 8h) — no la tasa horaria
real que se liquida cada hora. Esto se puede contrastar por orden de
magnitud con el propio valor real observado en `/archive/v2/contracts` para
BTC: "0.000230091043874675". Si esto fuera ya la tasa horaria, el APR
resultante (funding_rate × 8760 × 100) saldría ≈2015%/año — absurdo para
BTC. Dividido entre 24 (asumiendo que es la tasa de 24h), la tasa horaria
real sale ≈0.0000095833 y el APR ≈8.4%/año — una cifra normal. Por tanto,
igual que con Variational (que "desanualiza" desde APY), este conector
"desanualiza" desde la tasa de 24h a la tasa horaria real antes de construir
el FundingRate:

    interval_hours    = 1.0  (liquidación horaria, confirmado por doc)
    rate_per_interval = funding_rate_24h / 24

**Pendiente de verificar contra tráfico real de producción** (igual que
Lighter/Paradex/Extended/Pacifica en su momento): no ha sido posible
contrastar este cálculo contra la propia interfaz de Nado en vivo. Si al
desplegar se ve un APR de Nado sistemáticamente ×24 o ÷24 respecto a lo que
enseña la web oficial de Nado, este es el sitio a revisar primero.

--- Nota sobre `open_interest_usd` ---

A diferencia de Backpack/RiseX (que solo dan el OI en activo base y hay que
multiplicar por mark price a mano), aquí `/archive/v2/contracts` YA trae
`open_interest_usd` precalculado — se usa directamente, sin conversión.

--- Nota sobre volumen 24h (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El mismo objeto de `/archive/v2/contracts` que ya se usa para todo lo demás
trae también, junto a `open_interest`/`open_interest_usd`, dos campos de
volumen de 24h: `base_volume` y `quote_volume`. Ejemplo real (BTC-PERP_USDT0):

    "base_volume": 3015.95925, "quote_volume": 230868794.13825437

`base_volume` viene en unidades del activo base (BTC) y `quote_volume` en la
moneda de cotización del contrato (USDT0 — un stablecoin 1:1 con USDT, que
este proyecto trata como USD igual que el resto de quote currencies
stablecoin). No hace falta ninguna llamada ni conversión adicional, igual que
con `open_interest_usd`:

    volume_24h_usd = quote_volume   (directo, sin conversión, ya en el
                                      mismo payload de contracts)
"""

from __future__ import annotations

import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://api.prod.nado.xyz"
CONTRACTS_URL = f"{BASE_URL}/archive/v2/contracts"
SYMBOLS_URL = f"{BASE_URL}/gateway/v1/query?type=symbols"

# Ver docstring: funding_rate de /archive/v2/contracts es la tasa
# equivalente a 24h, no la tasa horaria real que se liquida.
FUNDING_RATE_HOURS_BASIS = 24.0
INTERVAL_HOURS = 1.0

PERP_SUFFIX = "-PERP"


class NadoConnector:
    name = "nado"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        contracts_resp = self._session.get(CONTRACTS_URL, timeout=self._timeout)
        contracts_resp.raise_for_status()
        contracts_raw = contracts_resp.json()

        if not isinstance(contracts_raw, dict) or not contracts_raw:
            raise RuntimeError(
                "nado: /archive/v2/contracts no devolvió un objeto de mercados — "
                f"tipo recibido: {type(contracts_raw).__name__}"
            )

        symbols_resp = self._session.get(SYMBOLS_URL, timeout=self._timeout)
        symbols_resp.raise_for_status()
        symbols_payload = symbols_resp.json()
        symbols_map = (symbols_payload.get("data") or {}).get("symbols") or {}
        if not symbols_map:
            # Hallazgo #9 de la auditoría (2026-09-19), ver README: antes esto
            # solo avisaba con un logger.warning y dejaba pasar TODOS los
            # mercados sin filtrar por trading_status -- si este endpoint
            # cambia de forma otra vez (ya ha pasado dos veces con el otro
            # endpoint de Nado, ver docstring del módulo), el filtro de
            # "solo mercados live" se apagaba entero, en silencio,
            # reintroduciendo exactamente los mercados fantasma/pausados/no
            # lanzados que este conector se construyó para excluir. En vez de
            # adivinar que "sin datos de status = todo vale", se trata igual
            # que la comprobación ya existente de /archive/v2/contracts
            # vacío: un fallo real y ruidoso. data_service.py ya captura
            # cualquier excepción por conector y sigue con el resto de
            # exchanges (ver core/data_service.py), así que esto no tira la
            # app -- solo dice claramente "Nado no disponible este ciclo" en
            # vez de colar mercados no confirmados en el Ranking.
            raise RuntimeError(
                "nado: /gateway/v1/query?type=symbols no trajo 'data.symbols' -- sin esto no "
                "se puede confirmar el trading_status de NINGÚN mercado, así que no se puede "
                "filtrar con seguridad (cambió la forma de la respuesta, ver README/docstring "
                "de este módulo). Se descarta el ciclo entero de Nado en vez de dejar pasar "
                "mercados sin confirmar."
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}

        for ticker_id, row in contracts_raw.items():
            if not isinstance(row, dict):
                skipped[ticker_id] = "fila no es un objeto (formato inesperado)"
                continue

            if row.get("product_type") != "perpetual":
                continue

            base_currency = row.get("base_currency")
            if not base_currency:
                skipped[ticker_id] = "sin base_currency"
                continue

            # Ver docstring: cruce con /query?type=symbols para descartar
            # mercados no operables (mismo problema que Aster/RiseX). Para
            # llegar aquí ya se confirmó arriba que symbols_map no está
            # vacío (Hallazgo #9) -- un base_currency concreto ausente del
            # mapa (símbolo nuevo que /contracts trae y /symbols todavía no)
            # sigue tratándose como "no confirmado como live" y se descarta,
            # no se asume que sea operable.
            status = (symbols_map.get(base_currency) or {}).get("trading_status")
            if status != "live":
                continue

            rate_24h_raw = row.get("funding_rate")
            mark_price_raw = row.get("mark_price")
            if rate_24h_raw is None:
                skipped[ticker_id] = "sin funding_rate"
                continue

            try:
                rate_24h = float(rate_24h_raw)
            except (TypeError, ValueError) as exc:
                skipped[ticker_id] = f"funding_rate no numérico: {exc}"
                continue

            # Ver docstring: desanualizar de tasa-24h a tasa horaria real.
            rate_per_interval = rate_24h / FUNDING_RATE_HOURS_BASIS

            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            oi_usd_raw = row.get("open_interest_usd")
            open_interest_usd = None
            if oi_usd_raw is not None:
                try:
                    open_interest_usd = float(oi_usd_raw)
                except (TypeError, ValueError):
                    open_interest_usd = None

            # Ver docstring: quote_volume ya viene en USDT0 (≈USD), sin conversión.
            volume_24h_raw = row.get("quote_volume")
            volume_24h_usd = None
            if volume_24h_raw is not None:
                try:
                    volume_24h_usd = float(volume_24h_raw)
                except (TypeError, ValueError):
                    volume_24h_usd = None

            symbol = base_currency[: -len(PERP_SUFFIX)] if base_currency.endswith(PERP_SUFFIX) else base_currency

            out.append(
                FundingRate(
                    exchange="nado",
                    venue_type=VenueType.DEX,
                    symbol=symbol,
                    raw_symbol=ticker_id,
                    funding_rate=rate_per_interval,
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=open_interest_usd,
                    volume_24h_usd=volume_24h_usd,
                )
            )

        if not out:
            sample = dict(list(skipped.items())[:3])
            raise RuntimeError(
                f"nado: {len(skipped)}/{len(contracts_raw)} mercados se saltaron (o no eran "
                f"perpetuos operables) y no quedó ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "nado: %d/%d mercados se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(contracts_raw),
                skipped,
            )

        return out


def nado() -> NadoConnector:
    return NadoConnector()
