"""
Conector para Vertex Protocol — DEX de perpetuos en Arbitrum (y varias otras
chains: Base, Sei, Bera, Mantle, Blast). Arquitectura "gateway + archive",
la misma familia de la que Nado es un fork (ver connectors/dex_nado.py) —
pero OJO: **este conector NO se ha podido verificar contra la API en vivo**
(los hosts `*.prod.vertexprotocol.com` no son alcanzables desde el entorno
de desarrollo de este proyecto — ni siquiera se pudo resolver su robots.txt,
a diferencia del resto de exchanges de esta tanda). Está construido
enteramente a partir de la documentación oficial (docs.vertexprotocol.com),
con el mismo nivel de confianza que Lighter/Paradex/Extended/Pacifica en su
momento: **pendiente de confirmar contra tráfico real de producción**.

--- Por qué NO es un simple "copiar Nado" (aunque Nado sea su fork) ---

Antes de construir esto se probó activamente si Nado era representativo de
Vertex. No lo es del todo:

  - Nado tiene un endpoint bulk moderno `GET /archive/v2/contracts` (una
    sola llamada, decimales planos, todo precalculado). Vertex, según su
    propia documentación, NO expone ese endpoint — su superficie
    "archive"/indexer es **POST** con cuerpo JSON
    (`POST [ARCHIVE_ENDPOINT]` con `{"market_snapshots": {...}}` o
    `{"funding_rate": {...}}`), no GET.
  - Vertex expone el funding en DOS escalas distintas según qué endpoint se
    use: `market_snapshots.funding_rates` (tasa horaria cruda, lista para
    usar) vs `funding_rate.funding_rate_x18` (tasa equivalente a 24h, hay
    que dividir entre 24 — el mismo patrón que Nado, pero aquí está
    documentado explícitamente en vez de ser un hallazgo empírico). Este
    conector usa la primera (`market_snapshots`) precisamente para
    evitarse esa conversión.
  - La doc de Vertex para `type=symbols` (gateway) NO documenta ningún
    campo `trading_status` — ese campo parece ser una adición propia de
    Nado sobre la base de Vertex, no algo heredado. Ver más abajo.

--- Llamadas que hace este conector ---

1. `GET {GATEWAY_URL}?type=symbols` — mapa símbolo→metadata (incluye
   `product_id` y `type`, spot/perp mezclados). Se usa para traducir
   `product_id` → símbolo legible y para quedarnos solo con `type == "perp"`.

2. `GET {GATEWAY_URL}?type=all_products` — trae, para cada producto, su
   `oracle_price_x18` (se usa como mark price). Solo informativo: si esta
   llamada falla o cambia de forma, el conector sigue funcionando con
   `mark_price=None` (no es crítico para el cálculo de APR ni de OI, ver
   más abajo).

3. `POST {ARCHIVE_URL}` con `{"market_snapshots": {"interval": {"count": 1,
   "granularity": 3600, "max_time": <ahora>}, "product_ids": [...]}}` —
   trae, en un único snapshot con timestamp más reciente,
   `funding_rates[product_id]` y `open_interests[product_id]` para TODOS
   los productos pedidos de golpe (no hace falta pool de hilos: se piden
   todos los product_id en un solo array, no uno por llamada).

--- Nota sobre la escala de `funding_rate` (documentada, no verificada en
    vivo) ---

Según la doc de Vertex, `market_snapshots.funding_rates` ya es la tasa
horaria cruda (liquidación cada 1h), como decimal escalado ×1e18. No hace
falta ninguna conversión adicional — a diferencia de Nado (que solo expone
la tasa equivalente a 24h y hay que dividir entre 24), aquí se evita ese
problema usando esta fuente en vez de `funding_rate.funding_rate_x18`.

    interval_hours = 1.0
    rate_per_interval = funding_rates[pid] / 1e18

--- Nota sobre `open_interest` (asunción razonada, NO confirmada — la más
    incierta de este conector) ---

El único valor de ejemplo real visto en la documentación
(`open_interests: {"2": "2907581091676822842104781"}`) da, dividido entre
1e18, ≈2,907,581. Interpretarlo como unidades del activo base (como se hace
con RiseX/Backpack/Hibachi en este mismo proyecto) sería absurdo si el
product_id 2 es BTC (heredando la numeración de Nado, donde product_id=2 es
BTC-PERP): ~2.9 millones de BTC de open interest no es físicamente posible
(supera varias veces el supply total de BTC). En cambio, ~$2.9M de USD de
open interest es una cifra perfectamente plausible para un mercado de
tamaño medio en Vertex. Por eso este conector asume que `open_interests` ya
viene EN USD y lo usa directamente, SIN multiplicar por el oracle price —
al revés que la mayoría de conectores de este proyecto. **Esta es la
asunción menos fiable de las tres tandas de DEX nuevas: no hay forma de
confirmarla sin acceso a la red real de Vertex.** Si al desplegar el Open
Interest de Vertex sale sistemáticamente ~absurdamente bajo, esta es la
primera sospechosa (probar a multiplicar por `oracle_price_x18` en su lugar).

--- Nota sobre mercados no operables (SIN filtro — hueco conocido) ---

A diferencia de Aster/RiseX/Backpack/Nado/Hibachi (todos con algún campo de
estado que se usa para descartar mercados fantasma/delistados), la
documentación de Vertex para `type=symbols` NO describe ningún campo de
estado operable. Por tanto este conector NO filtra por estado — todo lo que
aparece en `type=symbols` con `type == "perp"` se acepta tal cual. Esto es
un hueco conocido y deliberado (mejor documentarlo que inventar un filtro
sin base): si al desplegar aparecen mercados de Vertex con funding pero sin
actividad real (el mismo patrón que causó el bug de Aster/STORJ), revisar
si `all_products` trae algún campo `risk`/`book_info` con el estado real
(la doc lo menciona de pasada pero no lo detalla) y añadir el filtro aquí.

--- Nota sobre el símbolo ---

El campo `symbol` de `type=symbols` se asume con el mismo formato que Nado
heredó de Vertex (ej. "BTC-PERP") — no se ha podido confirmar en vivo. Se
recorta el sufijo "-PERP" si está presente, igual que en dex_nado.py.

--- Nota sobre volumen 24h: hueco conocido ---

Sigue sin poder confirmarse contra tráfico real: al intentar esta tarea se
volvió a probar tanto `GET` directo (curl, bloqueado por el proxy de red de
este entorno con 403 en el CONNECT, confirmado vía `/__agentproxy/status`)
como WebFetch contra `gateway.prod.vertexprotocol.com` y
`archive.prod.vertexprotocol.com` — ambos fallan igual
(`robots.txt fetch failed: ConnectError`), el mismo bloqueo total descrito
arriba para el resto de este conector. Lo que SÍ fue alcanzable vía WebFetch
es la documentación pública en `docs.vertexprotocol.com` (host distinto, sin
bloquear), que describe la forma completa de la respuesta de
`market_snapshots` (endpoint que este conector ya usa para funding/OI, ver
arriba): sí trae un campo de volumen, `cumulative_volumes` (mapa
product_id → volumen en USDC), pero la propia documentación lo describe como
ACUMULADO desde el origen del producto, no una ventana de 24h — a diferencia
de `turnoverOf24h`/`turnover24h` en KuCoin/ApeX, no sirve tal cual como
"volumen negociado en 24h". Obtener un volumen de 24h real exigiría pedir
DOS snapshots (uno actual y otro de hace ~24h, cambiando `interval.count` a
2 y ajustando `granularity`) y restar `cumulative_volumes` entre ambos —
eso es cambiar la FORMA de una petición que hoy se sabe que funciona para
funding/OI, no solo leer un campo adicional, y no hay forma de probarlo sin
acceso real a la API. Por el mismo criterio de prudencia que el resto de
asunciones no confirmadas de este archivo (ver `open_interests` y el filtro
de mercados, arriba), se deja `volume_24h_usd=None` en vez de inventar esa
resta sin poder contrastarla. Si Vertex se vuelve alcanzable desde este
entorno en el futuro, el primer paso sería probar ese diff de dos snapshots
contra un producto conocido y contrastarlo con el volumen que muestra la UI
pública de Vertex.

--- Nota sobre Hallazgo #15 de la auditoría (2026-09-19) — diagnóstico
    añadido, ninguna fórmula cambiada ---

La auditoría concretó lo que ya se sabía: tanto el ÷1e18 de `funding_rate`
como la suposición "`open_interests` ya viene en USD" (ver las dos notas de
arriba) dependen enteramente de la documentación oficial, sin ningún dato
en vivo — este conector nunca se ha podido alcanzar desde ningún entorno de
desarrollo de este proyecto (ver "NO se ha podido verificar contra la API
en vivo" al principio del docstring), a diferencia de MEXC/Paradex/KuCoin,
donde al menos algo se pudo contrastar con WebFetch aunque fuera parcial.
Investigado de nuevo en esta ronda (WebSearch + `coinalyze.net`): no
apareció ningún valor numérico real nuevo, solo confirmación indirecta de
que las tasas de Vertex "se normalizan a 8h" y son fracciones pequeñas de
porcentaje — coherente con lo que ya asume el código, pero no una cifra
concreta que contraste el ÷1e18. La página de GitBook con el detalle del
campo (`vertex-protocol.gitbook.io/docs/basics/funding-rates`) no se pudo
leer, bloqueada por el sandbox.

No se cambia ninguna fórmula sin evidencia nueva (mismo criterio de
siempre). Se añade en su lugar un log de diagnóstico
(`vertex DIAGNÓSTICO escala funding_rate/open_interest`) con los primeros
valores crudos y ya convertidos de cada ciclo, para poder contrastarlos en
cuanto llegue el primer log real de producción.

Nota práctica: en los últimos despliegues aparecía "vertex SSL" como error
conocido en los logs — si esta conexión no está llegando a completarse en
producción todavía, este diagnóstico no producirá ningún dato hasta que
ese problema de conectividad se resuelva primero; la confirmación de la
escala está bloqueada detrás de eso, no solo de la falta de evidencia.
Confirmado en el primer despliegue de este fix (2026-09-19): efectivamente
sigue fallando con `ssl.SSLEOFError` contra `gateway.prod.vertexprotocol.com`,
antes incluso de llegar al código de este diagnóstico.

Nota sobre el nivel del log (2026-09-19, corregida tras ese mismo
despliegue): se loguea a nivel WARNING, no INFO -- la app nunca llama
`logging.basicConfig()`, así que el logger raíz se queda en su nivel por
defecto (WARNING) y cualquier `logger.info(...)` se descarta antes de
llegar a los logs de Streamlit Cloud (confirmado con el diagnóstico gemelo
de Paradex, que sí corrió sin errores ese despliegue y aun así no apareció
en el log, por estar a nivel INFO). Mismo nivel que el resto de
diagnósticos del proyecto.
"""

