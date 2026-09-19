"""
Conector para MEXC Futures — CEX de derivados.

No soportado por ccxt: `ccxt.mexc.fetch_funding_rates()` lanza `NotSupported`
(comprobado leyendo el código fuente de ccxt) — el spot de MEXC sí funciona
en ccxt, pero el módulo de futuros/swap no expone ese método.

MEXC reparte la información en TRES endpoints públicos, todos bulk (sin pool
de hilos):

    GET https://contract.mexc.com/api/v1/contract/detail
        -> metadata de todos los contratos: contractSize, operable (apiAllowed)
    GET https://contract.mexc.com/api/v1/contract/funding_rate
        -> funding rate, intervalo (collectCycle) y precios, de todos los contratos
    GET https://contract.mexc.com/api/v1/contract/ticker
        -> holdVol (open interest en Nº de contratos) y amount24 (volumen de
           24h ya en USD), de todos los contratos

Todo comprobado en vivo (WebFetch directo — ver nota de entorno en
connectors/cex_kucoin.py: estos hosts están bloqueados por la política de
red de ESTE sandbox de desarrollo, así que la investigación se hizo con
WebFetch en vez de curl directo, pero contra la API real, no solo contra
documentación).

--- Nota sobre el universo de símbolos: MEXC mezcla cripto con activos RWA,
    y aquí se dejan ambos a propósito (a diferencia del bug real de ApeX) ---

`/api/v1/contract/detail` trae, en la misma lista, perpetuos cripto normales
(BTC_USDT, ETH_USDT...) y perpetuos sobre acciones/materias primas
tokenizadas (ej. "XAU_USDT", "GOOGLSTOCK_USDT", "NVIDIA_USDT", "USOIL_USDT").
A diferencia del bug real de ApeX (que mezclaba cripto con mercados de
predicción que SÍ rompían con 403 — ver connectors/dex_apex.py), aquí los RWA
de MEXC responden con datos de funding reales y válidos, con el mismo
esquema que el resto — no hay nada que arreglar ni filtrar. Como este
proyecto cubre "cripto + RWA" por diseño (ver README), se dejan sin filtrar:
son oportunidades legítimas más, no ruido.

--- Nota sobre `apiAllowed` como filtro de operable (en vez de `state`) ---

Cada contrato en /contract/detail trae `state` (0 en el único contrato
consultado en vivo, BTC_USDT) y `apiAllowed` (true). Se usa `apiAllowed`
como filtro porque su nombre es autoexplicativo — el mapeo completo de
valores de `state` no está documentado ni se ha podido confirmar con más de
un ejemplo en vivo, mismo criterio de precaución que con `status`/
`orderBookState` en el resto de conectores del proyecto.

--- Nota sobre `collectCycle` (CONFIRMADO en vivo — SÍ varía por símbolo,
    a diferencia del resto de CEX del proyecto) ---

El resto de CEX vía ccxt (ver DEFAULT_INTERVAL_HOURS en cex_ccxt.py) usa 8h
fijo como aproximación porque ccxt no siempre expone el intervalo real. Aquí
MEXC SÍ lo expone por contrato (`collectCycle`, en horas) y de verdad varía:
BTC_USDT trae 8, XAU_USDT trae 4 (confirmado en vivo) — se usa este campo
directamente en vez de asumir un valor fijo para todos.

--- Nota sobre `fundingRate` (CONFIRMADO en vivo, tasa cruda del intervalo) ---

BTC_USDT en vivo: fundingRate=0.000024 con collectCycle=8 -> APR ≈ 2.63%,
cifra normal — confirma que es la tasa cruda del intervalo, no anualizada.

--- Nota sobre `holdVol` (CONFIRMADO en vivo, contrastado con contractSize
    y fairPrice) ---

`holdVol` (de /contract/ticker) viene en Nº DE CONTRATOS, no en el activo
base ni en USD. Cada contrato representa `contractSize` unidades del activo
base (0.0001 BTC/contrato para BTC_USDT, confirmado en /contract/detail):

    open_interest_usd = holdVol * contractSize * fairPrice

--- Nota sobre `amount24` (CONFIRMADO en vivo, Paso 6 punto 2 — Volumen) ---

El mismo /contract/ticker trae, junto a holdVol, dos campos de volumen:
`volume24` (545,348,505 en el ejemplo real de BTC_USDT) y `amount24`
(4,265,182,792.82). `volume24` está en Nº DE CONTRATOS, igual que holdVol
(comprobado por consistencia interna: volume24 × contractSize × fairPrice ≈
amount24 — ambos lados dan ≈$4.25-4.27B, la pequeña diferencia es normal
porque el precio se mueve entre el cálculo de cada campo en el propio
exchange). `amount24` ya es el turnover de 24h en USD directamente, así que
se usa tal cual, sin repetir la conversión que sí hace falta para holdVol.
"""

