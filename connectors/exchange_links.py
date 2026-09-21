"""
Enlaces directos a cada exchange, por símbolo — "Links directos a cada
exchange" (punto 5 de la lista que Carlos preparó para preguntarle a
Charles, comparándonos con Smartbitrage; identificado también en
`comparativa-competidores.md` como quick-win de UX que no necesita
backend propio, 2026-09-20/21).

IMPORTANTE (mismo criterio de "nunca inventar, siempre confirmar" de todo
este proyecto): cada patrón de URL se investigó EN VIVO (WebSearch +
WebFetch contra una página real, ej. la página real de BTC/USDT de cada
exchange), 2026-09-21 — nunca adivinado por analogía con otro exchange
parecido, ni asumido de memoria. El campo `note` de cada entrada cita la
evidencia exacta usada para confirmarla.

Para 6 de los 25 exchanges (htx, extended, risex, nado, hibachi, vertex) NO
se pudo confirmar en vivo un patrón de URL que preseleccione el símbolo —
por SPA fuertemente basada en JS sin ruta indexable, por acceso restringido
(whitelist/invita), o porque simplemente no existe tal patrón documentado.
Para esos, `template` es `None` y `build_link()` devuelve la página general
de trading del exchange (sin símbolo preseleccionado) en su lugar —
NUNCA se inventa un patrón sin evidencia real. `confirmed=False` marca
estos casos para que la interfaz pueda avisar de la diferencia.

Diseño pensado para cambiar de opinión sin tocar el resto del código: si en
el futuro se quiere sustituir cualquiera de estas URLs por una versión con
código de referido/afiliado, es cambiar el `template`/`fallback_url` de esa
entrada aquí — nada más en el proyecto depende del formato exacto de la
URL, solo de `build_link()`.

--- Auditoría en vivo del punto 5, pedida por Carlos (2026-09-21) ---

Carlos reportó en producción que varios enlaces "no salían" (nada), otros
redirigían al mercado por defecto del exchange (BTC), y otros daban un
"bloqueo regional" al abrirlos desde su navegador. Se auditaron en vivo, con
el navegador real del usuario (no WebFetch, que sale desde otra IP/región),
los ~20 dominios de exchange que aparecían en un export real de la tabla
Ranking (16-25 símbolos por exchange, incluyendo casos raros a propósito:
tokens de una letra, acciones tokenizadas, forex). Resultado:

  - Todas las plantillas `confirmed=True` cargaron el mercado correcto con
    el símbolo pedido (Hyperliquid, Lighter -- incl. AAPL --, Pacifica --
    incl. EURUSD --, Backpack, BingX, GRVT, Variational -- incl. un token
    literalmente llamado "4" --, Phemex, edgeX -- incl. AAOI --, Bitget --
    incl. "2Z" --, Gate -- incl. "4STOCK" --, KuCoin, MEXC -- incl. "AAPU"
    --, Binance, Bybit, ApeX -- incl. AR, ver nota debajo). Ninguna mostró
    "va a BTC porque no encuentra el par": las plantillas en sí están bien.
  - ApeX (AR-USDT) pareció mostrar todo a cero en la primera lectura -- fue
    una falsa alarma por leer la página antes de que terminara de cargar
    (SPA pesada); una segunda lectura, un poco después, mostró el precio y
    el símbolo correctos. Aviso para quien reproduzca esta auditoría: dar
    tiempo a la SPA antes de concluir que un mercado está roto.
  - Los 6 exchanges sin plantilla (`confirmed=False`) cargaron su página
    general sin errores -- funcionan tal como están diseñados (con el aviso
    de la interfaz), aunque a un usuario le puede seguir pareciendo que "no
    sale nada" porque no hay símbolo preseleccionado.
  - DOS exchanges con plantilla confirmada (`confirmed=True`, el formato de
    URL es correcto) están BLOQUEADOS por el propio exchange para la región
    del usuario (España/UE), confirmado en vivo con dos símbolos distintos
    cada uno -- ver `region_note` en sus entradas: OKX (mensaje explícito
    "no disponible en tu país o región") y Aster (403 de CloudFront en TODO
    el dominio, incluida la home, contrastado contra WebFetch que sí carga
    la misma URL desde otra región). Esto es justo el "bloqueo regional"
    que describió Carlos -- no es un bug de la plantilla, es el propio
    exchange rechazando el producto para esa región.
  - Vertex tenía un fallback_url muerto (app.vertexprotocol.com/trade daba
    404 de Vercel) -- corregido, ver su nota.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExchangeLink:
    # Plantilla con {base} (ticker base tal cual, ej. "BTC") y/o
    # {base_lower} (en minúsculas, solo lo usa OKX) -- None si no hay
    # patrón por símbolo confirmado en vivo.
    template: str | None
    # Página general de trading del exchange, sin símbolo preseleccionado
    # -- se usa cuando template es None, o como referencia en el propio
    # código.
    fallback_url: str
    # False = no hay enlace directo AL SÍMBOLO confirmado; build_link()
    # siempre devuelve fallback_url para esta entrada.
    confirmed: bool
    note: str = ""
    # Distinto de `confirmed=False`: aquí el FORMATO de la URL es correcto
    # (confirmed=True), pero el propio exchange bloquea el producto entero
    # para ciertas regiones -- se comprobó EN VIVO desde España (navegador
    # real del usuario, no WebFetch) y el bloqueo salta para cualquier
    # símbolo, no es un problema del enlace. None si no hay bloqueo
    # regional conocido.
    region_note: str | None = None


EXCHANGE_LINKS: dict[str, ExchangeLink] = {
    # ---------------- CEX ----------------
    "binanceusdm": ExchangeLink(
        "https://www.binance.com/en/futures/{base}USDT",
        "https://www.binance.com/en/futures/markets",
        confirmed=True,
        note="Confirmado en vivo (WebFetch) contra binance.com/en/futures/BTCUSDT -- canonical y "
        "título de página coinciden, 2026-09-21.",
    ),
    "bybit": ExchangeLink(
        "https://www.bybit.com/trade/usdt/{base}USDT",
        "https://www.bybit.com/trade/usdt",
        confirmed=True,
        note="Confirmado en vivo contra bybit.com/trade/usdt/BTCUSDT, 2026-09-21.",
    ),
    "okx": ExchangeLink(
        "https://www.okx.com/trade-swap/{base_lower}-usdt-swap",
        "https://www.okx.com/trade-swap",
        confirmed=True,
        note="Confirmado en vivo contra okx.com/trade-swap/btc-usdt-swap -- minúsculas, con sufijo "
        "-swap (ojo, distinto del resto), 2026-09-21.",
        region_note="Bloqueado en vivo (navegador real, España, 2026-09-21) para CUALQUIER símbolo "
        "(probado con btc-usdt-swap y 2z-usdt-swap) -- OKX muestra: «Este producto no está "
        "disponible actualmente en tu país o región debido a las leyes y normativas locales.» "
        "Es un bloqueo de OKX a nivel de producto (derivados/perpetuos para retail en la UE, "
        "coherente con MiCA/ESMA), no un fallo de la URL -- el enlace está bien formado, pero no "
        "abrirá el mercado desde una IP española/UE.",
    ),
    "bitget": ExchangeLink(
        "https://www.bitget.com/futures/usdt/{base}USDT",
        "https://www.bitget.com/futures/usdt",
        confirmed=True,
        note="Confirmado en vivo contra bitget.com/futures/usdt/BTCUSDT (sin redirección, título "
        "coincide), 2026-09-21.",
    ),
    "kucoinfutures": ExchangeLink(
        "https://www.kucoin.com/trade/futures/{base}USDTM",
        "https://www.kucoin.com/futures/trade",
        confirmed=True,
        note='Confirmado en vivo contra kucoin.com/trade/futures/BTCUSDTM -- lleva una "M" final '
        "tras USDT (BTCUSDTM, no BTCUSDT) y vive bajo el dominio principal, no bajo "
        "futures.kucoin.com como se podría asumir, 2026-09-21.",
    ),
    "gate": ExchangeLink(
        "https://www.gate.com/futures/USDT/{base}_USDT",
        "https://www.gate.com/futures",
        confirmed=True,
        note="Confirmado en vivo contra gate.com/futures/USDT/BTC_USDT -- gate.io migró su marca a "
        "gate.com, 2026-09-21.",
    ),
    "mexc": ExchangeLink(
        "https://www.mexc.com/futures/{base}_USDT",
        "https://www.mexc.com/futures",
        confirmed=True,
        note="Confirmado en vivo contra mexc.com/futures/BTC_USDT, 2026-09-21.",
    ),
    "htx": ExchangeLink(
        None,
        "https://www.htx.com/futures/linear_swap/exchange/",
        confirmed=False,
        note="HTX es una SPA pesada en JS -- se confirmó la página por defecto (BTC/USDT) pero NO "
        "se pudo verificar en vivo que el parámetro ?contract_code={base}-USDT realmente "
        "preseleccione el símbolo (el fetch con ese parámetro devolvió contenido vacío). Se deja "
        "sin plantilla hasta confirmarlo a mano en un navegador real, 2026-09-21.",
    ),
    "bingx": ExchangeLink(
        "https://bingx.com/en/perpetual/{base}-USDT",
        "https://bingx.com/en/perpetual",
        confirmed=True,
        note="Confirmado en vivo contra bingx.com/en/perpetual/BTC-USDT (canonical coincide), "
        "2026-09-21.",
    ),
    "phemex": ExchangeLink(
        "https://phemex.com/futures/{base}-USDT",
        "https://phemex.com/futures",
        confirmed=True,
        note="Confirmado en vivo contra phemex.com/futures/BTC-USDT, 2026-09-21.",
    ),
    # ---------------- DEX ----------------
    "hyperliquid": ExchangeLink(
        "https://app.hyperliquid.xyz/trade/{base}",
        "https://app.hyperliquid.xyz/trade",
        confirmed=True,
        note="Confirmado en vivo contra app.hyperliquid.xyz/trade/BTC -- sin sufijo de quote, solo "
        "el ticker base, 2026-09-21.",
    ),
    "lighter": ExchangeLink(
        "https://app.lighter.xyz/trade/{base}",
        "https://app.lighter.xyz/trade",
        confirmed=True,
        note="Confirmado en vivo contra app.lighter.xyz/trade/BTC y /trade/ETH, 2026-09-21.",
    ),
    "paradex": ExchangeLink(
        "https://app.paradex.trade/trade/{base}-USD-PERP",
        "https://app.paradex.trade/trade",
        confirmed=True,
        note="Confirmado en vivo contra app.paradex.trade/trade/BTC-USD-PERP, 2026-09-21.",
    ),
    "extended": ExchangeLink(
        None,
        "https://app.extended.exchange/trade",
        confirmed=False,
        note="App SPA -- se confirmó que existe (app.extended.exchange/trade) y que su API usa el "
        "formato {base}-USD, pero no hay evidencia en vivo de que /trade/{base}-USD preseleccione "
        "ese mercado en la interfaz. Sin plantilla hasta confirmarlo a mano, 2026-09-21.",
    ),
    "pacifica": ExchangeLink(
        "https://app.pacifica.fi/trade/{base}",
        "https://app.pacifica.fi/trade",
        confirmed=True,
        note="Confirmado en vivo contra app.pacifica.fi/trade/ETH y /trade/BTC, 2026-09-21.",
    ),
    "aster": ExchangeLink(
        "https://www.asterdex.com/en/futures/v1/{base}USDT",
        "https://www.asterdex.com/en/futures/v1",
        confirmed=True,
        note="Confirmado en vivo contra asterdex.com/en/futures/v1/BTCUSDT (dominio real: "
        "asterdex.com, no aster.exchange), 2026-09-21.",
        region_note="Bloqueado en vivo (navegador real, España, 2026-09-21): TODO asterdex.com "
        "devuelve un 403 de CloudFront (\"ERROR: The request could not be satisfied\"), incluso la "
        "home sin símbolo -- probado con /en/futures/v1/BTCUSDT y con la home. Contraste: la misma "
        "URL SÍ carga bien vía WebFetch (infraestructura de Anthropic, otra IP/región), lo que "
        "confirma que es un bloqueo geográfico del propio Aster, no una caída del sitio ni un fallo "
        "de la plantilla.",
    ),
    "edgex": ExchangeLink(
        "https://pro.edgex.exchange/en-US/trade/{base}USD",
        "https://pro.edgex.exchange/en-US/trade",
        confirmed=True,
        note="Confirmado en vivo contra pro.edgex.exchange/en-US/trade/BTCUSD -- sufijo USD, no "
        "USDT (aunque el settlement real sea USDC), 2026-09-21.",
    ),
    "grvt": ExchangeLink(
        "https://grvt.io/exchange/perpetual/{base}-USDT",
        "https://grvt.io/exchange/perpetual",
        confirmed=True,
        note="Confirmado en vivo contra grvt.io/exchange/perpetual/ETH-USDT, 2026-09-21.",
    ),
    "variational": ExchangeLink(
        "https://omni.variational.io/perpetual/{base}",
        "https://omni.variational.io",
        confirmed=True,
        note="Confirmado vía la propia página oficial de Variational, que lista ejemplos reales de "
        "esta URL (BTC, ETH, NVDA) -- no se pudo cargar la app en vivo (bloqueada por robots.txt) "
        "pero la evidencia viene del propio exchange, no de una analogía con otro, 2026-09-21.",
    ),
    "risex": ExchangeLink(
        None,
        "https://www.rise.trade",
        confirmed=False,
        note="El patrón /en/trade/{base}-PERP SÍ está confirmado en TESTNET (testnet.rise.trade), "
        "pero el mainnet (www.rise.trade, mismo dominio que usa nuestro conector real para la API "
        "de datos) sigue detrás de una whitelist -- no se pudo confirmar que la misma ruta funcione "
        "ahí. Sin plantilla hasta confirmarlo tras el lanzamiento público, 2026-09-21.",
    ),
    "backpack": ExchangeLink(
        "https://backpack.exchange/trade/{base}_USD_PERP",
        "https://backpack.exchange/trade",
        confirmed=True,
        note="Confirmado en vivo contra backpack.exchange/trade/BTC_USD_PERP -- ojo, la URL web usa "
        "_USD_PERP aunque la API de Backpack use _USDC_PERP internamente (son distintos), "
        "2026-09-21.",
    ),
    "nado": ExchangeLink(
        None,
        "https://app.nado.xyz",
        confirmed=False,
        note="Solo un ejemplo en vivo confirmado (?market=TAOUSDT0, con un '0' final sin explicar) "
        "-- sin un segundo caso para confirmar que el patrón generaliza, y Nado parece estar en "
        "alpha cerrada con invitación según varias fuentes. Sin plantilla hasta confirmarlo, "
        "2026-09-21.",
    ),
    "hibachi": ExchangeLink(
        None,
        "https://hibachi.xyz/trade",
        confirmed=False,
        note="No se encontró ningún patrón de URL por símbolo, ni en la app ni en su documentación "
        "oficial, 2026-09-21.",
    ),
    "vertex": ExchangeLink(
        None,
        "https://www.google.com/search?q=Vertex+Protocol+perpetuals+exchange+official+site",
        confirmed=False,
        note="Actualización 2026-09-21 (auditoría de enlaces rotos pedida por Carlos): el "
        "fallback_url anterior (app.vertexprotocol.com/trade) YA NO EXISTE -- comprobado en vivo "
        "con navegador real, devuelve 404 DEPLOYMENT_NOT_FOUND de Vercel (\"This page doesn't "
        "exist... It may have been moved, removed, or never existed\"); vertexprotocol.io (dominio "
        "oficial listado en CoinMarketCap) tampoco cargó. Se probaron ambos dominios conocidos y "
        "los dos están caídos. Una búsqueda web del nombre del proyecto devuelve, en su mayoría, "
        "dominios clon con pinta de phishing (*.pages.dev, *.typedream.app con nombres como "
        "\"vertex-exxchanges-us\") -- por eso NO se sustituye por ninguno de esos resultados ni se "
        "adivina un dominio nuevo. Se deja un buscador genérico sin símbolo como único fallback "
        "seguro hasta poder confirmar a mano cuál es (si lo hay) el sitio oficial vigente de "
        "Vertex Protocol. Coherente con que Vertex ya era el conector menos confirmable de todo el "
        "proyecto (ver README, bloqueado por red desde el hosting).",
    ),
    "apex": ExchangeLink(
        "https://omni.apex.exchange/trade/{base}USDT",
        "https://omni.apex.exchange/trade",
        confirmed=True,
        note="Confirmado en vivo contra omni.apex.exchange/trade/BTCUSDT -- mismo dominio "
        "(omni.apex.exchange) que ya usa nuestro conector real para la API, 2026-09-21.",
    ),
}

# Exchanges sin patrón de URL por símbolo confirmado -- se calcula del
# propio diccionario de arriba (no una lista aparte) para que nunca se
# quede desactualizada si se confirma uno de estos más adelante.
UNCONFIRMED_EXCHANGES: frozenset[str] = frozenset(
    name for name, entry in EXCHANGE_LINKS.items() if not entry.confirmed
)

# Exchanges cuya plantilla de URL es correcta (confirmed=True) pero que se
# comprobó EN VIVO, desde un navegador real en España, que bloquean el
# producto entero para esa región -- distinto de "sin confirmar": aquí el
# enlace está bien construido, el propio exchange lo rechaza. Igual que
# arriba, se calcula del diccionario para no quedar desactualizado.
REGION_RESTRICTED_EXCHANGES: frozenset[str] = frozenset(
    name for name, entry in EXCHANGE_LINKS.items() if entry.region_note
)


def build_link(exchange: str, base_symbol: str) -> tuple[str, bool]:
    """
    Devuelve (url, confirmado) para abrir el símbolo `base_symbol` (ej.
    "BTC", "EMBER", "1000PEPE") directamente en `exchange`.

    Si `exchange` no está en EXCHANGE_LINKS (no debería pasar para ninguno
    de los exchanges activos de este proyecto, pero por si se añade uno
    nuevo y se olvida registrar su URL aquí), se devuelve una búsqueda
    genérica en vez de un enlace roto o `None` -- mismo criterio de "nunca
    un hueco silencioso" que el resto del proyecto.
    """
    entry = EXCHANGE_LINKS.get(exchange)
    if entry is None:
        query = f"{exchange} {base_symbol} perpetual futures".replace(" ", "+")
        return f"https://www.google.com/search?q={query}", False

    if entry.template is None:
        return entry.fallback_url, False

    url = entry.template.format(base=base_symbol, base_lower=base_symbol.lower())
    return url, entry.confirmed