from __future__ import annotations

import logging
import time

import requests

from .base import FundingRate, VenueType

logger = logging.getLogger(__name__)

GATEWAY_URL = "https://gateway.prod.vertexprotocol.com/v1/query"
ARCHIVE_URL = "https://archive.prod.vertexprotocol.com/v1"

INTERVAL_HOURS = 1.0
SCALE_1E18 = 1e18
PERP_SUFFIX = "-PERP"


def _find_perp_products(payload: object) -> list[dict]:
    """
    Busca la lista de productos perp dentro de la respuesta de
    `type=all_products` por FORMA, no por nombre exacto de clave — la doc de
    Vertex no se pudo verificar en vivo, así que no conviene fiarse de un
    único nombre de clave (mismo enfoque defensivo que RiseX, ver
    connectors/dex_risex.py).
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []

    candidate = data.get("perp_products")
    if isinstance(candidate, list):
        return [p for p in candidate if isinstance(p, dict)]

    # Fallback: cualquier lista de dicts con product_id + oracle_price_x18.
    for value in data.values():
        if isinstance(value, list) and value and all(
            isinstance(v, dict) and "product_id" in v for v in value
        ):
            return value
    return []


class VertexConnector:
    name = "vertex"
    venue_type = VenueType.DEX

    def __init__(self, session: requests.Session | None = None, timeout: float = 10.0):
        self._session = session or requests.Session()
        self._timeout = timeout

    def fetch_funding_rates(self) -> list[FundingRate]:
        symbols_resp = self._session.get(GATEWAY_URL, params={"type": "symbols"}, timeout=self._timeout)
        symbols_resp.raise_for_status()
        symbols_payload = symbols_resp.json()
        symbols_map = (symbols_payload.get("data") or {}).get("symbols") if isinstance(symbols_payload, dict) else None

        if not symbols_map:
            raise RuntimeError(
                "vertex: /v1/query?type=symbols no devolvió 'data.symbols' — "
                f"probablemente cambió la forma de la respuesta (claves recibidas: "
                f"{list(symbols_payload.keys()) if isinstance(symbols_payload, dict) else type(symbols_payload).__name__})"
            )

        # product_id -> (symbol, base_symbol) solo para perps.
        perp_by_id: dict[int, str] = {}
        for symbol, meta in symbols_map.items():
            if not isinstance(meta, dict) or meta.get("type") != "perp":
                continue
            product_id = meta.get("product_id")
            if product_id is None:
                continue
            try:
                perp_by_id[int(product_id)] = symbol
            except (TypeError, ValueError):
                continue

        if not perp_by_id:
            raise RuntimeError(
                "vertex: 'data.symbols' no trajo ningún producto con type == 'perp' — "
                f"¿cambió el nombre/valor del campo? ({len(symbols_map)} símbolos recibidos en total)"
            )

        # Mark price: informativo, no crítico (ver docstring) — si falla, se
        # sigue adelante con mark_price=None para todos.
        mark_price_by_id: dict[int, float] = {}
        try:
            products_resp = self._session.get(GATEWAY_URL, params={"type": "all_products"}, timeout=self._timeout)
            products_resp.raise_for_status()
            perp_products = _find_perp_products(products_resp.json())
            for row in perp_products:
                pid = row.get("product_id")
                price_raw = row.get("oracle_price_x18")
                if pid is None or price_raw is None:
                    continue
                try:
                    mark_price_by_id[int(pid)] = float(price_raw) / SCALE_1E18
                except (TypeError, ValueError):
                    continue
        except Exception as exc:
            logger.warning("vertex: no se pudo obtener mark price de type=all_products (no crítico): %s", exc)

        product_ids = list(perp_by_id.keys())
        snapshot_body = {
            "market_snapshots": {
                "interval": {
                    "count": 1,
                    "granularity": 3600,
                    "max_time": int(time.time()),
                },
                "product_ids": product_ids,
            }
        }
        archive_resp = self._session.post(ARCHIVE_URL, json=snapshot_body, timeout=self._timeout)
        archive_resp.raise_for_status()
        archive_payload = archive_resp.json()

        snapshots = archive_payload.get("snapshots") if isinstance(archive_payload, dict) else None
        if not snapshots:
            raise RuntimeError(
                "vertex: POST /v1 (market_snapshots) no devolvió ningún snapshot — "
                f"claves de nivel superior recibidas: "
                f"{list(archive_payload.keys()) if isinstance(archive_payload, dict) else type(archive_payload).__name__}"
            )

        latest = snapshots[-1]
        funding_rates_raw = latest.get("funding_rates") or {}
        open_interests_raw = latest.get("open_interests") or {}

        if not funding_rates_raw:
            raise RuntimeError(
                "vertex: el snapshot de market_snapshots no trajo 'funding_rates' — "
                f"claves recibidas en el snapshot: {list(latest.keys())}"
            )

        out: list[FundingRate] = []
        skipped: dict[str, str] = {}
        # Ver docstring, "Nota sobre la escala de funding_rate" y "Nota
        # sobre open_interest" (Hallazgo #15 de la auditoría, 2026-09-19 --
        # SOSPECHOSO, sin verificar en vivo, conector nunca alcanzable
        # desde este sandbox): muestra de los primeros valores crudos y ya
        # convertidos, para poder confirmar/descartar el ÷1e18 y la
        # suposición de "open_interests ya en USD" con el próximo log real.
        scale_diagnostic_samples: dict[str, dict] = {}

        for pid, symbol in perp_by_id.items():
            rate_raw = funding_rates_raw.get(str(pid))
            if rate_raw is None:
                skipped[symbol] = f"sin funding_rate para product_id={pid} en el snapshot"
                continue

            try:
                rate = float(rate_raw) / SCALE_1E18
            except (TypeError, ValueError) as exc:
                skipped[symbol] = f"funding rate no numérico: {exc}"
                continue

            # Ver docstring: se asume que open_interests YA viene en USD.
            oi_raw = open_interests_raw.get(str(pid))
            open_interest_usd = None
            if oi_raw is not None:
                try:
                    open_interest_usd = float(oi_raw) / SCALE_1E18
                except (TypeError, ValueError):
                    open_interest_usd = None

            base_symbol = symbol[: -len(PERP_SUFFIX)] if symbol.endswith(PERP_SUFFIX) else symbol

            if len(scale_diagnostic_samples) < 15:
                scale_diagnostic_samples[symbol] = {
                    "funding_rate_raw_x18": rate_raw,
                    "funding_rate_calculado": rate,
                    "open_interest_raw_x18": oi_raw,
                    "open_interest_usd_calculado": open_interest_usd,
                    "mark_price_oracle": mark_price_by_id.get(pid),
                }

            out.append(
                FundingRate(
                    exchange="vertex",
                    venue_type=VenueType.DEX,
                    symbol=base_symbol,
                    raw_symbol=symbol,
                    funding_rate=rate,
                    interval_hours=INTERVAL_HOURS,
                    mark_price=mark_price_by_id.get(pid),
                    next_funding_time=None,
                    open_interest_usd=open_interest_usd,
                    # Ver docstring: no hay campo de volumen 24h confirmable
                    # (cumulative_volumes es acumulado, no una ventana 24h) —
                    # hueco conocido, no se inventa el valor.
                    volume_24h_usd=None,
                )
            )

        if not out:
            sample = dict(list(skipped.items())[:3])
            raise RuntimeError(
                f"vertex: {len(skipped)}/{len(perp_by_id)} productos perp se saltaron y no "
                f"quedó ningún par válido — muestra de motivos: {sample}"
            )
        elif skipped:
            logger.warning(
                "vertex: %d/%d productos perp se saltaron (se omiten, no tiran el resto): %s",
                len(skipped),
                len(perp_by_id),
                skipped,
            )

        if scale_diagnostic_samples:
            logger.warning(
                "vertex DIAGNÓSTICO escala funding_rate/open_interest (Hallazgo #15 de la "
                "auditoría, 2026-09-19 -- SOSPECHOSO, ver docstring del módulo, la asunción "
                "menos confirmada de este conector): muestra de %d producto(s) este ciclo, "
                "crudo y calculado, para contrastar contra valores esperados (funding típico "
                "fracciones de 0.01%%-0.05%% por hora; OI de un mercado mediano del orden de "
                "$1M-$10M, no billones ni céntimos -- si sale muy desproporcionado, revisar "
                "primero si open_interests necesita multiplicarse por oracle_price_x18 en vez "
                "de usarse directo, ver docstring): %s",
                len(scale_diagnostic_samples),
                scale_diagnostic_samples,
            )

        return out


def vertex() -> VertexConnector:
    return VertexConnector()