from __future__ import annotations

import json
import logging

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

BASE_URL = "https://contract.mexc.com"
DETAIL_URL = f"{BASE_URL}/api/v1/contract/detail"
FUNDING_RATE_URL = f"{BASE_URL}/api/v1/contract/funding_rate"
TICKER_URL = f"{BASE_URL}/api/v1/contract/ticker"


class MexcConnector:
    name = "mexc"
    venue_type = VenueType.CEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        detail_resp = self._session.get(DETAIL_URL, timeout=self._timeout)
        detail_resp.raise_for_status()
        detail_payload = detail_resp.json()
        detail_rows = detail_payload.get("data")
        if not isinstance(detail_rows, list) or not detail_rows:
            raise RuntimeError(
                "mexc: /api/v1/contract/detail no devolvió una lista en 'data' — "
                f"claves de nivel superior: "
                f"{list(detail_payload.keys()) if isinstance(detail_payload, dict) else type(detail_payload).__name__}"
            )

        # symbol -> (contractSize, operable)
        detail_by_symbol: dict[str, tuple[float | None, bool]] = {}
        for row in detail_rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            if symbol is None:
                continue
            contract_size_raw = row.get("contractSize")
            try:
                contract_size = float(contract_size_raw) if contract_size_raw is not None else None
            except (TypeError, ValueError):
                contract_size = None
            # Ver docstring: se usa apiAllowed, no state, como filtro de operable.
            operable = bool(row.get("apiAllowed"))
            detail_by_symbol[symbol] = (contract_size, operable)

        funding_resp = self._session.get(FUNDING_RATE_URL, timeout=self._timeout)
        funding_resp.raise_for_status()
        funding_payload = funding_resp.json()
        funding_rows = funding_payload.get("data")
        if not isinstance(funding_rows, list) or not funding_rows:
            raise RuntimeError(
                "mexc: /api/v1/contract/funding_rate no devolvió una lista en 'data' — "
                f"claves de nivel superior: "
                f"{list(funding_payload.keys()) if isinstance(funding_payload, dict) else type(funding_payload).__name__}"
            )

        ticker_resp = self._session.get(TICKER_URL, timeout=self._timeout)
        ticker_resp.raise_for_status()
        ticker_payload = ticker_resp.json()
        ticker_rows = ticker_payload.get("data") or []
        if not ticker_rows:
            # Hallazgo #10 de la auditoría (2026-09-19), ver README: a
            # diferencia de detail/funding_rate (que SÍ lanzan RuntimeError
            # si vienen vacíos), este endpoint antes se quedaba en silencio
            # con `or []` -- si /api/v1/contract/ticker responde vacío o
            # cambia de forma, holdVol_by_symbol/volume_24h_by_symbol quedan
            # vacíos y TODO el exchange pierde OI/volumen sin ningún aviso,
            # indistinguible de "MEXC simplemente no reporta esto". No se
            # convierte en RuntimeError (el funding_rate en sí sigue siendo
            # válido y útil sin estos campos, a diferencia del filtro de
            # trading_status de Nado, donde dejar pasar sin filtrar sí
            # colaba datos falsos) -- se deja constancia explícita para que
            # deje de ser un fallo silencioso.
            logger.warning(
                "mexc: /api/v1/contract/ticker no trajo ninguna fila en 'data' -- "
                "open_interest_usd y volume_24h_usd quedarán en None para TODO el "
                "exchange este ciclo (claves de nivel superior recibidas: %s)",
                list(ticker_payload.keys()) if isinstance(ticker_payload, dict) else type(ticker_payload).__name__,
            )
        hold_vol_by_symbol = {
            row["symbol"]: row.get("holdVol")
            for row in ticker_rows
            if isinstance(row, dict) and row.get("symbol")
        }
        # Ver docstring: amount24 ya viene en USD, no hace falta convertir.
        volume_24h_by_symbol = {
            row["symbol"]: row.get("amount24")
            for row in ticker_rows
            if isinstance(row, dict) and row.get("symbol")
        }

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}
        # Ver auditoría de bugs (Hallazgo #3, 2026-09-19): mismo guard
        # defensivo que ya tiene connectors/cex_kucoin.py — un OI negativo
        # es físicamente imposible, así que no se propaga tal cual, se
        # descarta a None y se deja el payload crudo para diagnosticar.
        # Aquí no hay (todavía) ninguna causa raíz confirmada como la de
        # KuCoin (contratos inversos) — es puramente defensivo.
        oi_anomaly_samples: dict[str, dict] = {}

        for row in funding_rows:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            if symbol is None:
                continue

            contract_size, operable = detail_by_symbol.get(symbol, (None, False))
            if not operable:
                continue

            rate_raw = row.get("fundingRate")
            interval_raw = row.get("collectCycle")
            if rate_raw is None or interval_raw is None:
                skipped[symbol] = "faltan campos (fundingRate/collectCycle)"
                continue

            try:
                rate = float(rate_raw)
                interval_hours = float(interval_raw)
            except (TypeError, ValueError) as exc:
                skipped[symbol] = f"valor no numérico: {exc}"
                continue

            if interval_hours <= 0:
                skipped[symbol] = f"collectCycle inválido ({interval_raw})"
                continue

            mark_price_raw = row.get("fairPrice")
            mark_price = None
            if mark_price_raw is not None:
                try:
                    mark_price = float(mark_price_raw)
                except (TypeError, ValueError):
                    mark_price = None

            hold_vol_raw = hold_vol_by_symbol.get(symbol)
            open_interest_usd = None
            if hold_vol_raw is not None and contract_size is not None and mark_price is not None:
                try:
                    # Ver docstring: holdVol viene en Nº de contratos.
                    open_interest_usd = float(hold_vol_raw) * contract_size * mark_price
                except (TypeError, ValueError):
                    open_interest_usd = None

                # Ver Hallazgo #3 de la auditoría: guard defensivo, mismo
                # patrón que cex_kucoin.py — un OI negativo no se propaga.
                if open_interest_usd is not None and open_interest_usd < 0:
                    oi_anomaly_samples[symbol] = {
                        "holdVol_raw": hold_vol_raw,
                        "contractSize": contract_size,
                        "fairPrice_raw": mark_price_raw,
                        "mark_price_calculado": mark_price,
                        "open_interest_usd_calculado_DESCARTADO": open_interest_usd,
                    }
                    open_interest_usd = None

            volume_24h_raw = volume_24h_by_symbol.get(symbol)
            volume_24h_usd = None
            if volume_24h_raw is not None:
                try:
                    volume_24h_usd = float(volume_24h_raw)
                except (TypeError, ValueError):
                    volume_24h_usd = None

            base_symbol = symbol.split("_")[0] if "_" in symbol else symbol

            out.append(
                FundingRate(
                    exchange="mexc",
                    venue_type=VenueType.CEX,
                    symbol=base_symbol,
                    raw_symbol=symbol,
                    funding_rate=rate,
                    interval_hours=interval_hours,
                    mark_price=mark_price,
                    next_funding_time=None,
                    open_interest_usd=open_interest_usd,
                    volume_24h_usd=volume_24h_usd,
                )
            )

        if not out:
            sample = dict(list(skipped.items())[:3])
            raise RuntimeError(
                f"mexc: {len(skipped)} símbolos con datos incompletos de {len(funding_rows)} "
                f"totales, y ningún par operable/válido quedó — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "mexc: %d/%d símbolos se saltaron por datos incompletos (se omiten, no tiran el resto): %s",
                len(skipped),
                len(funding_rows),
                skipped,
            )

        if oi_anomaly_samples:
            logger.warning(
                "mexc DIAGNÓSTICO OI negativo (%d contrato(s) — guard defensivo del Hallazgo #3 "
                "de la auditoría, sin causa raíz confirmada todavía a diferencia de KuCoin/SOL; "
                "se descartó a None en vez de propagarse; payload crudo de los tres factores "
                "para investigarla): %s",
                len(oi_anomaly_samples),
                json.dumps(oi_anomaly_samples, default=str)[:4000],
            )

        return out


def mexc() -> MexcConnector:
    return MexcConnector()
