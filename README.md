# Funding Toolkit

Herramienta propia de funding rates (cripto + RWA), construida combinando lo mejor de
ProFunding, Loris Tools y el selector delta-neutral de John5Cripto.

## Estado del build

Vamos construyéndola paso a paso. Progreso:

- [x] Paso 1 — Arquitectura del proyecto y modelo de datos común
- [x] Paso 2 — Conectores de datos: 10 CEX (Binance, Bybit, OKX, Bitget, Gate, BingX, Phemex vía ccxt; KuCoin/MEXC/HTX con conector propio — ccxt no soporta `fetchFundingRates()` para estos tres, ver sexta tanda más abajo; BingX/Phemex son la novena tanda, ampliación tras la comparativa con la competencia — Crypto.com se queda fuera a propósito, ver esa sección) + 15 DEX (Hyperliquid, Lighter, Paradex, Extended, Pacifica, Aster, edgeX, GRVT, Variational, RiseX, Backpack, Nado, Hibachi, Vertex y ApeX vía API directa/ccxt) — los 13 primeros DEX confirmados devolviendo datos reales en producción (Variational: 547 pares, RiseX: 30 pares tras dos rondas de fix, Backpack/Nado/Hibachi sin ningún error en su primer despliegue real, ver más abajo); Vertex y ApeX son la quinta tanda — ApeX confirmado en vivo, Vertex construido solo a partir de documentación (no se pudo alcanzar su API desde este entorno) y CONFIRMADO en producción bloqueado por red (mismo patrón que Binance/Bybit — ver sección dedicada). Se investigó también Drift Protocol y se descartó: su API quedó inutilizable tras el hackeo de ~$285-295M de abril 2026 y sus dominios oficiales redirigen a un fork no oficial ("Velocity Exchange") que no es Drift — ver sección dedicada
- [x] Paso 3 — Normalización de intervalos y cálculo de APR anualizado
- [x] Paso 4 — Snapshots históricos (SQLite) → APR histórico real 1h/24h/7d/30d
- [x] Paso 5 — Consistency Score, OI Depth, Price Spread y Volume 24h (con fallback contratos×mark_price para exchanges que no dan el USD directo; ver séptima tanda para Price Spread y octava tanda para Volume 24h)
- [x] Paso 6 — Vista ranking + vista matriz (CLI), con filtros por venue/exchange
- [x] Paso 9 — Interfaz Streamlit (Home + página Funding Rates: Ranking / Matriz / Histórico), desplegada en Streamlit Cloud
- [ ] Paso 7 — Alertas por Telegram (pendiente, a petición tuya)
- [ ] Paso 8 — Módulo de ejecución (pendiente, a petición tuya)
- [ ] Scheduler de snapshots en producción (pendiente, a petición tuya) — sin esto, Consistency Score e Histórico se quedan en blanco en el despliegue real

### DEX nuevos (Lighter, Paradex, Extended, Pacifica) — pendiente de verificar en vivo

Estos 4 conectores se construyeron a partir de la documentación pública de cada API
(no se pudieron probar contra la red real desde este entorno de desarrollo, que solo
tiene salida a PyPI). Antes de fiarte de sus números al 100%, comprueba en el
despliegue real:

- **Intervalo de funding**: Extended/Pacifica = 1h, Paradex/Lighter = 8h (Lighter
  liquida cada hora, pero el `rate` que da su API está normalizado a 8h — ver más
  abajo). Si el APR de alguno sale desproporcionado (ej. x8 de más o de menos),
  revisa `INTERVAL_HOURS` en `connectors/dex_<nombre>.py` — Extended y Pacifica
  siguen sin contrastar en vivo, así que no se puede descartar el mismo problema ahí.
- **Unidades del Open Interest**: Extended da el OI ya en USD directamente
  (`openInterest`, distinto de `openInterestBase`). Lighter, Paradex y Pacifica NO
  documentan explícitamente la unidad de su campo de OI — se asumió que es en
  unidades del activo base (igual que Hyperliquid) y se multiplica por el mark
  price para sacar el USD. Si al desplegar el OI de estos tres sale absurdamente
  alto o bajo, esa multiplicación es la primera sospechosa. **Este punto solo se
  ha verificado en vivo para Lighter (ver abajo) — Paradex y Pacifica siguen sin
  contrastar contra la red real.**

#### Bug real encontrado y corregido: Lighter mezclaba datos de otros exchanges

Con el primer despliegue real, el ranking salía dominado por "lighter" con valores
extremos y repetidos (ej. "+84.1%" en varios símbolos sin relación entre sí). Se
investigó contra la API en vivo de Lighter y se confirmó la causa: `/api/v1/funding-rates`
no es un endpoint solo de Lighter, es un endpoint de COMPARACIÓN que trae, para un
universo amplio de símbolos, la tasa de exchanges de referencia (binance, bybit,
hyperliquid) junto a la propia de Lighter — y ese universo incluye símbolos que
Lighter ni siquiera lista (se vieron en vivo tickers de acciones como "GME", "ORCL",
"TTWO", solo con fila `exchange:"binance"`, sin fila `"lighter"`).

El conector original, cuando un mercado no tenía una fila marcada `"lighter"`, caía a
"si solo hay una fila, es la propia" — y esa fila única resultó ser, en la práctica,
la tasa de otro exchange mal etiquetada como si fuera de Lighter. Eso es lo que
inflaba el ranking. **Ya está corregido**: ahora el conector es estricto, solo se
queda con la fila cuyo `exchange` sea exactamente `"lighter"`, y descarta el mercado
si esa fila no existe (verificado con un test que reproduce el escenario exacto).

#### Segundo bug encontrado y corregido: el `rate` de Lighter no es por hora, es por 8h

Tras corregir el bug anterior, BTC en Lighter seguía saliendo con un APR de 84.1% en
el ranking — desproporcionado. Comparando directamente con la interfaz oficial de
Lighter (captura del usuario: "1HR FUNDING: +0.0012%" para BTC) contra lo que devolvía
`/funding-rates` para ese mismo mercado (0.0096%), la diferencia era de exactamente 8
veces. Es decir: aunque Lighter liquida el funding cada hora, el campo `rate` de este
endpoint de comparación viene normalizado a un equivalente de 8h, no a la tasa horaria
real — tiene sentido, ya que ese mismo endpoint compara Lighter contra otros exchanges
que liquidan con otra frecuencia, así que normalizan todos a un periodo común para
poder comparar. El conector tenía `INTERVAL_HOURS = 1`, lo que multiplicaba el APR por
8 de más. **Ya está corregido**: ahora usa `INTERVAL_HOURS = 8`, y el APR de BTC en
Lighter pasa de 84.1% a los ~10.5% que coinciden con la interfaz oficial.

### DEX nuevos, segunda tanda (Aster, edgeX, GRVT)

Se eligieron mirando el ranking real de DEX de perpetuos por volumen/OI (no de memoria
— se consultó DefiLlama en vivo): tras Hyperliquid, Aster es el #2 mundial, edgeX y GRVT
están también en el top de los que faltaban por cubrir. Los tres tienen forma muy distinta
entre sí:

- **Aster**: no se escribió conector propio — ya está bien soportado por ccxt (se
  comprobó leyendo el código fuente de ccxt, no solo la bandera de capacidad), así que
  se reutiliza el mismo `CexConnector` genérico que usan los CEX, solo que declarado con
  `venue_type=DEX`. Es el más fiable de los tres nuevos, mismo nivel de confianza que
  cualquier CEX de la lista.
- **edgeX**: conector propio, pero de los más sencillos — un único endpoint bulk
  (`getTicker` sin `contractId`) trae funding rate, mark price y OI de todos los
  contratos de golpe. El intervalo de liquidación sí varía por contrato (a diferencia de
  Extended/Pacifica), así que se cruza con una segunda llamada a metadata.
- **GRVT**: el más delicado de los tres. Su API de mercado NO tiene ningún endpoint
  bulk — hay que pedir el ticker instrumento por instrumento, así que el conector lanza
  las peticiones en paralelo (pool de hilos) en vez de secuencialmente, para no disparar
  el tiempo de carga de la página. Es también el que más asunciones sin verificar
  acumula: la escala de los precios (÷ 1e9), la unidad del funding rate ("centibeeps",
  convertido asumiendo 1 centibeep = 1e-6) y el intervalo de liquidación (sin campo
  fiable encontrado, se usa 8h por defecto). **Es el primer candidato a revisar en
  cuanto tengas datos reales**, igual que hicimos con Lighter — compara al menos un
  símbolo contra la interfaz oficial de GRVT.

#### Bug real encontrado y corregido: edgeX daba 0 pares sin ningún error visible

En el primer despliegue de esta segunda tanda, edgeX y GRVT salieron con 0 pares en el
diagnóstico "pares traídos por exchange" — pero, a diferencia de binance/bybit/etc., NO
aparecían en el banner de "Algunos exchanges no respondieron". Eso significa que no
saltó ninguna excepción: el conector "funcionó" pero no encontró nada que devolver, lo
cual es mucho más difícil de diagnosticar que un error explícito.

Para edgeX se investigó pidiendo la API en vivo directamente (no solo la
documentación): el endpoint bulk que usaba (`getTicker` sin `contractId`) devolvía
`"data": []` de forma consistente, probado con distintos contratos. En cambio, el
endpoint de funding por contrato (`getLatestFundingRate`) sí devolvió datos reales y
completos. **Ya está corregido**: el conector ahora usa ese endpoint que se comprobó
que funciona, uno por contrato en paralelo (como GRVT) — a cambio, ya no trae Open
Interest, porque ese endpoint no lo incluye y no se encontró ninguna alternativa fiable
para edgeX; el OI de edgeX sale como "s/d" hasta que se encuentre otra fuente.

Además, ambos conectores (edgeX y GRVT) ahora lanzan un error explícito si TODOS sus
contratos/instrumentos fallan en vez de devolver una lista vacía en silencio — así que
si algo similar vuelve a pasar (por ejemplo con GRVT, que sigue sin verificar en vivo),
el motivo real aparecerá en el banner de errores de la interfaz en vez de un "0 pares"
mudo que hay que ir a investigar a ciegas.

**Confirmado en el redespliegue real tras este fix**: edgeX pasó de 0 a **169 pares**
con datos normales — el cambio de endpoint funcionó. GRVT, en cambio, disparó el nuevo
error explícito con un dato revelador: *"0/194 instrumentos fallaron"* — es decir,
CERO peticiones dieron excepción (las 194 respondieron 200 OK), pero ninguna trajo un
ticker reconocible. Se añadió un segundo nivel de diagnóstico (muestra del JSON crudo
en el propio mensaje de error) y, en el despliegue siguiente, ese diagnóstico reveló
la causa exacta sin necesitar otra ronda de build a ciegas: la clave `"result"` sí es
correcta y el ticker sí trae datos, pero el campo del funding rate no se llama
`funding_rate_curr` (lo que decía el SDK oficial) sino **`funding_rate_8h_curr`**.
**Ya corregido** — el conector ahora lee ese campo (con el nombre antiguo como
segundo intento por compatibilidad). Como beneficio colateral, el propio nombre del
campo confirma que el intervalo de liquidación de GRVT es de 8h, lo que ya se estaba
usando como valor por defecto pero ahora tiene respaldo directo en vez de ser solo
"el más común del sector". Sigue pendiente de confirmar contra la interfaz oficial de
GRVT: la escala de precios (÷ 1e9) y la conversión de "centibeeps" — ver el docstring
de `connectors/dex_grvt.py` para el detalle completo.

**Cerrado**: en el despliegue siguiente a este último fix, GRVT pasó de 0 a **194
pares** y ya no aparece en el banner de errores. Con esto los 8 DEX de esta segunda
tanda (Aster, edgeX, GRVT) están devolviendo datos reales en producción — 11 exchanges
en total funcionando, sumados a los CEX/DEX ya estables. Queda pendiente, sin urgencia,
verificar al menos un símbolo de GRVT (ej. BTC) contra su interfaz oficial para
confirmar la escala de precios y la conversión de funding rate — es el mismo tipo de
comprobación que se hizo con Lighter, y sigue siendo el primer candidato a revisar si
algún número de GRVT se ve desproporcionado en el ranking o la matriz.

### DEX nuevos, tercera tanda (Variational, RiseX)

Ambos son, en diseño, los conectores más simples de todo el proyecto: un único
endpoint bulk trae funding rate + mark price + open interest de todos los mercados de
golpe — sin pool de hilos, sin llamada aparte a metadata.

- **Variational**: DEX omnichain (RFQ + AMM híbrido). Un solo endpoint público,
  `GET /metadata/stats`, trae todo. Aquí apareció la asunción más importante de esta
  tanda: el campo `funding_rate` de cada mercado **no es la tasa cruda del intervalo,
  sino que todo apunta a que ya viene anualizada (APY)** — los valores en vivo
  observados (ej. REZ en -1.333404 sobre un intervalo de 4h) serían tasas del ±133% cada
  4 horas si se tomaran literalmente, algo imposible dado el límite de funding del
  2%/hora que documenta el propio exchange, pero encajan perfectamente como APY. Se
  contrastó además contra cómo muestra estas mismas tasas `loris.tools` (uno de los dos
  trackers en los que se inspira este proyecto) y la conclusión fue consistente. Por
  eso el conector "desanualiza" el APY a una tasa por intervalo antes de guardarlo — si
  no lo hiciera, `core/normalize.py` volvería a anualizar algo que ya estaba anualizado
  y los APR de Variational saldrían disparatadamente altos. Ver el docstring de
  `connectors/dex_variational.py` para el detalle completo del razonamiento.
- **RiseX**: DEX sobre RISE Chain. Un solo endpoint, `GET /v1/markets` contra
  `https://api.rise.trade`, con el diseño más limpio de todos: el propio endpoint da el
  intervalo de liquidación explícito en nanosegundos (`funding_interval`), así que no
  hace falta ninguna adivinanza de intervalo como sí hizo falta con GRVT o Lighter en su
  momento. Cuesta encontrar la URL correcta porque RiseX reparte su documentación en dos
  dominios distintos (`docs.risechain.com`, conceptual, y `developer.rise.trade`, la
  referencia real de API) — se usó la segunda. No se logró un fetch en vivo con datos
  reales de `/v1/markets` esta sesión (solo el ejemplo de su spec OpenAPI), así que el
  conector se apoya en lo documentado, no en datos contrastados como Variational.

#### Bug real encontrado y corregido: risex tiraba `'str' object has no attribute 'get'`

En el primer despliegue real, apenas se subió el zip, el diagnóstico "pares traídos por
exchange" seguía sin mostrar ni `variational` ni `risex` — ni un dato, ni un error, nada
— a pesar de que el repo de GitHub ya tenía el código correcto. La causa no era del
código: Streamlit Cloud había hecho `git pull` pero no había reiniciado de verdad el
proceso de Python, así que seguía sirviendo los módulos viejos desde memoria (mismo tipo
de desajuste "código en disco ≠ código corriendo" que ya se había visto antes con otros
despliegues). Un **Reboot app** manual desde el panel de Streamlit Cloud lo resolvió —
tras eso sí aparecieron ambos en el diagnóstico, cada uno con su resultado real.

**Variational salió redondo a la primera**: 547 pares con datos reales, sin errores. Solo
6 tickers se saltaron (`USOILP`, `US500S`, `US100S`, `XAGS`, `XAUS`, `UKOILP` —
productos sintéticos de materias primas/índices: petróleo, S&P 500, Nasdaq 100, plata,
oro) por traer `funding_interval_s: 0`, algo que el conector detecta y descarta fila por
fila sin tirar el resto — probablemente estos productos no liquidan funding de la forma
habitual, no es un bug del conector.

**RiseX tiró un error real, y en dos rondas.** Primera ronda:
`AttributeError: 'str' object has no attribute 'get'`. La causa: el conector asumía
que `payload["markets"]` es una LISTA de mercados, tal como lo documenta el ejemplo
del OpenAPI spec — pero la respuesta real envuelve los mercados en un DICCIONARIO
indexado por `market_id`, no en un array. Se corrigió para iterar `.values()` cuando
el contenedor es un dict — pero esa corrección seguía asumiendo que la clave
envolvente se llama `"markets"` o `"data"`.

Segunda ronda, con el fix anterior ya desplegado (y tras el `Reboot` manual, que
también hizo falta esta vez): el error cambió a "2/2 mercados se saltaron", con el
propio mensaje de error mostrando — de regalo — la lista completa de 32 mercados
reales de RiseX aplastada en una sola entrada. Eso reveló la causa real: la respuesta
NO usa ni `"markets"` ni `"data"` como nombre de clave en el nivel donde el conector
las buscaba, así que caía a un último recurso (tratar el propio payload como el
contenedor) que en realidad mezclaba la lista completa de mercados con un campo de
timestamp suelto en un dict de solo un par de claves — de ahí que `.values()`
devolviera "la lista entera" como una fila y "el timestamp" como otra, en vez de cada
mercado por separado.

**Ya corregido de raíz, cambiando de estrategia**: en vez de seguir adivinando el
nombre de la clave envolvente (que ya ha fallado dos veces), el conector ahora busca
la lista de mercados **por forma**: recorre el payload buscando la primera lista de
objetos que "parecen" un mercado (tienen `market_id` o `current_funding_rate`), o un
diccionario cuyos valores todos lo parecen — mirando tanto en el nivel raíz como un
nivel de anidación dentro de cada valor. Esto es agnóstico al nombre exacto de la
clave, así que sobrevive a que RiseX vuelva a cambiarla sin aviso.

El propio error de la segunda ronda, al traer datos reales, permitió confirmar de
golpe varias cosas que antes eran solo asunciones sin verificar:

- **`current_funding_rate` SÍ es una fracción decimal directamente utilizable** — BTC
  traía `"0.000004358528047845"` con intervalo de 1h, lo que da un APR ≈ 3.8%, una
  cifra normal. Confirmado: NO hay que dividir entre 1e18.
- **`open_interest` SÍ está en unidades del activo base**, no en USD — BTC traía
  `open_interest: "142.86417"` con `mark_price ≈ 77,047`; en USD directo serían solo
  ~$142 de OI (absurdo para BTC en un DEX con millones de volumen diario), mientras
  que 142.86 BTC × mark price ≈ $11.0M es una cifra realista. Confirmada la asunción
  que ya se venía usando.
- **`base_asset_symbol` NO viene limpio** — viene con el par completo pegado, ej.
  `"BTC/USDC"` en vez de `"BTC"`. Esto no se había anticipado (el ejemplo de la spec sí
  mostraba "BTC" limpio) y habría roto el cruce de símbolos con el resto de exchanges.
  **Ya corregido**: se recorta usando `quote_asset_symbol` con precisión.
- **Hay mercados inactivos mezclados con los activos en la misma respuesta** — un
  "DOGE/USDC" viejo marcado `[deprecated-...]` con todo en cero, conviviendo con un
  "DOGE/USDC" nuevo y activo; y mercados como ONDO con `active: false` y precios/volumen
  en cero (pendientes de lanzamiento, probablemente). **Ya corregido**: se descarta
  cualquier mercado con `active` distinto de `true` antes de procesarlo.

Todo esto está probado con los 32 mercados reales que trajo el propio error de
producción (pegados literalmente en un test, no inventados) — cubre el caso que
falló en producción, más las formas anteriores (dict-de-id, lista plana) para no
volver a romper lo que ya funcionaba. Ver el docstring de `connectors/dex_risex.py`
para el detalle completo.

**Cerrado**: en el redespliegue siguiente a este fix, risex pasó a traer **30 pares**
reales (32 mercados totales − 2 inactivos filtrados, exactamente lo esperado) sin
ningún error en el banner. Con esto los 10 DEX de este proyecto están devolviendo
datos reales en producción.

#### Confirmado contra la interfaz oficial: los APR extremos de Variational son reales

Tras este mismo redespliegue, el top del ranking salió dominado por Variational con
APRs muy por encima de lo visto en cualquier otro exchange — STORJ en -8497%, LSK en
-6751%, ARK en -2706%, GLM en -3111%, STEEM en -3051%, POWR en -2484%, POLYX en
-2466%, PUNDIX en -2050%. Esto ponía en duda la asunción de "APY ya anualizado"
documentada en `connectors/dex_variational.py`, así que se comprobó contra la propia
interfaz de Variational — exactamente el mismo tipo de verificación que cerró las
dudas de Lighter y GRVT en su momento.

**Confirmado**: la propia interfaz de Variational muestra una columna llamada
literalmente **"Ann. Funding"** (funding anualizado), y para STORJ mostraba
**-6,863.56%** en el momento de la comprobación — del mismo orden de magnitud que el
-8497.1% que había en el ranking (la diferencia se explica porque son dos capturas en
momentos distintos de un mercado muy volátil: STORJ traía un -40.53% de cambio en 24h
en esa misma captura, así que su funding puede moverse mucho en minutos). Esto
confirma dos cosas a la vez:

- Que Variational reporta el funding **ya anualizado**, tal como se había asumido —
  la propia UI lo llama "Ann. Funding", no "tasa del intervalo".
- Que estos números extremos son reales, no un artefacto de la conversión del
  conector: STORJ es, en la práctica, un mercado de baja liquidez en Variational con
  un desequilibrio fuerte entre longs y shorts, lo que dispara el funding para
  corregirlo — exactamente el tipo de dato "extremo pero real" que ProFunding/Loris
  también enseñan.

De paso, la misma captura permitió contrastar el Open Interest: la UI mostraba
$43.76K de OI total para STORJ, y el ranking de esta herramienta mostraba $22,117 de
OI en el lado "long" — aproximadamente la mitad, coherente con un OI total repartido
entre long y short. Confirma también la asunción de que `open_interest` de Variational
ya viene en USD (no hubo que multiplicar por nada para llegar a un número del orden
correcto).

Con esto, las dos asunciones documentadas de Variational (escala del funding rate y
unidad del open interest) quedan confirmadas contra datos reales — no quedan
asunciones pendientes de este conector. Nota técnica para quien toque este código más
adelante: la conversión "APY → tasa por intervalo → re-anualizar" que hace
`core/normalize.py` es, en los hechos, matemáticamente neutra sobre el APR final que
se muestra (`apr = (apy/periods_per_year) × periods_per_year × 100 = apy × 100`,
el `interval_hours` se cancela) — así que el número que se ve en pantalla es siempre
`funding_rate × 100` tal cual lo reporta Variational, independientemente del
`funding_interval_s`. El `interval_hours` sí importa para el resto de columnas
derivadas (tasa cruda del intervalo, "cada 8h"), solo no para el APR anualizado.

#### Bug real encontrado y corregido: Aster daba mercados "fantasma" (funding real, Open Interest $0)

Ya con los 10 DEX en producción, apareció STORJ en el top del ranking como
`Long en variational (-6078.9%) / Short en aster (+1.4%)`, con un spread de más de
6000%. El usuario reportó que, al buscar "Storj" directamente en la web de Aster, no
aparecía por ningún sitio.

Investigación: se comprobaron en vivo los dos endpoints relevantes de Aster —
`GET /fapi/v1/exchangeInfo` (el listado oficial de símbolos operables, lo que
alimentaría su buscador: 76 símbolos, ninguno STORJ) y `GET /fapi/v3/premiumIndex`
(el endpoint EXACTO que usa `fetch_funding_rates()` de ccxt para Aster — confirmado
leyendo el propio código fuente de ccxt con `inspect.getsource`, no solo su
documentación: 500 entradas, tampoco ninguna STORJ). Ninguno de los dos tenía rastro
de STORJ en el momento de la comprobación, lo que en un primer momento dejó la duda
como un posible problema de timing/caché en vez de un bug de verdad.

La pista definitiva llegó de una fuente independiente: el usuario comprobó el mismo
símbolo en **Loris Tools** (uno de los trackers en los que se inspira este proyecto)
y su "Storj Exchange Breakdown" también lista a Aster — pero con **Open Interest
$0.0** y Funding (8h)/(7d) en blanco ("—"). Que un tracker externo, que lee la API de
Aster de forma completamente independiente de este proyecto, vea exactamente el mismo
mercado "fantasma" confirma que no es un bug de parsing de este conector: Aster
realmente tiene, en algún momento, un mercado STORJ presente en su superficie de API
(funding rates y open interest por símbolo) sin que sea un mercado operable de verdad
— no aparece en su listado oficial de símbolos ni en su buscador, y no tiene ninguna
posición abierta.

**La causa raíz**: el fetch masivo de funding rates (`/fapi/v3/premiumIndex`) le
devuelve a ccxt una tasa numérica "normal" para STORJ aunque el mercado esté
efectivamente muerto — probablemente un valor de relleno/última-tasa-conocida que
Aster no limpia de ese endpoint aunque ya no sea operable. El propio conector
(`connectors/cex_ccxt.py`) no tiene forma de distinguir esto de un mercado real solo
con ese dato: hace falta el Open Interest real, que este proyecto solo pide para el
top N de oportunidades (`OI_ENRICH_TOP_N = 10` en `pages/1_Funding_Rates.py`, vía
`fetch_open_interest_usd`), y que para STORJ en Aster resultó ser exactamente `$0` —
coincidiendo con lo que mostraba Loris.

**La corrección**: en vez de intentar adivinar/filtrar símbolos "muertos" por nombre
(fragil, y no ataja el problema en otros exchanges), se añadió
`has_dead_liquidity()` en `core/opportunities.py`: cualquier oportunidad cuyo Open
Interest YA CONFIRMADO (no `None` — eso sigue siendo "no consultado todavía", se deja
tal cual como "—") sea exactamente `$0` en una de las dos piernas se saca del
ranking antes de mostrarlo, con un aviso explicando por qué (`pages/1_Funding_Rates.py`,
más un expander de diagnóstico igual que el de errores de OI). Un mercado sin ninguna
posición abierta no es una operación ejecutable por mucho que el spread de APR salga
enorme — no hay nadie al otro lado. Esto es genérico: protege contra este mismo patrón
en cualquier exchange, no solo Aster, y solo actúa sobre dato confirmado, nunca sobre
una ausencia de dato.

**Vuelta al mismo bug — el fix de `has_dead_liquidity()` no bastaba**: tras desplegar
lo de arriba, STORJ/Aster seguía apareciendo en el top del ranking, ahora con
`OI short ($)` en "—" en vez de en "$0". Dos fallos distintos, encadenados:

1. `collect_oi_targets()` en `core/opportunities.py` solo pedía el Open Interest real
   para piernas con `venue_type == VenueType.CEX`. Aster se declara
   `venue_type=VenueType.DEX` (es arquitectónicamente un DEX, aunque use
   `CexConnector`/ccxt por debajo — ver `connectors/cex_ccxt.py`), así que nunca
   entraba en la lista de piernas a consultar. El criterio correcto no es el
   venue_type (que es una etiqueta de categoría/visualización), sino "¿este exchange
   tiene un conector con OI registrado?" — se cambió a comprobar pertenencia en
   `CEX_FACTORY_BY_NAME` en vez de comparar venue_type.
2. Aunque se corrija (1), `"aster"` ni siquiera estaba en `CEX_FACTORY_BY_NAME` — se
   añadió. Pero al intentarlo de verdad se descubrió algo más de fondo: **ccxt NO
   soporta `fetch_open_interest()` para Aster en absoluto** (`ex.has["fetchOpenInterest"]`
   es `False`, y su propio código fuente confirma que cae en
   `raise NotSupported(...)` — no es una bandera mal declarada como pasó con
   GRVT/`fetchFundingRates`, aquí genuinamente no está implementado). Se intentó además
   pedir `GET /fapi/v1/openInterest` directo (bypaseando ccxt) contra la propia API de
   Aster — devolvió **400** incluso para un símbolo real y activo como BTCUSDT (un
   endpoint inexistente da 404, no 400, así que el endpoint existe pero rechaza la
   petición por algún motivo no confirmado, posiblemente autenticación). Conclusión: el
   Open Interest de Aster, hoy, **no se puede consultar de forma fiable** desde este
   proyecto — ni vía ccxt ni vía REST directo.

Eso deja sin piso la estrategia de "confirmar OI en $0" como forma de detectar este
mercado fantasma en Aster específicamente (sigue siendo válida y se mantiene para
cualquier otro exchange donde el Open Interest sí se pueda consultar). El fix real,
más de raíz, va en el propio conector: **`connectors/cex_ccxt.py`**, dentro de
`CexConnector.fetch_funding_rates()`, ahora descarta cualquier símbolo que
`fetch_funding_rates()` de ccxt devuelva pero que NO esté en `self._client.markets`
— el listado oficial de mercados operables que ccxt ya carga internamente (vía
`load_markets()`) como efecto secundario de la propia llamada, así que este chequeo
no cuesta ninguna petición extra. Esto ataja el problema en el origen exacto que
reportó el usuario ("no me sale al buscar Storj en Aster"): si el símbolo no está en
el listado oficial —el mismo que alimentaría el buscador del propio exchange—, se
descarta antes de convertirse siquiera en un `FundingRate`, para cualquier símbolo y
en cualquier exchange que use `CexConnector`, no solo para el top N del ranking. Se
verificó con un cliente ccxt simulado (un mercado real presente en `self.markets` +
uno "fantasma" ausente de ahí pero presente en la respuesta de funding rates): el
fantasma se descarta, el real se mantiene.

**Tercera vuelta — el filtro aún dejaba pasar a STORJ**: tras desplegar lo de arriba,
STORJ seguía en el ranking bajo `aster`. Investigando `parse_market()` de ccxt para
Aster se vio la causa: ccxt NO excluye de `self.markets` los símbolos cuyo `status`
en `exchangeInfo` no es `"TRADING"` (delistados, suspendidos, en pre-lanzamiento...)
— los incluye igual, solo que marcados con `active: False`. El chequeo anterior
("¿está el símbolo en `self.markets`?") daba por bueno cualquier símbolo presente,
activo o no, así que a STORJ —presente pero inactivo— se le seguía dejando pasar.
Se amplió el filtro para comprobar también `active`: un símbolo se descarta si falta
del listado por completo O si está pero con `active is False`. Se verificó con un
cliente ccxt simulado con los tres casos a la vez (uno real y activo, uno presente
pero `active: False`, uno ausente del todo): solo el real y activo se mantiene.

Se deja registrado `"aster": aster` en `CEX_FACTORY_BY_NAME` de todos modos (no hace
daño: si algún día ccxt añade soporte, empezará a funcionar solo) — con esto, un
intento de pedir su OI real para el top N falla con un error explícito
(`NotSupported: aster fetchOpenInterest() is not supported yet`) visible en el
expander de diagnóstico "OI Depth no disponible", en vez de un "—" mudo sin
explicación. Entre el filtro por listado oficial (que ya evita que aparezcan
fantasmas) y este error explícito (que explica honestamente por qué Aster nunca va a
mostrar profundidad de OI en el top del ranking), la interfaz ya no deja al usuario
adivinando.

### DEX nuevos, cuarta tanda (Backpack, Nado, Hibachi)

Igual que con las tandas anteriores: investigación primero (contra la API en vivo con
WebFetch, no solo contra la documentación), luego el código, luego pruebas sintéticas
que reproducen la forma real de la respuesta de cada exchange (mockeando `requests`),
y por último este apartado del README. Este entorno de desarrollo no tiene salida de
red hacia estos exchanges (solo hacia PyPI), así que las pruebas sintéticas — con
datos calcados de respuestas reales observadas vía WebFetch — son el sustituto de
"correrlo de verdad", igual que con Lighter/Paradex/Extended/Pacifica en su momento.

**Los tres son conectores "planos" — ninguno necesita pool de hilos** (a diferencia de
edgeX/GRVT): Backpack y Hibachi resuelven todo con endpoints bulk directos; Nado
necesita dos llamadas GET (una para precio/funding/OI, otra para el estado del
mercado), pero ambas bulk, sin iterar símbolo a símbolo.

- **Backpack** (`connectors/dex_backpack.py`): tres endpoints bulk —
  `/api/v1/markets` (metadata + `fundingInterval` en ms, y `marketType` para separar
  perp de spot, que vienen mezclados en el mismo listado), `/api/v1/markPrices`
  (funding rate crudo del intervalo + mark price) y `/api/v1/openInterest` (OI en
  activo base, se multiplica por mark price). Se descartan los mercados con
  `orderBookState != "Open"`. Todo confirmado en vivo vía WebFetch.
- **Nado** (`connectors/dex_nado.py`): el más enrevesado de investigar. El endpoint
  bulk que documenta la API (`/gateway/v1/query?type=all_products`) **ignora el
  parámetro `type` en vivo** y siempre devuelve el mismo payload que `type=symbols`
  (solo specs de contrato, sin precio/funding/OI) — se probaron una decena de valores
  de `type=` distintos y todos hicieron lo mismo. El dato real bulk estaba en una
  superficie de API totalmente distinta y no mencionada donde se esperaba:
  `GET /archive/v2/contracts` (API v2, confirmada en vivo, con funding/mark
  price/OI-en-USD ya calculados para todos los mercados de golpe). El estado
  operable (`trading_status`) sí hay que sacarlo de `/gateway/v1/query?type=symbols`
  (ese sí funciona tal cual la doc). El `funding_rate` de `/archive/v2/contracts` es,
  según la documentación de mecánica de funding de Nado, la tasa equivalente a 24h
  (3× la tasa de 8h), no la tasa horaria real que se liquida — se divide entre 24
  antes de guardarlo, mismo patrón que la conversión APY→intervalo que ya se hizo
  para Variational. **Esta división entre 24 está pendiente de contrastar contra la
  interfaz oficial de Nado en producción real** — si el APR de Nado sale
  sistemáticamente ×24 o ÷24 de más, este es el sitio a revisar primero.
- **Hibachi** (`connectors/dex_hibachi.py`): investigado instalando el SDK oficial
  (`hibachi-xyz` de PyPI, requiere Python ≥3.13 — se instaló en un virtualenv aparte)
  y leyendo su código fuente directamente, en vez de fiarse solo de la doc. Esto
  reveló un bug de la propia doc/SDK: el cliente se construye con un parámetro
  `api_url` que por defecto apunta a `api.hibachi.xyz`, pero las peticiones públicas
  de datos de mercado en realidad usan SIEMPRE `data_api_url`
  (`data-api.hibachi.xyz`) — confirmado leyendo `executors/httpx.py` del SDK.
  Probar `api.hibachi.xyz/market/exchange-info` a pelo (lo que sugeriría la doc a
  primera vista) da 404; el dominio correcto es `data-api.hibachi.xyz`. Una vez
  resuelto eso, el propio endpoint `/market/inventory` (pensado para listar
  mercados) ya trae funding/mark price/OI para todos los mercados de golpe — no
  hacían falta las llamadas por símbolo que sugería la doc a primera vista. Único
  punto sin confirmar en vivo: el intervalo de liquidación (se usa 1h fijo, según la
  doc conceptual de Hibachi — la API no lo expone como campo explícito, a diferencia
  de Backpack/RiseX/Aster).

En los tres casos se sigue el mismo patrón fail-loud del resto de conectores:
`RuntimeError` si no queda ningún par válido, `logger.warning` por símbolos
descartados individualmente sin tirar el resto, y el mismo filtro de "solo mercados
realmente operables" que ya nos enseñó el caso Aster/STORJ (aquí: `orderBookState`
en Backpack, `trading_status` en Nado, `status`+`symbolStatus` en Hibachi).

### DEX nuevos, quinta tanda (Vertex, ApeX) — y por qué Drift se descartó

Se pidió investigar tres exchanges: Drift Protocol, Vertex Protocol y ApeX Protocol.
Drift se descartó por completo tras la investigación — vale la pena documentar por qué,
para no volver a proponerlo sin más contexto:

**Drift Protocol**: su infraestructura de API pública lleva caída desde que sufrió un
hackeo de ~$285-295M (DPRK, abril 2026) — los dominios documentados
(`data.api.drift.trade`, `mainnet-beta.api.drift.trade`, `dlob.drift.trade`) no
resuelven DNS. Sus propios dominios de docs/app (`docs.drift.trade`, `app.drift.trade`)
redirigen ahora a **Velocity Exchange**, un fork que sus propios documentos afirman
explícitamente que NO es Drift ("Velocity should not be described simply as Drift
under a new name") — con solo 4 mercados y muy poca liquidez real. Además, los datos
de Drift en sí (cuando su API funcionaba) vivían on-chain vía Solana RPC
(`driftpy`, leyendo cuentas `PerpMarket` directamente), no detrás de una REST API
simple como el resto de exchanges de este proyecto — arquitectura totalmente distinta
que habría exigido un módulo aparte. Se decidió (contigo) saltar Drift por ahora en vez
de etiquetar el fork Velocity como si fuera Drift.

**Vertex Protocol** (`connectors/dex_vertex.py`): es la arquitectura de la que Nado es
fork, pero NO es un simple "copiar Nado" — se comprobó activamente y hay diferencias
reales. El equivalente al endpoint bulk `GET archive/v2/contracts` de Nado no existe en
Vertex según su propia doc: su superficie "archive" es **POST** con cuerpo JSON, no GET.
**No se pudo verificar nada de esto en vivo** — los hosts `*.prod.vertexprotocol.com`
no son alcanzables desde este entorno de desarrollo (ni siquiera se pudo resolver su
`robots.txt`), así que este conector está construido enteramente a partir de la
documentación oficial, con el mismo nivel de confianza que tuvieron en su momento
Lighter/Paradex/Extended/Pacifica: pendiente de confirmar contra tráfico real. Dos
asunciones documentadas explícitamente como inciertas en el propio conector:
- El open interest (`open_interests` del endpoint `market_snapshots`) se asume que
  YA viene en USD (al revés que la mayoría de conectores de este proyecto) — el único
  valor de ejemplo real de la documentación, dividido entre 1e18, da ~2.9M; interpretado
  como unidades de BTC sería un open interest físicamente imposible (más BTC del que
  existe en circulación), así que USD es la lectura más plausible, pero no hay forma de
  confirmarlo sin acceso real a la API.
- No hay ningún campo de estado operable documentado para `type=symbols` en Vertex (a
  diferencia de Aster/RiseX/Backpack/Nado/Hibachi) — así que este conector NO filtra
  mercados fantasma/delistados. Es un hueco conocido y deliberado, no un descuido.

**ApeX Omni** (`connectors/dex_apex.py`): el más limpio de investigar de los tres —
confirmado 100% en vivo vía WebFetch. Dato importante: ApeX tuvo un producto anterior
("ApeX Pro", sobre StarkEx) que se discontinuó (comunicado oficial "ApeX Pro Sunset",
marzo 2025) — su API vieja ya no funciona (devuelve error interno genérico), así que
este conector apunta exclusivamente al producto actual, "ApeX Omni"
(`omni.apex.exchange`). Hallazgo real de arquitectura: a diferencia de TODOS los demás
DEX de este proyecto, ApeX Omni no tiene ningún endpoint bulk que funcione — se probó
en vivo `/v3/ticker` sin símbolo, con varios símbolos separados por coma, y ambos casos
devuelven `{"data": []}` vacío; hay que pedir el ticker de cada símbolo por separado.
Por eso este conector usa un pool de hilos (mismo patrón que edgeX/GRVT), pidiendo el
universo de símbolos candidatos desde `/v3/config` y su ticker individual en paralelo.
Tampoco hay ningún campo de estado operable — el proxy real es si `/v3/ticker` de ese
símbolo devuelve datos o `[]` (ya lo resuelve el propio pool de hilos, sin filtro
aparte). Funding rate y Open Interest confirmados en vivo con el mismo rigor que
Backpack: `fundingRate` es tasa cruda horaria (no anualizada), `openInterest` viene en
unidades del activo base y hay que multiplicarlo por `markPrice`.

#### Confirmado en el primer despliegue real: Vertex bloqueado, ApeX con ruido real (corregido)

Backpack, Nado y Hibachi entraron limpios en su primer despliegue real (sin ningún
símbolo descartado, ver más abajo). Vertex y ApeX, no — cada uno por un motivo distinto:

- **Vertex: 0 pares, `SSLEOFError` al conectar con `gateway.prod.vertexprotocol.com`.**
  No es un bug del conector — es el mismo fallo de conexión que ya se veía venir en la
  sección de arriba (no se pudo alcanzar ese host ni siquiera desde este entorno de
  desarrollo), ahora CONFIRMADO también desde el propio Streamlit Cloud, no solo desde
  aquí. El patrón (fallo a nivel de conexión/TLS, no un error de la aplicación) es el
  mismo que ya sufren Binance/Bybit en este despliegue — probablemente Vertex bloquea
  conexiones desde rangos de IP de proveedores cloud/datacenter. No hay nada que
  arreglar en el código: el conector se queda ahí, fail-loud como los demás (se ve en
  el banner "Algunos exchanges no respondieron"), a la espera de que algún día responda
  desde esta ubicación de despliegue.
- **ApeX: 89 pares reales sí llegaron, pero 271/370 símbolos candidatos fallaron con
  403.** Investigado: casi ninguno de esos 271 era un mercado cripto real — eran
  mercados de predicción y apuestas deportivas de ApeX (`Raptors_Win_Against_Hornets_Nov29USDT`,
  `Christopher_Waller_nominated_as_Fed_ChairUSDT`...) mezclados sin ningún campo
  distintivo en `data.contractConfig.tokens`. **Ya corregido**: se añadió un filtro por
  forma (`_looks_like_crypto_token()`) que descarta cualquier token con "_" antes de
  pedir su ticker — ningún token cripto real de ApeX lleva guion bajo, así que el
  filtro no puede descartar un mercado legítimo por error. Quedan sin filtrar (a
  propósito) unos pocos símbolos de acciones/materias primas (`AAPLUSDT`, `XAUUSDT`)
  que también dieron 403 — probablemente RWA con acceso restringido por compliance, no
  fantasmas; no rompen nada, solo generan algo de log residual.

  **Actualización (2026-09-18) — ya no es ese ruido, es un bloqueo de red nuevo.** El
  usuario preguntó por este error viendo un despliegue donde ApeX dio 403 en
  **186/186 símbolos (el 100%)**, incluidos cripto reales que antes sí respondían
  (SOLUSDT, XRPUSDT, 1000SHIBUSDT). Eso ya no encaja con el patrón de arriba (que
  dejaba pasar los cripto reales y solo bloqueaba mercados de predicción/apuestas) —
  es el mismo patrón de bloqueo a nivel de red/IP que ya sufren Binance, Bybit y
  Vertex desde Streamlit Cloud (ver más arriba y más abajo en este README):
  probablemente ApeX empezó a bloquear también el rango de IPs de Streamlit Cloud.
  No hay nada que arreglar en el código — el conector ya lo maneja igual que los
  demás exchanges bloqueados (falla ese exchange en concreto, se ve en el banner
  "Algunos exchanges no respondieron", el resto de la app sigue funcionando). Se
  mejoró el mensaje de error en `connectors/dex_apex.py` para decir explícitamente
  "esto es un bloqueo de red, no un bug" cuando el 100% de los fallos son 403, en vez
  de dejar que parezca el mismo ruido de mercados de predicción ya conocido.

### CEX nuevos, sexta tanda (KuCoin, MEXC, HTX — conector propio en vez de ccxt)

KuCoin Futures, MEXC Futures y HTX (Huobi) estaban en la lista de 8 CEX desde
el Paso 2, pero los tres fallaban en producción con el mismo motivo:
`ccxt.<exchange>.fetch_funding_rates()` lanza `NotSupported` — comprobado
leyendo el código fuente de ccxt, no solo la bandera `has[...]`. No es un
bloqueo de red (a diferencia de Binance/Bybit/Vertex, ver más abajo): ccxt
simplemente no tiene implementado ese método para estos tres exchanges. La
solución fue la misma que ya se aplicó con los DEX que no estaban en ccxt
(edgeX, GRVT...): escribir un conector propio por exchange, investigado
contra la API real (`connectors/cex_kucoin.py`, `cex_mexc.py`, `cex_htx.py`),
y sustituir su entrada en `ALL_CEX_FACTORIES`/`CEX_FACTORY_BY_NAME` en
`cex_ccxt.py`. Los otros 5 CEX (Binance, Bybit, OKX, Bitget, Gate) siguen
usando `CexConnector` vía ccxt sin cambios.

Nota sobre el propio proceso de investigación: los tres hosts
(`api-futures.kucoin.com`, `contract.mexc.com`, `api.hbdm.com`) están en la
lista de dominios bloqueados por la política de red de ESTE sandbox de
desarrollo (confirmado vía `/__agentproxy/status` — bloqueo del proxy de
egress, no del exchange), así que toda la investigación se hizo con
`WebFetch` en vez de `curl` directo. Sigue siendo contra la API real en
producción, no contra documentación — no debería fallar por este motivo
concreto en Streamlit Cloud, que no tiene esta restricción.

- **KuCoin Futures**: el conector más simple de los tres — un único endpoint
  (`GET /api/v1/contracts/active`) trae metadata, funding rate, mark price,
  index price y open interest de golpe, sin pool de hilos. Bug de cruce de
  símbolos evitado a propósito: KuCoin usa "XBT" (no "BTC") como
  `baseCurrency` para Bitcoin — sin normalizarlo, KuCoin nunca habría
  cruzado con el resto de exchanges en el ranking. `openInterest` viene en
  Nº de contratos, se multiplica por `multiplier` (activo base/contrato) y
  por `markPrice` para el USD — confirmado dando un notional plausible
  (~$812M en BTC).
- **MEXC Futures**: tres llamadas bulk (`/contract/detail` para metadata +
  `contractSize`, `/contract/funding_rate` para tasa + intervalo real por
  símbolo, `/contract/ticker` para el open interest en contratos). A
  diferencia del resto de CEX del proyecto (que asumen 8h fijo porque ccxt
  no siempre expone el intervalo real), MEXC sí lo da por contrato
  (`collectCycle`) y de verdad varía — confirmado en vivo: 8h para BTC_USDT,
  4h para XAU_USDT. Además, MEXC mezcla cripto con activos RWA
  (`XAU_USDT`, `GOOGLSTOCK_USDT`, `NVIDIA_USDT`...) en el mismo listado —
  a diferencia del bug real de ApeX (que mezclaba cripto con mercados de
  predicción que SÍ rompían con 403), aquí los RWA de MEXC responden con
  datos de funding reales y válidos, así que se dejan sin filtrar a
  propósito: son oportunidades legítimas más, coherente con que este
  proyecto ya cubre "cripto + RWA" por diseño.
- **HTX**: cuatro llamadas bulk (`swap_contract_info` para metadata +
  intervalo real por contrato vía `settlement_period`, `swap_batch_funding_rate`
  para la tasa, `swap_open_interest` para el open interest, y
  `/market/detail/batch_merged` como proxy de mark price vía `close`, ya que
  no existe ningún endpoint de HTX con un campo de "mark price" dedicado
  para swaps — se probó `/swap_batch_mark_price`, que no existe, 404 real).
  Caso curioso confirmado en vivo: a diferencia de KuCoin/MEXC (donde el open
  interest viene en Nº de contratos y hay que convertirlo a mano), el campo
  `value` de `swap_open_interest` en HTX YA viene en USD directamente — se
  usa tal cual, sin multiplicar por `contract_size` ni por precio. También
  se confirmó una inconsistencia real de la propia API: tres de los cuatro
  endpoints envuelven su lista bajo `"data"`, pero el de ticker
  (`batch_merged`) la envuelve bajo `"ticks"` en su lugar — el conector lo
  maneja explícitamente en vez de asumir el mismo nombre en los cuatro.

Los tres conectores se probaron con payloads sintéticos (mock de
`requests.Session.get`) que reproducen la forma real vista en vivo, incluyendo
casos borde: un mercado no operable por exchange (debe descartarse sin tirar
el resto) y, para MEXC, un símbolo RWA junto al cripto (debe conservarse).

**Pendiente de confirmar contra el primer despliegue real** (mismo criterio
que el resto de conectores nuevos de este proyecto): si al desplegar alguno
de los tres da un APR o un Open Interest claramente disparatado, revisar
primero los puntos marcados arriba como "confirmado en vivo, un solo valor
observado" (el campo `status`/`contract_status`/`apiAllowed` de cada uno,
visto con un único ejemplo cada vez).

### Price Spread, séptima tanda (comparativa con Kusi/Smartbitrage, punto 3)

A raíz de comparar este proyecto contra los requisitos de otras herramientas
del mismo tipo (Kusi, Smartbitrage, Usenami), salió un hueco real: el `mark_price`
de cada pierna ya se capturaba y llegaba hasta `OpportunityRow.long_mark_price`/
`short_mark_price`, pero en ningún sitio se calculaba la diferencia entre
ambos ni se mostraba. Es un dato de riesgo importante y distinto del Spread
APR: el Spread APR es el beneficio recurrente (se cobra cada intervalo de
funding), mientras que el Price Spread es un coste que se paga una sola vez,
al entrar en la operación (y otra vez al salir, si los precios no han vuelto
a converger) — un spread de funding enorme no sirve de nada si hace falta
comprar la pierna long bastante más cara que donde se vende la pierna short.

Implementación:

- **`core/scoring.py::price_spread()`** (nueva función, mismo patrón que
  `oi_depth()`: toma las dos piernas como `NormalizedRate` y devuelve un
  dataclass, aquí `PriceSpread`). Fórmula: `abs(short_price - long_price) /
  long_price * 100` — siempre en valor absoluto a propósito, porque lo que
  importa es CUÁNTO cuesta entrar, no qué lado está más caro (eso ya lo dice
  qué exchange es long/short). Devuelve `None` (no inventa un 0) cuando a
  cualquiera de las dos piernas le falta el `mark_price`, o cuando el precio
  de la pierna long es exactamente 0 (evita división por cero).
- **`core/opportunities.py`**: nuevo campo `OpportunityRow.price_spread_pct`,
  calculado en `compute_opportunities()` junto a `oi_depth()`.
- **`pages/1_Funding_Rates.py`**: nueva columna "Price Spread" en la tabla de
  Ranking (entre Spread APR y Consistency), pre-formateada como texto vía un
  nuevo helper `_fmt_pct()` — mismo motivo que `_fmt_usd()` con las columnas
  de OI: pasar el valor crudo con `column_config.NumberColumn` enseña el
  texto literal "None" para las filas sin dato. Caption actualizado
  explicando la diferencia entre Price Spread (coste único) y Spread APR
  (beneficio recurrente).
- **`cli.py`**: misma columna añadida a la tabla de terminal (Rich), con
  color de aviso (rojo ≥1%, ámbar ≥0.3%, tenue si es bajo) para que salte a
  la vista cuando el coste de entrada empieza a comerse una porción grande
  del Spread APR — umbrales elegidos a criterio propio (no vienen de ningún
  dato de mercado ni de Kusi/Smartbitrage), fácil de ajustar si en la
  práctica se ven demasiados falsos avisos.

Probado con datos sintéticos (`price_spread()` con precios normales, con un
`mark_price=None` en una pierna, y con `mark_price=0` en la pierna long para
confirmar que no revienta por división por cero; y `compute_opportunities()`
de punta a punta comprobando que el campo llega bien a `OpportunityRow`).

No se ha tocado la lógica de ranking ni se ha añadido ningún filtro
automático (a diferencia de `has_dead_liquidity()` con el OI en $0): de
momento Price Spread es solo informativo, una columna más a mirar. Si en la
práctica conviene descartar o avisar más fuerte cuando el Price Spread sale
muy alto, es un cambio pequeño a partir de aquí — pero no se ha hecho porque
no se pidió.

### Volume 24h, octava tanda (comparativa con Kusi/Smartbitrage, punto 2)

Segunda mitad del Punto 2 de la comparativa (la primera, Open Interest, ya
estaba resuelta desde el Paso 5). El Open Interest dice cuánto hay abierto
AHORA MISMO en cada pierna; el volumen de 24h dice cuánto se ha estado
MOVIENDO — un mercado puede tener buen OI pero estar prácticamente
congelado, lo que en la práctica significa más slippage del que el OI por sí
solo sugeriría. Son señales complementarias, por eso se muestran las dos por
separado (ver `core/scoring.py::volume_depth()`, mismo patrón que
`oi_depth()`).

A diferencia de OI Depth (que deliberadamente solo se pide para el top 10
del ranking, con una llamada aparte, para no reventar el rate limit — ver
Paso 5), aquí se decidió ir a por la opción completa: volumen para TODAS las
oportunidades, no solo el top 10, leyendo el campo directamente del mismo
fetch masivo que ya se hace (o, como mucho, una única llamada bulk
adicional) en cada uno de los 23 conectores. Esto significa tocar cada
conector individualmente en vez de un mecanismo genérico — más trabajo, pero
disponible para todo el ranking desde el primer render, no solo tras
enriquecer el top 10.

**Nuevo campo de punta a punta**: `FundingRate.volume_24h_usd` →
`NormalizedRate.volume_24h_usd` → `OpportunityRow.volume_long_usd` /
`volume_short_usd` / `volume_bottleneck_usd` / `volume_bottleneck_side`.
Nuevas columnas "Vol 24h long/short" y "Cuello de botella Vol" en la tabla
de Ranking (Streamlit y CLI), junto a las de OI. Regla seguida en los 23
conectores, sin excepción: si el campo no viene ya en USD/USDT/USDC en el
mismo payload que el conector ya consulta, se documenta el hueco y se deja
`None` — nunca se añadió una llamada símbolo a símbolo solo para conseguir
volumen (eso sí se acepta, en cambio, para OI Depth, pero ahí el propio
diseño ya lo limita al top 10).

**CEX vía ccxt (Binance, Bybit, OKX, Bitget, Gate, Aster)**: una segunda
llamada bulk `fetch_tickers()` (unificada por ccxt, todos los símbolos de
golpe), leyendo `quoteVolume` — campo estándar de ccxt, no hace falta
investigar por exchange como con las APIs propias. Si `fetch_tickers()`
falla para algún exchange, se captura y se sigue sin volumen para ese
exchange (no tira el fetch principal, que es el dato crítico).

**KuCoin, MEXC, HTX (conector propio)**: los tres ya traían el volumen de
24h en el mismo endpoint que se usa para todo lo demás, sin llamada extra:
KuCoin `turnoverOf24h` (confirmado en vivo, ej. XBTUSDTM ≈$370.1M, ya en
USDT), MEXC `amount24` (confirmado en vivo por consistencia interna:
`volume24 × contractSize × fairPrice ≈ amount24`, ambos ≈$4.25-4.27B para
BTC_USDT), HTX `trade_turnover` (confirmado en vivo, mismo ticker que ya se
usa para el mark price proxy, ej. BTC-USDT ≈$627.2M).

**DEX — campo confirmado en vivo, usado directo (ya en USD/quote)**:
Lighter (`daily_quote_token_volume`, BTC ≈$880.3M), Extended
(`marketStats.dailyVolume`, XRP ≈$35.1M), Pacifica (`volume_24h`, BTC
≈$371.7M — se trata como ya-en-USD porque como unidades de BTC no tendría
sentido de magnitud), Variational (`volume_24h` en `/metadata/stats`, BTC
≈$363.5M, mismo razonamiento de magnitud que ya se aplica a su
open_interest), Nado (`quote_volume` del mismo objeto que ya se usaba, BTC
≈$230.9M en USDT0), Backpack (`quoteVolume`, nuevo endpoint bulk
`GET /api/v1/tickers` — es el único de este grupo que sí necesitó una
llamada extra, porque ninguno de los tres endpoints que ya usaba traía
volumen; BTC_USDC_PERP ≈$197.3M), ApeX (`turnover24h`, mismo ticker por
símbolo que ya se pedía — ojo, ver nota de fiabilidad más abajo).

**DEX — campo encontrado pero solo confirmado contra documentación, no
contra una respuesta JSON real** (incidencia común a los tres: el endpoint
en cuestión es POST-only o no se pudo aislar un ejemplo completo vía
WebFetch — se deja igualmente activado porque el nombre/unidad del campo sí
está documentado oficialmente, pero es el primer sitio a mirar si algún
volumen sale con una magnitud rara en producción):
- Hyperliquid: `dayNtlVlm` (documentado como "24-hour notional volume in
  USD"; su endpoint es POST-only, WebFetch no pudo repetir la llamada en
  vivo símbolo a símbolo como con el resto de campos de este conector).
- GRVT: `buy_volume_24h_q + sell_volume_24h_q`, dividido por `PRICE_SCALE`
  (1e9) — mismo patrón de escala que ya usa `open_interest` en este
  conector. El endpoint real da 405 (POST-only); el JSON de ejemplo de la
  documentación oficial es un placeholder, así que la escala ÷1e9 es una
  asunción razonada, no una cifra contrastada.
- RiseX: `quote_volume_24h`, confirmado solo contra el schema OpenAPI
  oficial — `api.rise.trade` dio 403 tanto por curl como por WebFetch. Dado
  el historial de este conector (dos rondas de bugs reales donde la
  documentación no coincidía con la respuesta real, ver quinta tanda), este
  campo concreto es el primer sospechoso si el volumen de RiseX sale raro.
- Paradex: `volume_24h` existe, pero no se pudo confirmar si ya viene en USD
  o en el activo base (el único ejemplo completo en vivo fue SUI, no BTC).
  Se aplicó el mismo criterio que ya usa `open_interest` en este conector
  (tratar como activo base, multiplicar por mark_price) por consistencia,
  dejando la incertidumbre documentada explícitamente en el docstring.

**DEX — hueco conocido, sin volumen disponible sin romper el patrón "bulk,
sin llamadas por símbolo"** (`volume_24h_usd=None`, documentado igual que ya
se documenta la falta de mark_price dedicado en HTX):
- edgeX: ni `getLatestFundingRate` ni `getMetaData` (los dos endpoints que
  usa el conector) traen volumen; el único endpoint que sí lo tiene
  (`getTicker`) es el mismo que este conector ya había descartado antes por
  devolver siempre `"data": []` — reconfirmado hoy, sigue vacío.
- Hibachi: verificado por dos vías independientes (SDK oficial `hibachi-xyz`
  y el módulo `hibachi` de ccxt) que el único campo de volumen sale de un
  endpoint por símbolo, no bulk — se respeta la regla del proyecto de no
  añadir peticiones símbolo a símbolo solo para esto.
- Vertex: sigue totalmente inalcanzable desde este entorno (mismo bloqueo de
  red que el resto de datos de Vertex, ver quinta tanda). Su documentación
  solo describe un volumen ACUMULADO desde el origen del producto, no una
  ventana de 24h — calcular una ventana real exigiría pedir dos snapshots y
  restar, un cambio no verificable sin acceso real, así que se dejó el hueco
  en vez de inventar el cálculo.

**Nota de fiabilidad sobre ApeX** (`turnover24h`): al investigar, WebFetch
devolvió el mismo objeto para tres símbolos distintos pedidos por separado
(`BTCUSDT`, `DOGEUSDT`, `ETHUSDT`) — probablemente una caché de WebFetch que
ignora la query string, no algo confirmable de otra forma sin acceso directo
al host (bloqueado en este sandbox). El payload es internamente consistente
(`turnover24h / volume24h` cae dentro del rango `[lowPrice24h, highPrice24h]`
del mismo objeto) y reutiliza campos ya confirmados en vivo en tandas
anteriores (`fundingRate`, `markPrice`, `openInterest`), así que se aceptó
como confirmación suficiente del nombre/unidad del campo — pero el valor
exacto para un símbolo concreto no quedó verificado en ese instante.

Probado: `core/scoring.py::volume_depth()` con datos sintéticos (caso
normal, una pierna sin dato → bottleneck `None` sin inventar), y
`compute_opportunities()` de punta a punta. Además, pipeline completo
verificado en modo Demo/offline (`core/data_service.py::fetch_normalized_rates(offline=True)`)
con volumen añadido a los tres fixtures existentes
(`tests/fixtures/*_funding.json`), confirmando que el campo llega intacto
hasta `OpportunityRow` sin tocar ningún conector en vivo.

**Pendiente de confirmar contra el primer despliegue real**, mismo criterio
que el resto de datos de este proyecto: los campos marcados arriba como
"solo contra documentación" o con nota de fiabilidad (Hyperliquid, GRVT,
RiseX, Paradex, ApeX) son los primeros a revisar si algún volumen sale con
una magnitud claramente disparatada en producción.

**[BUG REAL encontrado en producción, corregido]** Al confirmar Volumen 24h
en el primer despliegue, el propio volumen quedó bien (columnas pobladas con
datos reales — ver ejemplo 1000000MOG: apex con $7.6M de volumen, un número
claramente distinto de su OI, así que no era una copia accidental del OI).
El bug estaba en cómo se pintaban las columnas: "OI long/short", "Cuello de
botella OI/Vol" y "Vol 24h long/short" se pre-formateaban como texto
(`_fmt_usd()`, ej. `"$9.4M"`) antes de meterlas en el `DataFrame`, por una
suposición de que pasarlas como número crudo a `column_config.NumberColumn`
enseñaría el texto literal `"None"` para los valores nulos. Al pulsar la
cabecera de una de esas columnas para ordenar, Streamlit las ordenaba como
TEXTO (alfabéticamente), no como número — así `"$9.4M"` salía antes que
`"$898,647"` porque `'9'` es mayor que `'8'` comparando caracter a caracter,
aunque 898.647 < 9.400.000 numéricamente. Confirmado con un test directo de
pandas/pyarrow que la suposición original era incorrecta: una columna con
mezcla de `float` y `None` se convierte a `float64` con `NaN`, no a texto, y
`column_config.NumberColumn` ya pinta esos `NaN` en blanco sin necesidad de
pre-formatear nada. **Arreglado**: las seis columnas pasan ahora el valor
crudo (`float | None`) con `column_config.NumberColumn(format="compact")`
(ordenan bien y se siguen viendo como `$1.2M` gracias al formato nativo de
Streamlit), y el "lado" del cuello de botella (antes un sufijo de texto
`" (long)"` pegado al número, lo que ya de por sí impedía tratarlo como
número) se separó en dos columnas propias, "Lado OI" y "Lado Vol" — así cada
columna es o 100% numérica o 100% texto, nunca una mezcla que rompa el
ordenado. `_fmt_usd()` se mantiene solo para los paneles de diagnóstico
(`st.json`), que no son tablas ordenables.

**[BUG REAL encontrado en producción, corregido] Escala de OI/Volumen de
GRVT (÷1e9 aplicado donde no tocaba)** — Un usuario reportó, con capturas de
un despliegue real, tres cosas a la vez en filas donde GRVT era una de las
dos piernas: (1) Price Spread de ~100.000.000.000% ("una bestialidad, no
creo que sea cierto"), y (2)(3) OI/Volumen con decenas de decimales para
valores prácticamente cero (ej. `0,00000000000079` en el OI de KPEPE). Causa
raíz: el conector de GRVT (`connectors/dex_grvt.py`) aplicaba el mismo
`PRICE_SCALE = 1e9` (el divisor correcto para `mark_price`) a
`open_interest` y a `buy/sell_volume_24h_q` — el propio docstring del módulo
ya llevaba desde una sesión anterior marcando esto como una asunción sin
confirmar. Se releyó hoy, campo por campo (no el JSON de ejemplo de la
página, ya antes demostrado no fiable numéricamente), la documentación
oficial en vivo de GRVT
(https://api-docs.grvt.io/schemas/api_ticker_response/ y
`api_get_all_instruments_response/`): los precios (`mark_price`,
`index_price`, etc.) SÍ están "expressed in `9` decimals" de forma uniforme
para todos los instrumentos — esa parte estaba bien — pero `open_interest`
está "expressed in **base asset decimal units**" y
`buy_volume_24h_q`/`sell_volume_24h_q` en "**quote asset decimal units**":
escalas que dependen de los campos `base_decimals`/`quote_decimals` que
`all_instruments` ya trae POR INSTRUMENTO, no de `PRICE_SCALE`. BTC/ETH
tienen `base_decimals = 9` (según el propio SDK oficial de GRVT), así que
coincidían con `PRICE_SCALE` por pura casualidad y el bug quedó invisible
hasta que aparecieron altcoins de precio bajo como KPEPE en el ranking, con
un `base_decimals` bien distinto de 9. **Arreglado**: `base_decimals` y
`quote_decimals` se leen del mismo `all_instruments` que ya se pedía (sin
llamada de red extra) y se usan para escalar OI/Volumen; si un instrumento
no los trae, su OI/Volumen se deja en `None` en vez de adivinar un divisor.
`mark_price` no cambia (su escala uniforme ya estaba confirmada y sigue
correcta). Cubierto por tests sintéticos con datos de ticker mockeados
(BTC con `base_decimals=9`, coincide con el comportamiento anterior; un caso
tipo-KPEPE con `base_decimals=0`, demuestra que con el bug viejo el OI salía
~1.000.000 de veces más pequeño que el real; y un caso sin
`base_decimals`/`quote_decimals`, confirma que se deja en `None` en vez de
inventar). Además, de paso, la misma pasada de documentación reveló que
`funding_rate_8h_curr`/`funding_rate_8h_avg` (los campos que este conector
lee para el funding rate) están marcados **DEPRECATED** en el esquema
actual de GRVT, con un campo nuevo `funding_rate` (misma unidad,
centibeeps) reemplazándolos — se cambió el conector para probar el nuevo
primero y caer a los antiguos como compatibilidad, para no quedarnos sin
datos de golpe el día que GRVT retire los deprecated.

Sobre el Price Spread disparatado en sí: como la escala de `mark_price`
salió confirmada correcta (no es lo que causaba el ~100.000.000.000%), no
se tocó esa parte del cálculo. Como red de seguridad — este mismo conector
ya había demostrado una vez que la documentación de GRVT puede no coincidir
con la realidad en vivo — se añadió una guardia de cordura en
`core/scoring.py::price_spread()`: por encima de `IMPLAUSIBLE_SPREAD_PCT`
(1000%, ya inalcanzable entre precios reales del mismo activo en dos
exchanges) el resultado se descarta a `None` ("—" en la interfaz) en vez de
enseñar un número que ni el propio proyecto se cree, en vez de intentar
"corregir" un valor sin evidencia real que lo respalde. Aplica a nivel de
`core/scoring.py`, no solo a GRVT, así que protege contra el mismo tipo de
fallo si aparece en cualquier otro conector en el futuro.

**Actualización — el fix de arriba NO resolvió el problema en producción.**
Tras desplegar el cambio de `base_decimals`/`quote_decimals`, el usuario
confirmó (descargó el zip, lo desplegó, reinició la app dos veces) que el OI
y el volumen de GRVT seguían saliendo con la misma magnitud cercana a cero.
Eso descarta la hipótesis de "está desplegando código viejo" y apunta a que
la asunción tomada de la documentación oficial — que `all_instruments`
expone `base_decimals`/`quote_decimals` con esos nombres exactos — puede no
coincidir con lo que la API responde de verdad en producción, exactamente
el mismo tipo de discrepancia que ya pasó una vez con
`funding_rate_curr`/`funding_rate_8h_curr` en este mismo conector. Como
este entorno de desarrollo no tiene salida de red hacia GRVT (ni siquiera
`all_instruments`, que si es alcanzable desde el despliegue real), no hay
forma de confirmar esto desde aquí sin datos reales.

**Se añadió diagnóstico en vez de seguir adivinando** (`connectors/dex_grvt.py`,
mismo patrón que ya funcionó para encontrar el bug de `funding_rate_curr`):
dos `logger.warning(...)` que en el próximo despliegue van a escribir en los
logs de Streamlit Cloud ("Manage app" → Logs):

  1. Una muestra cruda (JSON tal cual, sin filtrar por nombre de campo) de
     hasta 6 filas de `all_instruments` — para ver si `base_decimals`/
     `quote_decimals` existen de verdad, y con qué nombre, en la respuesta
     real.
  2. Para hasta 6 instrumentos cuyo OI/volumen calculado sigue saliendo por
     debajo de $1 (o que no encontraron `base_decimals`/`quote_decimals`),
     los valores CRUDOS de `mark_price`, `open_interest`,
     `buy_volume_24h_q`/`sell_volume_24h_q` antes de aplicar ninguna
     escala — para poder hacer la cuenta a mano contra la interfaz oficial
     de GRVT y averiguar la fórmula real, en vez de seguir basándonos en un
     texto de documentación que este mismo conector ya demostró no fiable
     una vez.

Pendiente: que el usuario redespliegue una vez más y pegue las líneas de
log que contengan "grvt DIAGNÓSTICO" — con eso se podrá confirmar (o
descartar) la hipótesis de `base_decimals`/`quote_decimals` con datos
reales, en vez de seguir iterando a ciegas.

**Actualización final — CONFIRMADO con el log real, y era otra cosa
completamente distinta.** El usuario pegó el log de producción pedido
arriba. Los datos en vivo (7 instrumentos reales de GRVT: AAOI, AAVE, AMAT,
ARB, AMZN, AAPL, ADA) fueron inequívocos y consistentes entre sí:
`mark_price`, `open_interest`, `buy_volume_24h_q` y `sell_volume_24h_q` NO
vienen como enteros de punto fijo — vienen como el número decimal humano
DIRECTO, ya en su unidad final. Ejemplos reales del log:
`"mark_price": "95.586640085"` para AAOI (una acción que de verdad cotiza
sobre los $95), `"mark_price": "0.150444379"` para ARB (~$0.15, el precio
real de ARB), `"open_interest": "2287449.6"` para ARB (una cantidad de
tokens perfectamente plausible, no un entero gigante). Ninguna de las dos
rondas anteriores de este bug (÷1e9 primero, `base_decimals`/
`quote_decimals` después) tenía razón — ambas dividían un número que ya
estaba bien, encima de más, hasta dejarlo cerca de cero.

**Corrección final**: se quitó TODA la división para estos tres campos —
`mark_price = float(mark_price_raw)`, `oi_usd = float(open_interest_raw) *
mark_price`, `volume_24h_usd = float(buy_volume_24h_q) +
float(sell_volume_24h_q)`. `PRICE_SCALE`/`base_decimals`/`quote_decimals`
ya no se usan para nada de esto (se documenta toda la historia en el
docstring de `connectors/dex_grvt.py`, sección "BUG REAL, tercera vuelta").
Verificado con un test que usa los valores EXACTOS del log real (AAOI, ARB)
en vez de datos inventados — con cualquiera de los dos fixes anteriores,
esos mismos números habrían seguido dando OI/volumen cercanos a cero.

**Efecto colateral positivo, sin tocar código**: al arreglar el precio, el
Price Spread de GRVT también debería volver a valores sensatos — ya no
dependía de OI, pero si algo aguas abajo llegaba a depender de un
mark_price mal escalado en otra ronda de este mismo bug, esto también lo
arregla. La guardia de cordura (`IMPLAUSIBLE_SPREAD_PCT`) se mantiene como
red de seguridad permanente, no solo para este caso.

**Actualización (2026-09-18) — CONFIRMADO y corregido**: la pierna de GRVT
mostraba sistemáticamente APR ~0.0% (`funding_rate_8h_curr` ×
`CENTIBEEPS_TO_DECIMAL`, ÷1e6) — el mismo patrón de "posible sobre-escalado"
que ya se vio tres veces con otros campos. Se dejó el valor crudo en el
diagnóstico (sin tocar la fórmula a ciegas, por el riesgo asimétrico de
pasar de "demasiado pequeño" a "un millón de veces demasiado grande") hasta
tener evidencia real. Confirmado con **tres despliegues independientes en
producción**, guardando `funding_rate_8h_curr` crudo para una muestra de
instrumentos:

    AAVE_USDT_Perp:       raw="0.01"   (visto 2 veces)
    ADA_USDT_Perp:        raw="0.01"   (visto 2 veces)
    ANTHROPIC_USDT_Perp:  raw="0.005"  (visto 2 veces)
    ARB_USDT_Perp:        raw="0.01"
    AMAT_USDT_Perp:       raw="0.013"
    AMZN_USDT_Perp:       raw="0.0129"
    AAOI_USDT_Perp / AAPL_USDT_Perp / AI16Z / AMD / ARM: raw="0.0"

Con `× CENTIBEEPS_TO_DECIMAL` (1e-6), AAVE (raw=0.01) da una fracción de
1e-8 → APR≈0.001%, indistinguible de cero — exactamente el "+0.0%" que se
veía en el ranking. La magnitud real (0.005 a 0.013) encaja con un % humano
directo del periodo de 8h, el mismo patrón que ya tuvieron mark_price/
open_interest/volumen de GRVT. **Fix aplicado**: `funding_rate =
float(rate_raw) / 100.0` en vez de `× CENTIBEEPS_TO_DECIMAL` (que se deja
definida pero sin uso, mismo criterio que `PRICE_SCALE`). Con esto: AAVE/
ADA/ARB → 10.95% APR, AMAT → 14.235%, AMZN → 14.1255%, ANTHROPIC → 5.475% —
cifras de funding normales, y los raw="0.0" (AAOI, AAPL, AI16Z, AMD, ARM)
siguen dando exactamente 0%. El diagnóstico `funding_rate_raw`/
`funding_rate_calculado` se mantiene activo por si esta interpretación
también resultara estar mal. Verificado con un test que usa los valores
EXACTOS de los tres despliegues de arriba (`connectors/dex_grvt.py` +
`core/normalize.py`).

**Confirmado en producción (despliegue 2026-09-19 00:14 UTC)**: el mismo
diagnóstico, ya con el fix desplegado, muestra `funding_rate_calculado`
como `raw / 100` en vez de `raw × 1e-6` — ANTHROPIC raw="0.005" →
calculado=5e-05 (antes 5e-09), AMD raw="0.0134" → calculado=0.000134
(antes habría sido 1.34e-08), ARB raw="0.01" → calculado=0.0001 (antes
1e-08). En APR: ANTHROPIC≈5.475%, AMD≈14.673%, ARB≈10.95% — coincide
exactamente con lo previsto arriba. El "+0.0%" sistemático de GRVT queda
resuelto.

**Confirmado por el usuario en producción**: "La parte de GRVT parece
arreglada" — el fix de arriba (quitar toda la división de mark_price/OI/
volumen) queda confirmado con datos reales, no solo con el test.

**Nuevo hallazgo, el mismo mensaje del usuario — Price Spread alto en pares
que NO tocan GRVT.** "Pero aun hay más activos con un 100% de Price spread"
— capturas de producción muestran Price Spread de entre ~1% y ~240% en
pares como CAT (bitget/mexc), RTX (gate/aster), HK50 (mexc/gate), PURR
(extended/hyperliquid), SIREN (extended/mexc), CAKE (kucoinfutures/extended),
BERA (hyperliquid/extended), APEX (lighter/extended), NEIRO (aster/apex) —
casi ninguno con GRVT de por medio, así que no es una repetición del mismo
bug que se acaba de arreglar.

Dos hipótesis sobre la mesa, ninguna todavía confirmada con datos reales:

  1. **Divergencia de precio genuina.** El propio docstring de
     `price_spread()` (`core/scoring.py`) ya avisa de que esto "puede
     dispararse en símbolos poco líquidos... donde cada exchange puede
     llevar su propio índice de precio" — sería la métrica haciendo
     exactamente lo que se diseñó para hacer.
  2. **Choque de símbolos.** El "Símbolo" que se ve en la tabla de Ranking
     es un ticker corto ya normalizado (`core/normalize.py`, y en el caso de
     los CEX vía ccxt, `base = entry["symbol"].split("/")[0]` en
     `connectors/cex_ccxt.py`) — si dos exchanges usan un nombre de contrato
     distinto para lo que en realidad es el mismo activo (ej. un exchange
     antepone "1000" al ticker de un memecoin de precio muy bajo y el otro
     no), o si directamente dos activos DISTINTOS comparten por casualidad
     el mismo ticker corto (más probable en tickers de 3-4 letras como
     "CAT"/"RTX"/"HK50"), `compute_opportunities()` los empareja como si
     fueran el mismo mercado y el "Price Spread" que sale no es un coste de
     entrada real — es comparar dos cosas distintas.

No se ha tocado código de cálculo todavía porque, igual que con los tres
intentos fallidos del bug de GRVT, adivinar la causa sin verla en datos
reales ya ha salido mal dos veces en este mismo proyecto. En vez de eso se
añadió una herramienta de diagnóstico permanente, no un log de usar y tirar:
un nuevo expander en `pages/1_Funding_Rates.py`, "Diagnóstico: Price Spread
más alto", que enseña — para las 20 oportunidades con el Price Spread más
alto — el **símbolo real (`raw_symbol`) y el `mark_price`** de cada pierna,
no solo el ticker corto normalizado de la tabla de Ranking. Con eso se puede
ver a ojo, sin adivinar:

  - Si `long_raw_symbol`/`short_raw_symbol` resultan ser el mismo contrato
    con nombres distintos (ej. `"CATUSDT"` vs `"1000CATUSDT"`) → es un
    choque de símbolos al normalizar (hipótesis 2), y el fix sería en
    `core/normalize.py`/los conectores CEX, no en `price_spread()`.
  - Si de verdad es el mismo contrato en ambos exchanges y aun así el
    `mark_price` difiere tanto → es divergencia real (hipótesis 1), y no
    hace falta ningún fix — la interfaz está avisando de un riesgo real de
    entrada, tal como se diseñó.

Pendiente: que el usuario redespliegue, abra ese expander para los símbolos
que vio con Price Spread alto y pegue lo que salga (es JSON, se puede copiar
directo) — con eso se decide entre las dos hipótesis con datos reales antes
de tocar ningún cálculo.

**Actualización — confirmado con datos reales, y es un choque de símbolos,
no divergencia de precio.** El usuario pegó el panel de diagnóstico con
`raw_symbol`/`mark_price` reales de las 20 oportunidades con Price Spread
más alto. Los datos fueron inequívocos:

  - **"CAT"**: Caterpillar Inc. tokenizada en bitget (`raw=CAT/USDT:USDT`,
    `mark_price=$785.85`, el precio real de la acción) frente a un
    memecoin sin ninguna relación también llamado "CAT" en mexc
    (`raw=CAT_USDT`, `mark_price=$0.000001946`) — más de 400 millones de
    veces más barato. Dos activos completamente distintos, mismo ticker.
  - **"RTX"**: Raytheon Technologies en gate (`$197.64`, precio real de la
    acción) frente a otra cosa sin relación en aster (`$0.71`).
  - **"HK50"**: el índice Hang Seng — mexc lo cotiza en `24638.7` (su nivel
    real de mercado en esa fecha) mientras que gate lo cotiza en `3143.0`.
  - **"XIAOMI"** (aster `$3.39` vs extended `$26.10`, ratio ~7.7x) y
    **"PURR"** (extended `$11.10` vs hyperliquid `$0.10`, ratio ~110x)
    entran en el mismo patrón, con algo menos de certeza sobre cuál es el
    activo "real" en cada caso, pero con ratios igual de imposibles para
    ser el mismo activo.

Frente a esto, los casos con Price Spread moderado (**"CAKE"**: mexc
`$2.18` vs extended `$1.30`, ratio 1.68x; **"BERA"**: lighter `$0.18` vs
extended `$0.25`, ratio 1.38x; **"APEX"**: ratio 1.21x; **"NEIRO"**: ratio
1.06x) tienen `raw_symbol`/`mark_price` del mismo orden de magnitud en las
dos piernas — consistente con ser de verdad el mismo activo con una
divergencia de precio real en un mercado poco líquido (justo lo que la
métrica está pensada para avisar).

**Fix**: como toda la fila (no solo el Price Spread — también el Spread
APR, que es el número principal del ranking) es basura cuando dos piernas
son activos sin relación, no basta con ocultar una columna. Se añadió
`has_implausible_price_pair()` en `core/opportunities.py` — mismo patrón
que `has_dead_liquidity()` (una función que solo pregunta "¿se descarta?",
y es la página Streamlit quien filtra y enseña por qué, sin tirar datos en
silencio) — con un umbral `IMPLAUSIBLE_PRICE_PAIR_PCT = 75.0` elegido
directamente de estos datos reales: todo lo confirmado como choque de
símbolos salió ≥ 87% (HK50, el caso más bajo); todo lo que parece
divergencia real salió ≤ 41% (CAKE, el caso más alto). 75% deja margen de
sobra a los dos lados. Se aplica en `pages/1_Funding_Rates.py` ANTES de
pedir OI Depth (top N), para no gastar esas llamadas en oportunidades que
ya son basura de raíz, con un aviso + panel de diagnóstico nuevo (mismo
estilo que el de OI $0) que enseña qué se descartó y por qué. Verificado
con un test que usa los valores EXACTOS del panel real (CAT, RTX, HK50,
XIAOMI, PURR se descartan; CAKE, BERA, APEX, NEIRO se conservan).

**Hallazgo secundario — RESUELTO (2026-09-19)**: el conector de Lighter
(`connectors/dex_lighter.py`) guardaba `raw_symbol=str(market_id)` — un ID
numérico interno ("20", "86"...) en vez de un ticker legible. No causaba
el bug de arriba (el campo `symbol`, que es el que se usa para emparejar
oportunidades, sí venía correcto — "BERA", "APEX"), pero hacía que
cualquier panel de diagnóstico que enseñe `raw_symbol` mostrara cosas como
`raw=20` para Lighter, sin decir nada por sí solo. Confirmado en vivo
(WebFetch a `/funding-rates` y `/orderBookDetails`) que ambos endpoints ya
traen, cada uno por su lado, un campo `symbol` con el ticker legible (ej.
"APT", "XLM", "POPMART") y que coincide exactamente entre los dos para el
mismo `market_id` — no hay un identificador "más crudo" por debajo de ese
ticker. Cambiado a `raw_symbol=symbol`, mismo patrón que ya usa
Hyperliquid cuando tampoco hay nada más nativo que el propio ticker. Tests
de regresión con valores reales confirmados en vivo
(`test_lighter_raw_symbol.py`). De paso se vio en `orderBookDetails` un
campo `status` ("active"/"inactive") que este conector no usa todavía —
no se tocó (fuera de alcance de este arreglo), pero queda anotado por si
algún mercado "inactive" se está colando en el ranking como si operara
con normalidad, mismo patrón que el bug real de `status` en Extended (ver
más abajo).

**Actualización — nuevo bug real, distinto: mercados fantasma en Extended
(no relacionado con el choque de símbolos de arriba).** Con el fix
anterior ya desplegado, el usuario preguntó "¿qué ha pasado con APEX?" y
dijo "he buscado CAKE en Extended y no sale", mirando la tabla de Ranking
(CAKE, BERA y APEX seguían arriba del todo — ya no por choque de símbolos,
esos se descartaron bien, sino por otra cosa). Revisando los datos que ya
traía la tabla: las tres tenían la pierna de **Extended** con un Open
Interest mínimo pero NO exactamente $0 (CAKE: $41, BERA: $64, APEX: $810 —
por eso `has_dead_liquidity()` no las pillaba, exige el $0 exacto) y **Vol
24h exactamente $0**. El usuario confirmó contra la interfaz REAL de
Extended que "CAKE" ni siquiera aparece listado ahí — no es un mercado
real y operable, es el mismo patrón "fantasma" ya conocido de Aster/STORJ
(ver más arriba en este README), solo que aquí lo delata el volumen en vez
del OI, y en un exchange distinto.

**Fix**: se añadió `has_zero_volume_leg()` en `core/opportunities.py`,
mismo patrón exacto que `has_dead_liquidity()` — descarta cualquier
oportunidad con Vol 24h CONFIRMADO en $0 en una de las dos piernas (no
`None`, que es "no se pudo consultar" y se deja tal cual). A diferencia
del OI Depth, el volumen de 24h de Extended ya viene directo en el fetch
masivo (no hace falta ninguna llamada aparte), así que se aplica a TODAS
las oportunidades sin coste extra, con su propio aviso + panel de
diagnóstico en `pages/1_Funding_Rates.py` (mismo estilo que el de OI $0).
Verificado con un test que usa los valores EXACTOS que vio el usuario
(CAKE/BERA/APEX se descartan; `None` sin consultar y volumen real en
ambas piernas NO se descartan).

Esto es un parche corriente abajo, no la causa raíz: se añadió un
diagnóstico (`logger.warning` en `connectors/dex_extended.py`) que vuelca
la fila CRUDA completa (todos los campos) para hasta 6 mercados con Vol
24h calculado = $0, para confirmar con datos reales si hay un campo de
estado que se esté ignorando y así poder filtrar en el origen, igual que
ya se hace con Aster — pendiente de un despliegue más.

**Actualización — el parche de arriba (Vol 24h == $0) estaba MAL, no solo
incompleto.** El usuario redesplegó y pegó el log con el diagnóstico ya en
marcha. La fila cruda de dos mercados reales lo dejó clarísimo:

```
{"name": "INTU-USD", "category": "RWA", "subCategory": "Equity",
 "active": true, "status": "ACTIVE", "isOffHours": true,
 "tradingHours": "NO_OVERNIGHT", "marketStats": {"dailyVolume": "0.000000", ...}}

{"name": "NOW_24_5-USD", "category": "RWA", "subCategory": "Equity",
 "active": true, "status": "DELISTED", "tradingHours": "WEEKDAYS",
 "marketStats": {"dailyVolume": "0", ...}}
```

**INTU** (Intuit, una acción tokenizada real) tiene `status: "ACTIVE"` —
es un mercado real y operable, solo que está `isOffHours: true` (fuera del
horario de la bolsa real, "NO_OVERNIGHT") — Vol 24h = $0 en ese momento es
lo esperable, NO un mercado fantasma, igual que cualquier acción de EEUU
fuera de las 9:30-16:00 ET. **NOW_24_5** (ServiceNow) sí tiene `status:
"DELISTED"` — ese sí es el mercado muerto de verdad. La prueba de fuego:
el aviso de "65 oportunidades descartadas" que generaba
`has_zero_volume_leg()` incluía "INTU" junto a una veintena más de
tickers de acciones reales (ABNB, ADSK, AXON, BKNG, DDOG, GILD, GPS, HIMS,
JCI, LIN, MCHP, MELI, MPWR, MRNA, REGN, RIOT, TEAM, TMUS, VRTX...) — el
filtro de volumen estaba sacando del ranking mercados RWA legítimos solo
por estar fuera de su horario de bolsa, que es su estado normal la mayor
parte del día. Un heurístico basado en un síntoma downstream (volumen)
resultó tener más de una causa posible, y una de ellas era completamente
legítima.

**Fix correcto, esta vez sí en el origen**: `connectors/dex_extended.py`
ahora descarta los mercados por el campo `status` que la propia API ya
trae (cualquier valor distinto de "ACTIVE"; de momento solo se ha visto
"DELISTED" en producción) — mismo patrón que `active` en ccxt para Aster
(ver más arriba en este README). Un `status` ausente NO se descarta, por
no haber evidencia de que signifique nada malo. **`has_zero_volume_leg()`
se retiró** de `core/opportunities.py` (se dejó una nota explicando por
qué, en vez de borrarlo sin rastro, mismo criterio que la historia de
`PRICE_SCALE` en `connectors/dex_grvt.py`) junto con su aviso/panel de
diagnóstico en `pages/1_Funding_Rates.py`. Verificado con un test que usa
los valores EXACTOS de INTU y NOW_24_5 del log real: INTU se conserva
(Vol 24h=$0 pero ACTIVE), NOW_24_5 se descarta (DELISTED).

### CEX nuevos, novena tanda (BingX, Phemex — ampliación tras la comparativa con la competencia; Crypto.com se queda fuera)

Origen: al analizar la competencia (ProFunding, Loris.tools, John5Cripto —
ver `comparativa-competidores.md` en el proyecto KUSI de Claude), se
confirmó que Loris.tools cubre 3 CEX que nosotros no: BingX, Phemex y
Crypto.com. Se investigó viabilidad real de los tres (leyendo el código
fuente de ccxt 4.5.76 instalado con `inspect.getsource()`, sin red al
exchange desde este sandbox — mismo método ya usado para KuCoin/MEXC/HTX,
ver más arriba — más la documentación oficial de cada exchange vía
WebSearch/WebFetch para el intervalo real de funding).

**BingX**: trivial. `ccxt.bingx().has['fetchFundingRates']` es `True` — cae
directo en el caso genérico de `CexConnector`, igual que binance/bybit/okx/
bitget/gate, sin ningún bypass. El Open Interest del top N tampoco necesita
fallback: `parse_open_interest()` de BingX ya rellena `openInterestValue`
directo en USD para swap lineal (confirmado leyendo su código fuente).

**Phemex**: `ccxt.phemex().has['fetchFundingRates']` es `False` — no hay
bulk implementado para este exchange (a diferencia de
`fetchFundingRate()` singular, que sí existe pero supondría una llamada de
red por símbolo para todo el universo, incompatible con el patrón de este
proyecto de "scan barato" — ver docstring de `fetch_open_interest_usd()`
en `connectors/cex_ccxt.py`). Se encontró un bypass real: `fetch_tickers()`
de Phemex (bulk de verdad) usa internamente, para swap lineal
(USDT-margined), el método implícito de bajo nivel `v2GetMdV2Ticker24hrAll`
(confirmado leyendo `inspect.getsource(ccxt.phemex().fetch_tickers)`). Cada
fila cruda de esa respuesta tiene la MISMA forma que espera
`Exchange.parse_funding_rate()` de ccxt (confirmado en el propio docstring
del parser: los ejemplos "linear swap v2" muestran literalmente las claves
`fundingRateRr`/`markPriceRp`/`symbol` de esa respuesta) — así que
`CexConnector._fetch_phemex_funding_rates_bulk()` llama al endpoint
implícito directamente y reutiliza `self._client.parse_funding_rate()`, el
parser REAL de ccxt, en vez de reimplementarlo. Solo se llama el endpoint
LINEAR — se excluye a propósito el endpoint INVERSE/USD-margined
(`v1GetMdTicker24hrAll`, contratos con sufijo Ep/Er de precisión escalada),
mismo criterio ya aplicado para MEXC (Hallazgo #12 de la auditoría):
mezclar coin-margined descuadra el resto del pipeline, que asume
USDT-margined en todas partes. Para el Open Interest, Phemex tampoco
necesita bypass propio: su `parse_open_interest()` deja `openInterestValue`
siempre en `None` (solo trae `openInterestAmount`, en la moneda base), así
que cae en el mismo fallback contratos×mark_price que ya existía para
bitget — código ya escrito, cero líneas nuevas para esto.

Ninguno de los dos exchanges rellena la clave `interval` de ccxt (siempre
`None` en su `parse_funding_rate()`, confirmado leyendo el código fuente —
mismo caso que bitget), así que ambos usan el valor fijo de
`DEFAULT_INTERVAL_HOURS`. El valor (8h para los dos) no se ha inventado:
viene confirmado leyendo en vivo la documentación oficial de cada uno —
BingX ("the standard settlement interval is 8 hours... for most trading
pairs", con la salvedad de que varía por símbolo en tokens volátiles — ccxt
no expone ese detalle por símbolo, mismo límite ya asumido para bitget) y
Phemex ("the default funding settlement interval for Phemex perpetual
futures is 8 hours").

**Crypto.com — NO implementado, bloqueo real confirmado, no solo "no lo
encontré en la doc"**: a diferencia de Phemex, aquí no hay ningún bypass
bulk posible. `ccxt.cryptocom().has['fetchFundingRates']` es `False`, y
`fetch_funding_rate()` (singular) exige `instrument_name` obligatorio —
una llamada de red por símbolo vía `public/get-valuations` (confirmado
leyendo su código fuente). Se comprobó explícitamente si `fetch_tickers()`
(bulk, `public/get-tickers`) trae algún campo de funding oculto en el
ticker — su respuesta solo trae `i, h, l, a, v, vv, c, b, k, oi, t`
(máximo, mínimo, apertura, volumen, cierre, bid, ask, open interest,
timestamp — ningún campo de funding). Y para no quedarse solo con "no lo
vi en la doc" (la doc pública de Crypto.com Exchange es una SPA en
JavaScript que WebFetch no puede parsear a contenido útil — se comprobó,
devolvió solo metadata de la página), se recorrió el mapa COMPLETO de
endpoints públicos que ccxt tiene definido para este exchange
(`ex.describe()['api']`, la lista entera, no una búsqueda por palabra
clave): el único endpoint relacionado con funding en TODA la API pública
de Crypto.com Exchange, según la implementación real de ccxt, es
`public/get-valuations` — el mismo, singular, de arriba. No existe ningún
endpoint bulk de "premium index" ni equivalente, ni en tickers ni en
ningún otro sitio.

Construir un conector para Crypto.com implicaría necesariamente un bucle
de N llamadas de red (una por símbolo) solo para el scan de funding rates
del universo completo — justo el patrón que este proyecto evita a
propósito en todos los demás conectores (ver docstring de
`fetch_open_interest_usd()`: las llamadas símbolo a símbolo se reservan
para el Open Interest del top N ya filtrado, nunca para el scan inicial).
Por eso, siguiendo el mismo criterio ya aplicado con Vertex (dejado como
bloqueado en vez de forzar una implementación sobre un acceso no
confirmado) y con el bypass REST de Aster (abandonado al fallar con 400 en
vez de insistir), **Crypto.com se deja fuera de esta ampliación** en vez
de implementarlo con un bucle lento que rompería la arquitectura del
resto del proyecto. Queda pendiente una decisión explícita: implementarlo
igualmente aceptando el coste de rendimiento/rate-limit, o dejarlo fuera
del alcance del proyecto.

Confirmado con 12 tests nuevos (`test_bingx_phemex_cex_expansion.py`):
registro en `ALL_CEX_FACTORIES`/`CEX_FACTORY_BY_NAME`, que BingX usa el
camino genérico sin desviarse al bypass de Phemex, que el bypass de Phemex
solo llama al endpoint linear (nunca al inverse), que reutiliza de verdad
el parser real de ccxt (no mockeado, con una instancia real de
`ccxt.phemex()`), que una fila no parseable no tira el resto del batch, el
pipeline end-to-end completo de `fetch_funding_rates()` para Phemex, y el
fallback de Open Interest para ambos exchanges. Suite completa del
proyecto: 138/138 (los 126 anteriores + estos 12).

## Investigando (2026-09-18): oportunidades con long y short en el MISMO exchange

El usuario exportó el Ranking completo a CSV (912 filas) para buscar dónde
poner un límite de liquidez mínima (ver siguiente sección) y, al estudiarlo,
aparecieron 121 filas donde `Long en` y `Short en` son literalmente el mismo
exchange — ej. SSV en `okx (+10.9%)` vs `okx (+10.9%)`, CIFR en `gate` vs
`gate`, MUSTOCK en `mexc` vs `mexc` — y en las 121, el Spread APR sale
EXACTAMENTE 0.0%, nunca solo "parecido".

**Por qué no puede ser "un token listado solo en un exchange"** (hipótesis
que se planteó y se descartó con el propio código, sin necesidad de datos en
vivo): en `compute_opportunities()`,

```python
for symbol, group in by_symbol.items():
    if len(group) < 2:
        continue
    long_leg = min(group, key=lambda r: r.apr_pct)
    short_leg = max(group, key=lambda r: r.apr_pct)
```

si un símbolo solo tiene una fila en `group` (un solo exchange lo trae), el
`if len(group) < 2` lo descarta ENTERO — no se crea ninguna oportunidad. Así
que "listado en un solo exchange" no puede producir nunca una fila
mismo-exchange; produce cero filas para ese símbolo. Para que aparezca la
pareja mismo-exchange hace falta lo contrario: que ESE exchange, él solo,
aporte 2+ filas para el mismo símbolo normalizado.

**Candidatos de código (sin confirmar aún en vivo — ver más abajo)**:

  - `connectors/cex_ccxt.py` (okx/bitget/gate...): el filtro de quote es
    `market_symbol.endswith(":USDT") or "/USDT" in market_symbol` — un
    contrato con vencimiento tipo `SSV/USDT:USDT-241227` también contiene
    `"/USDT"` como substring, así que pasaría el filtro igual que el
    perpetuo `SSV/USDT:USDT`, y `base = ...split("/")[0]` normaliza a los
    dos como "SSV".
  - `connectors/cex_mexc.py`: `base_symbol = symbol.split("_")[0]` — si MEXC
    lista el mismo activo con dos quotes distintos (`MUSTOCK_USDT` y
    `MUSTOCK_USD`), ambos recortan a "MUSTOCK".
  - `connectors/cex_kucoin.py`: `symbol = row.get("baseCurrency")`
    directamente — si dos códigos de contrato de KuCoin comparten
    `baseCurrency`, colisionan igual.

Que el Spread APR salga exactamente 0.0% en las 121 filas (no solo
parecido) sugiere que probablemente sea el mismo contrato subyacente
reportando el mismo funding real por dos vías distintas, no dos contratos
genuinamente distintos con tasas parecidas por casualidad — pero esto
**todavía no está confirmado con datos reales**.

**Por qué no se confirmó en vivo desde aquí**: se intentó reproducir
llamando a `ccxt.okx().fetch_funding_rates()` y `ccxt.gate().fetch_funding_rates()`
directamente desde el sandbox de desarrollo para ver si de verdad salen 2
claves para el mismo símbolo base — la política de red de ESTE sandbox
bloquea esas conexiones con un 403 en el CONNECT (`curl -sS
http://127.0.0.1:38935/__agentproxy/status`), no es un fallo transitorio.

**Paso dado, solo diagnóstico, SIN cambiar comportamiento todavía**:
`pages/1_Funding_Rates.py` ahora calcula `same_exchange_pairs = [o for o in
opportunities if o.long_exchange == o.short_exchange]` justo después de
`compute_opportunities()`, y lo enseña en un nuevo expander de Diagnóstico
con el `raw_symbol` real de ambas piernas, como tabla (no JSON anidado, que
Streamlit pagina en rangos `[0-99]`/`[100-122]` ilegibles en una captura) y
con un recuento automático de cuántas filas tienen `raw_symbol` idéntico
entre las dos piernas frente a distinto. `compute_opportunities()` en sí NO
se ha tocado — no se descarta nada todavía.

**Resultado del primer despliegue con este diagnóstico**: 128/128 filas
tienen `long_raw_symbol == short_raw_symbol` EXACTAMENTE IGUAL (ej. "1000CAT"
en bitget: `long_raw_symbol="1000CAT/USDT:USDT"`,
`short_raw_symbol="1000CAT/USDT:USDT"`, ambos con el mismo APR). **Esto
descarta los tres candidatos de código planteados arriba** (quarterly
futures colándose en el filtro de ccxt, variantes de quote/margen en
MEXC/KuCoin) — todos predecían dos raw_symbol DISTINTOS, y salieron 0. Es
un duplicado literal del mismo contrato, no dos contratos reales
colisionando.

Revisado el código de `compute_opportunities()`
(`by_symbol[r.symbol].append(r)` + `if len(group) < 2: continue` +
`min()`/`max()`) y de `core/data_service.py::fetch_normalized_rates()`
(`build_connectors()` construye cada exchange una sola vez —
`ALL_CEX_FACTORIES`/`ALL_DEX_FACTORIES` no tienen ningún factory duplicado,
comprobado con `collections.Counter` sobre sus nombres — y el bucle solo
llama a `conn.fetch_funding_rates()` una vez por conector, sin acumular
entre reruns de Streamlit ni mutar `all_rates` después de `load_data()`),
ninguno de los dos explica por sí solo cómo `rates` puede contener el mismo
(exchange, raw_symbol) dos veces — un `dict` de Python (que es lo que
itera `cex_ccxt.py` vía `raw.items()`) no puede tener la misma clave
repetida. El duplicado, si viene de ahí, tendría que originarse dentro de
la propia llamada `self._client.fetch_funding_rates()` de ccxt para
bitget, no confirmado aún porque la red de este sandbox de desarrollo
bloquea la conexión a bitget con 403 en el CONNECT (mismo bloqueo que con
okx/gate, ver sección de arriba).

**Segundo diagnóstico añadido, más fino** (`core/data_service.py`): ahora,
justo al salir de CADA conector por separado (antes de fusionar nada entre
exchanges), se cuenta si ese conector YA devolvió algún `raw_symbol`
repetido dentro de su propia llamada (`logger.warning` con
`Counter(r.raw_symbol for r in rates)`), y por separado, tras fusionar
TODOS los conectores, si aparece algún `(exchange, raw_symbol)` repetido
que no estuviera ya en el chequeo individual. Esto aísla de una vez si el
duplicado nace dentro del propio `fetch_funding_rates()` de bitget (ccxt/el
exchange devolviéndolo así) o se genera después, al fusionar los
conectores en este pipeline — cualquiera de los dos casos queda confirmado
con el próximo log de despliegue, sin necesidad de adivinar. Probado con un
conector falso que devuelve el mismo raw_symbol dos veces: el aviso sale
exactamente como se espera.

## Resuelto (2026-09-19): oportunidades con long y short en el MISMO exchange (auditoría de bugs #2)

Tras la investigación de arriba (128/128 filas con `raw_symbol` idéntico
entre las dos piernas — duplicado literal del mismo contrato, no dos
contratos reales colisionando) se dejó el caso "en observación 24 horas" en
vez de filtrarlo, a la espera de ver si aparecía algún caso sospechoso
distinto. La auditoría de bugs completa (`AUDITORIA_BUGS_2026-09-19.md`,
Hallazgo #2) encontró el problema real: `pages/1_Funding_Rates.py`
calculaba `same_exchange_pairs` para el panel de diagnóstico pero **nunca
lo restaba de `opportunities`** — a diferencia de `implausible_pairs`,
`dead_liquidity` y `low_liquidity`, que sí se restan justo al lado de
calcularse. Es decir: las 121+ filas mismo-exchange seguían apareciendo en
el Ranking normal, con Spread APR llamativo (aunque fuera 0.0% en los
casos vistos hasta ahora), sin ningún filtro real protegiendo el ranking.

**Por qué salía siempre 0.0% en los casos vistos y por qué eso no bastaba
como protección**: revisando `compute_opportunities()` (`core/opportunities.py`),

```python
long_leg = min(group, key=lambda r: r.apr_pct)
short_leg = max(group, key=lambda r: r.apr_pct)
```

cuando dos filas del mismo símbolo EMPATAN en `apr_pct` (el caso real visto
en producción: duplicado literal del mismo contrato), `min()` y `max()` de
Python devuelven el MISMO objeto — verificado directamente:

```python
group = [R('bitget', 5.0), R('bitget', 5.0)]
long_leg = min(group, key=lambda r: r.apr_pct)
short_leg = max(group, key=lambda r: r.apr_pct)
# long_leg is short_leg  →  True
```

de ahí que `spread_apr` saliera exactamente 0.0% en las 121 filas — no es
que hubiera protección, es que ambas piernas son literalmente la misma
fila. Pero eso solo cubre el caso EMPATADO. Si el mismo exchange aporta dos
filas GENUINAMENTE DISTINTAS para el mismo símbolo normalizado (ej. un
contrato perpetuo y uno con vencimiento, con `apr_pct` distinto), `min()`/
`max()` sí devuelven objetos distintos, se calcula un Spread APR no-cero, y
esa fila pasaba el Ranking sin ningún aviso ni filtro — el caso realmente
peligroso, y el que la auditoría encontró sin protección alguna.

**Fix**: en `pages/1_Funding_Rates.py`, justo después de calcular
`same_exchange_pairs`, se añadió la resta que faltaba —

```python
same_exchange_pairs = [o for o in opportunities if o.long_exchange == o.short_exchange]
opportunities = [o for o in opportunities if o.long_exchange != o.short_exchange]
```

mismo patrón que los otros tres filtros — con un aviso `st.caption(...)`
nuevo (mismos símbolos que se descartan) y el panel de diagnóstico
existente actualizado para explicar el mecanismo del empate de
`min()`/`max()` y dejar constancia de la fecha del arreglo. `compute_opportunities()`
en sí no se tocó — el filtro sigue siendo responsabilidad de la capa de
página, igual que los otros tres, así que cualquier otro consumidor (por
ejemplo `cli.py`) tendría que aplicar el mismo filtro por su cuenta si
alguna vez muestra `opportunities` sin pasar por esta página.

**Verificado** con un test nuevo (`test_same_exchange_filter.py`) que usa
`compute_opportunities()` real (no solo la línea del filtro aislada): (1)
el caso EMPATADO ya visto en producción (Spread=0%) se descarta, (2) un
caso NO empatado sintético (mismo exchange, `apr_pct` distinto, Spread APR
≠ 0% — el caso que antes se colaba sin protección) también se descarta, y
(3) una oportunidad cruzada real entre dos exchanges distintos sobrevive
intacta. 18/18 tests pasan en el conjunto completo del proyecto tras este
cambio, sin regresiones.

## Resuelto (2026-09-18 → 2026-09-19): piso de liquidez mínima

Al mismo tiempo que lo de arriba, el usuario planteó que las oportunidades
con Open Interest o Volumen 24h casi-cero (pero no exactamente $0, que ya
descarta `has_dead_liquidity`) son "una trampa" — aparecen en el ranking con
un Spread APR llamativo pero son, en la práctica, imposibles de operar en
ningún tamaño real. Ejemplo real visto por el usuario: MNT en grvt(long) vs
hyperliquid(short), Spread APR 60.7%, pero Cuello de botella OI = $261 y
Cuello de botella Vol = $347 — el propio funding de GRVT para ese mercado
sale +0.0% (ver sección de GRVT/funding más abajo/arriba), consistente con
un mercado con casi ninguna actividad real: sin presión de compra/venta que
separe el precio del índice, no hay premium que calcular, así que el
funding sale plano.

Del CSV completo (912 filas, 177 con Cuello de botella OI y 170 con Cuello
de botella Vol), la distribución es continua, sin un salto/hueco natural
evidente — percentiles de Cuello de botella OI: p5=$1.345, p10=$4.740,
p20=$10.284, p25=$23.591, p50=$124.803. De Cuello de botella Vol: p5=$288,
p10=$1.958, p20=$12.152, p25=$18.090, p50=$92.978. El caso más limpio y sin
ambigüedad es Volumen EXACTAMENTE $0 (5 filas: PYTH, PEOPLE, KLUNC, KFLOKI,
US500).

**Decisión del usuario (2026-09-19): piso de $1.000**, aplicado a
CUALQUIERA de los dos lados (OI o Volumen del cuello de botella) — un
volumen casi nulo ya es la trampa por sí solo aunque el OI parezca alto,
sin nadie tradeando de verdad no se puede entrar ni salir de la posición
sin mover el precio. $1.000 cae entre p5 y p10 de ambas distribuciones, así
que solo descarta el ~5-8% más ilíquido de cada una — de paso cubre sin
necesidad de una función aparte el caso de Volumen $0 exacto de arriba (0 <
1.000).

**Implementado**: `core/opportunities.py::has_low_liquidity()` +
`LOW_LIQUIDITY_FLOOR_USD = 1000.0`, mismo patrón que
`has_dead_liquidity()`/`has_implausible_price_pair()` — una función que
solo pregunta "¿se descarta?", y `pages/1_Funding_Rates.py` filtra Y
enseña un panel de diagnóstico con las filas descartadas, para no tirar
datos en silencio. `None` (dato no consultado, no confirmado) nunca cuenta
como "por debajo del piso".

**Bug real encontrado en producción (2026-09-19) y corregido el mismo
día**: la primera versión de `has_low_liquidity()` miraba
`oi_bottleneck_usd`/`volume_bottleneck_usd` (el cuello de botella ya
calculado, el MIN de las dos piernas) — pero esos campos SOLO se calculan
cuando las DOS piernas tienen dato confirmado (ver `apply_oi_map()`/
`compute_opportunities()`). Si una pierna no se llegó a consultar (`None`
— la mayoría de filas del ranking, porque el OI Depth real solo se pide
para el top N y el Volumen depende de si el conector lo trae en el fetch
masivo), el "cuello de botella" se quedaba en `None` aunque la OTRA pierna
ya estuviera confirmada y fuera claramente ilíquida, así que la fila
pasaba el filtro intacta. El usuario lo pilló mirando la tabla del
Ranking tras desplegar: filas como **B2** (Volumen 24h CONFIRMADO=$16 en
la pierna short de aster, con el long en `None`) o **TRUST** (Volumen 24h
CONFIRMADO=$168 en variational, long en `None`) — exactamente el tipo de
trampa que este piso se creó para evitar — seguían apareciendo en el
Ranking sin ningún aviso.

**Fix**: `has_low_liquidity()` ahora comprueba las CUATRO piernas sueltas
(`oi_long_usd`, `oi_short_usd`, `volume_long_usd`, `volume_short_usd`) por
separado en vez del cuello de botella agregado — se descarta si
CUALQUIERA de las cuatro, aunque sea una sola, tiene un valor confirmado
por debajo del piso, sin esperar a que la pareja completa esté disponible.
Mismo criterio "por pierna suelta" que ya usaba `has_dead_liquidity()`
desde el principio — el bug fue no haber seguido ese mismo patrón la
primera vez. De paso se corrigió un riesgo de excepción en el panel de
diagnóstico de Streamlit: el orden de la tabla usaba
`min(cuello_de_botella_oi, cuello_de_botella_vol)` descartando `None`, que
podía quedarse sin ningún valor (`ValueError: min() arg is an empty
sequence`) precisamente en las filas nuevas que ahora sí se descartan por
una sola pierna — sustituido por un `_min_known()` que devuelve `inf`
cuando las cuatro piernas son `None`, y la tabla ahora enseña las cuatro
piernas sueltas en vez de solo los dos cuellos de botella. Test de
regresión con los casos reales B2/TRUST (una sola pierna confirmada y
baja, con el bottleneck en `None` por la otra pierna sin consultar) más
el caso real del CSV original (MNT grvt/hyperliquid, OI=$261/Vol=$347),
Volumen $0 exacto, los bordes del umbral y liquidez sana de control.
**Confirmado en producción (2026-09-19, tras el redespliegue)**: el
usuario revisó la tabla del Ranking en vivo tras el fix ("Y en la tabla
ya está bien") — las filas con una sola pierna confirmada y por debajo de
$1.000 (el caso B2/TRUST) ya no se cuelan.

De paso, estudiando el mismo CSV aparecieron dos hallazgos más sin
investigar todavía:

  - **SOL en kucoinfutures muestra OI long negativo — RESUELTO
    (2026-09-19), causa raíz confirmada con datos reales de producción**:
    visto tres veces en producción (−$616.481.571, −$618.445.974,96,
    −$613.841.851,68 — mismo orden de magnitud, no un dato congelado). Se
    añadió primero una guardia + `logger.warning` con el payload crudo (sin
    saber aún la causa), y el siguiente despliegue trajo el culpable real
    en el log:

        kucoinfutures DIAGNÓSTICO OI negativo (4 contrato(s)): {
          "ETHUSDM": {"openInterest_raw": "24974631", "multiplier_raw": -1.0, "markPrice_raw": 2608.06, ...},
          "SOLUSDM": {"openInterest_raw": "5424548",  "multiplier_raw": -1.0, "markPrice_raw": 113.326, ...},
          "XBTUSDM": {"openInterest_raw": "46529511", "multiplier_raw": -1.0, "markPrice_raw": 81148.8, ...},
          "XRPUSDM": {"openInterest_raw": "7629466",  "multiplier_raw": -1.0, "markPrice_raw": 1.3995,  ...}
        }

    El símbolo real no era "SOLUSDTM" (el lineal, investigado primero, que
    siempre vino sano) — era **"SOLUSDM"** (sin la "T"), un contrato
    DISTINTO. Confirmado en vivo vía WebFetch al endpoint de detalle
    (`GET /api/v1/contracts/SOLUSDM` — el listado completo se trunca sin
    avisar, un intento anterior con él dio un falso "no existe"):
    `baseCurrency=SOL, quoteCurrency=USD, settleCurrency=SOL,
    multiplier=-1.0, isInverse=true`. Es un contrato **inverso**
    (coin-margined, P&L y margen en SOL) que comparte `baseCurrency=SOL`
    con el lineal (USDT-margined) — este conector los normalizaba al mismo
    `symbol="SOL"`, así que `compute_opportunities()` los agrupaba y
    comparaba como si fueran el mismo mercado. La fórmula `openInterest ×
    multiplier × markPrice` solo es válida para contratos lineales; en un
    inverso, `multiplier=-1.0` es un centinela de "esto es inverso", no una
    cantidad real, y no hay una fórmula de conversión a USD confirmada
    (la documentación de KuCoin es una SPA — WebFetch solo pudo leer el
    esqueleto de navegación, no el cuerpo).

    **Fix**: se excluyen del todo los contratos con `isInverse=True` (o
    `multiplier<0` como señal de refuerzo) en `cex_kucoin.py`, en vez de
    inventar su conversión a USD. Es la decisión correcta más allá del bug
    de OI: un mercado coin-margined no es la misma operación que uno
    USDT-margined, así que no debían tratarse como el mismo símbolo "SOL"
    de todos modos. La guardia genérica de OI negativo se mantiene como
    red de seguridad para cualquier otra causa futura, ahora marcada
    "inesperada" en el log si dispara sin `isInverse=True`. Tests de
    regresión (`test_kucoin_negative_oi_guard.py`) con los valores reales
    de producción de SOLUSDM + SOLUSDTM: el inverso se excluye del todo, el
    lineal sigue sano, y la guardia de respaldo se sigue probando por
    separado para una causa hipotética distinta. **Confirmado en producción
    (2026-09-19)**: SOL ya aparece en el Ranking con OI/Volumen positivos y
    con sentido (ej. OI long $736 mil, OI short $1,9 M, Vol 24h $362 mil /
    $4,2 M) — sin rastro de OI negativo.
  - **Las 47 filas donde participa GRVT muestran las 47 exactamente
    "+0.0%" de funding**, incluyendo activos muy líquidos (LINK, ADA, AVAX,
    UNI, AAVE, ARB, JUP, con Cuello de botella OI de cientos de miles de
    dólares) — este era el mismo bug de `funding_rate_8h_curr` ×
    `CENTIBEEPS_TO_DECIMAL`, corregido el 2026-09-18 (`/ 100.0` en vez de
    `× CENTIBEEPS_TO_DECIMAL`) y **confirmado en producción el 2026-09-19**
    (ver sección de GRVT/funding más arriba). Pendiente solo de un nuevo CSV
    completo del Ranking tras el redespliegue, para confirmar que ya no
    quedan las 47 filas en "+0.0%".

## Resuelto (2026-09-19): guard de OI negativo en MEXC y HTX (auditoría de bugs #3)

La auditoría completa (`AUDITORIA_BUGS_2026-09-19.md`, Hallazgo #3) señaló
que `cex_mexc.py` y `cex_htx.py` calculan Open Interest en USD igual que
`cex_kucoin.py` (`holdVol × contractSize × fairPrice` en MEXC; `value`
directo en HTX) pero, a diferencia de KuCoin, **no tenían ninguna guardia
si ese cálculo daba negativo** — un valor físicamente imposible se habría
propagado tal cual al Ranking, en vez de descartarse a `None` como ya pasa
en KuCoin desde el bug real de SOLUSDM.

**Diferencia importante con el fix de KuCoin**: aquí NO hay ninguna causa
raíz confirmada con datos de producción (no se ha visto todavía OI
negativo real en MEXC ni HTX) — es un guard puramente defensivo, igual que
la "guardia de respaldo" que ya tenía KuCoin para causas no confirmadas.
No se excluye ningún tipo de contrato (no hay evidencia de que MEXC/HTX
tengan un equivalente a los contratos inversos de KuCoin) — solo se evita
que un cálculo negativo, si algún día aparece, se cuele en el Ranking.

**Fix**: mismo patrón exacto que la guardia de respaldo de
`cex_kucoin.py` — si `open_interest_usd` calculado sale negativo, se
descarta a `None` (nunca se propaga) y se deja un `logger.warning` con el
payload crudo de los factores usados (en MEXC: `holdVol`, `contractSize`,
`fairPrice`; en HTX: `value`, ya que ahí no hay cálculo propio) para poder
investigar la causa si algún día se dispara. Aplicado en `cex_mexc.py`
justo después de calcular `open_interest_usd` y en `cex_htx.py` justo
después de leer `value`.

**Verificado** con un test nuevo (`test_mexc_htx_negative_oi_guard.py`,
datos sintéticos ya que no hay evidencia real de este caso todavía): el
caso normal (OI positivo) no se ve afectado en ninguno de los dos
conectores, y un OI negativo sintético (precio/valor anómalo) se descarta
a `None` y queda registrado en el log en ambos. 22/22 tests pasan en el
conjunto completo del proyecto tras este cambio, sin regresiones.

## Resuelto (2026-09-19): Hallazgos #4 y #5 de la auditoría — `cex_ccxt.py`

Ambos en el mismo módulo (`connectors/cex_ccxt.py`), resueltos a la vez.

**Hallazgo #4 — `next_funding_time` guardaba un string, no un `datetime`.**
`FundingRate.next_funding_time` está tipado `Optional[datetime]`
(`connectors/base.py`), pero `cex_ccxt.py` le asignaba directamente
`entry.get("fundingDatetime")` — el string ISO8601 crudo que da ccxt (ej.
`"2026-09-19T08:00:00.000Z"`), sin convertir. Confirmado con `grep` que hoy
es inerte de verdad: ningún archivo de `core/` ni de `pages/` lee este
campo, y ni siquiera `core/normalize.py` lo traslada a `NormalizedRate`
(no existe ahí). No rompe nada hoy, pero es un bug latente — si algún día
se usa (ej. para mostrar cuándo liquida cada oportunidad), se rompería con
un string donde se espera un objeto `datetime`.

**Fix**: se convierte con el propio parser ISO8601 de ccxt
(`self._client.parse8601(...)`, el mismo que ccxt usa internamente para
construir `fundingDatetime` a partir del timestamp en ms — no se reinventa
el parseo) a un `datetime` real, timezone-aware en UTC. Si el campo falta
o no se puede parsear, se guarda `None` en vez de lanzar una excepción o
propagar basura.

**Hallazgo #5 — intervalo fijo de 8h para 6 exchanges, sin usar el dato
real cuando ccxt SÍ lo trae.** `DEFAULT_INTERVAL_HOURS` asumía 8h fijas
para binance/bybit/okx/bitget/gate/aster, con un comentario propio del
código reconociéndolo como aproximación pendiente de refinar. Antes de
tocar nada se confirmó **leyendo el código fuente de ccxt instalado en
este entorno** (versión 4.5.76, método `parse_funding_rate()` de cada
exchange) en vez de asumir:

  - `binance`/`bybit`/`okx`/`gate`/`aster`: **SÍ** rellenan una clave
    `'interval'` (string tipo `"8h"`, `"4h"`, `"16h"`...) en cada fila de
    `fetch_funding_rates()`, calculada por ccxt a partir de un dato real
    del exchange por símbolo (`fundingIntervalHours` en binance/aster,
    `fundingInterval` de la metadata de mercado en bybit, la diferencia
    `nextFundingTime − fundingTime` en okx, `funding_interval` en gate).
  - `bitget` es la excepción confirmada: su `fetch_funding_rates()` usa
    por defecto el endpoint `publicMixGetV2MixMarketTickers`, cuya
    respuesta bulk (confirmado leyendo el propio docstring de
    `parse_funding_rate()` en `bitget.py`) NO incluye `ratePeriod`/
    `fundingRateInterval` — ese campo solo lo trae el endpoint alternativo
    de `fetchFundingInterval`, que este conector no llama. Así que para
    bitget seguirá cayendo siempre al valor fijo (8h) con el código
    actual — no es un descuido, es el límite real de la llamada que
    hacemos hoy.

**Fix**: se lee `entry.get("interval")` de cada fila y, si viene con un
valor válido (parseado con una regex tolerante, nunca lanza), se usa
DIRECTAMENTE como `interval_hours` de esa fila — el diccionario fijo pasa
a ser solo el fallback para cuando el campo no viene (bitget siempre, y
cualquier símbolo de los otros cinco donde ccxt tampoco lo traiga). Se
añadió un log de diagnóstico por exchange (`%s: intervalo real de ccxt
usado en %d/%d símbolos...`) para confirmar en producción cuántos
símbolos de cada exchange usan de verdad el dato real frente al fallback.

**Verificado** con un test nuevo
(`test_ccxt_interval_and_next_funding.py`) usando una instancia REAL de
`ccxt.binanceusdm()` (sin red — solo se sustituyen
`fetch_funding_rates()`/`fetch_tickers()`/`markets`, dejando
`parse8601()` como el método real de ccxt, no un mock) con payloads
sintéticos que replican el formato exacto confirmado en el código fuente:
intervalo real (`"4h"`) sustituye al fijo, intervalo ausente cae al fijo,
valores de `interval` inválidos (`None`, string vacío, no numérico, tipo
incorrecto) caen al fallback sin lanzar excepción, `fundingDatetime`
válido produce un `datetime` real timezone-aware con el valor correcto, y
`fundingDatetime` ausente o no parseable da `None` sin excepción. 28/28
tests pasan en el conjunto completo del proyecto tras este cambio, sin
regresiones.

## Resuelto (2026-09-19): Hallazgo #6 — mercados `inactive` sin filtrar en Lighter

`connectors/dex_lighter.py` veía, desde el arreglo cosmético del
`raw_symbol` (ver más arriba), un campo `status` (`"active"`/`"inactive"`)
en `orderBookDetails` que no se usaba todavía — mismo patrón, en teoría,
que el bug real ya arreglado en `dex_extended.py` (mercados delistados que
seguían dando datos de ticker).

**Antes de tocar nada se confirmó en vivo (WebFetch, 2026-09-19)**: en el
momento de la investigación había mercados `"inactive"` — 8 market_id se
repitieron de forma consistente en dos llamadas distintas a
`orderBookDetails` (MAGS, SPACEX, AI16Z, HYUNDAI, KRCOMP, DUSK, BIRB,
LAUNCHCOIN), aunque el TOTAL de mercados que devolvió WebFetch varió entre
llamadas (63 vs. 80) — la misma truncación silenciosa en arrays grandes ya
documentada en `connectors/cex_kucoin.py`, así que ese total no es
fiable, pero estos 8 market_id concretos sí. Se comprobó, uno a uno contra
`/funding-rates`, si alguno de esos 8 tenía una fila `exchange="lighter"`
— que es lo que este conector ya exige para no descartar un mercado (ver
la nota del propio módulo sobre exchanges de referencia mal etiquetados).

**Resultado confirmado: NINGUNO de los 8 la tenía.** Es decir, a
diferencia de Extended (donde SÍ había un bug real observado), aquí el
filtro que YA existía (solo se queda con la fila propia de "lighter") ya
descartaba de facto estos 8 mercados inactivos, sin necesidad del campo
`status` para nada — el `status` sin usar no estaba causando ningún dato
malo en el Ranking hoy.

**Fix aplicado de todos modos, con el mismo criterio que el guard de OI
negativo de MEXC/HTX (Hallazgo #3)**: defensivo, sin causa raíz real
observada, pero correcto tenerlo. `/funding-rates` y `/orderBookDetails`
son dos llamadas HTTP independientes sin ninguna garantía documentada de
actualizarse atómicamente a la vez — es perfectamente posible que un
mercado pase a `"inactive"` en el order book mientras `/funding-rates`
todavía trae, por una ventana breve, una fila `"lighter"` residual. Se
excluyen del todo (no solo se dejan sin profundidad) los `market_id`
marcados `status` distinto de `"active"`, mismo patrón que el filtro de
`status` de `dex_extended.py`: un `status` ausente NO se descarta.

**Verificado** con un test nuevo
(`test_lighter_inactive_status_guard.py`): el caso real confirmado (MAGS,
inactive y sin fila lighter) sigue descartado igual que antes; el caso
hipotético que el fix protege de verdad (un mercado inactive con una fila
lighter residual, simulando la desincronización) ahora SÍ se descarta,
cosa que antes no pasaba; un mercado `"active"` sano no se ve afectado; y
un `status` ausente no se descarta, mismo criterio que Extended. 32/32
tests pasan en el conjunto completo del proyecto tras este cambio, sin
regresiones.

## Resuelto (2026-09-19): Hallazgos #7 y #8 de la auditoría — cadena de fallback de `funding_rate` en GRVT

Ambos en el mismo bloque de código (`connectors/dex_grvt.py`, la línea que
resuelve qué campo usar para la tasa de funding de cada instrumento),
resueltos a la vez.

**Hallazgo #7 — bug real de Python, CONFIRMADO por lectura de código: un
`null` explícito rompería el fallback entero.** El código anterior
encadenaba los candidatos con `.get()` anidados:

```python
rate_raw = ticker.get(
    "funding_rate",
    ticker.get("funding_rate_8h_curr", ticker.get("funding_rate_curr")),
)
```

`dict.get(clave, default)` solo devuelve `default` cuando la CLAVE NO
EXISTE — si la clave existe con valor `None` explícito, `.get()` devuelve
ese `None`, no sigue probando el resto de la cadena. Con esta forma de
escribirlo, si GRVT mandara algún día `"funding_rate": null` de forma
explícita (en vez de omitir la clave), el resultado sería `None` aunque
`funding_rate_8h_curr` sí trajera un valor real — el resto de la cadena
nunca llegaba a probarse. No hay evidencia de que GRVT haga esto hoy (no
se ha observado en producción), pero es un bug real del propio Python en
la forma en que estaba escrito el fallback, no una suposición.

**Fix**: se sustituyó la cadena de `.get()` anidados por un bucle explícito
que prueba cada clave candidata por orden y solo la descarta si su valor
es `None` — sin importar si la clave estaba ausente o presente-pero-nula,
el resultado es el mismo (seguir probando la siguiente), que es el
comportamiento que la cadena de `.get()` anidados solo daba por accidente
cuando las claves estaban ausentes.

**Hallazgo #8 — el `÷100` solo está confirmado para los campos antiguos,
pero se aplicaba igual si el valor viniera del campo nuevo `funding_rate`
(unidad sin confirmar, potencialmente "centibeeps" según la documentación
de GRVT) — SOSPECHOSO.** La cadena de fallback anterior probaba
`funding_rate` PRIMERO, antes que `funding_rate_8h_curr`/
`funding_rate_curr` — es decir, si `funding_rate` alguna vez trajera un
valor real, se le habría aplicado a ciegas el mismo `÷100` que solo está
confirmado (tres despliegues de producción independientes, ver la sección
de más arriba) para los campos antiguos. El `÷100` de `funding_rate_8h_curr`
y el "centibeeps" (`×1e-6`) que la documentación de GRVT sugiere para
`funding_rate` son conversiones muy distintas — aplicar la equivocada
daría un número con la escala completamente rota, silenciosamente.

**Antes de tocar nada se confirmó qué trae realmente `funding_rate` hoy**:
la lista completa de claves capturada de un log real de producción (ya
documentada en el propio docstring del módulo, sección "ESTADO ACTUAL
CONFIRMADO") incluye `funding_rate_8h_curr` y `funding_rate_8h_avg`, pero
**`funding_rate` nunca ha aparecido con datos reales en ninguna captura de
producción** — no es que esté mal medido, es que GRVT simplemente no lo
está sirviendo todavía en el endpoint de ticker que usa este conector.
Se intentó re-confirmar en vivo con WebFetch contra
`market-data.grvt.io/full/v1/ticker`, pero ese endpoint solo acepta POST
(WebFetch únicamente hace GET, da `405`), así que no hay forma de volver a
comprobarlo desde este entorno — se usó la evidencia ya capturada y
documentada en el módulo en vez de inventar una nueva.

**Fix**: se invirtió el orden de prioridad (los campos confirmados
primero) y, si el único valor real disponible viene de `funding_rate`
(sin confirmar), el instrumento se **descarta** en vez de adivinar su
escala — igual que el patrón ya establecido para KuCoin con los contratos
inversos sin confirmar. Se añadió un diagnóstico (`logger.warning`)
separado que registra, si esto llega a pasar alguna vez, el nombre del
instrumento y el valor crudo de `funding_rate`, para poder confirmar su
unidad real con datos de producción en vez de suponerla. **Como
`funding_rate` no ha aparecido nunca con datos reales, este fix no cambia
ningún dato visible en el Ranking hoy** — es protección de cara al futuro,
para el día en que GRVT complete la migración a ese campo.

**Verificado** con un test nuevo (`test_grvt_funding_rate_fallback.py`, 5
tests): un `null` explícito en el primer campo confirmado ya no rompe la
cadena (cae correctamente al segundo campo confirmado); con los dos
campos confirmados en `null` y sin `funding_rate`, el instrumento se
descarta sin lanzar ninguna excepción; el caso normal (`funding_rate_8h_curr`
con el `÷100` ya verificado) sigue exactamente igual que antes; un valor
real solo en `funding_rate` (sin confirmar) se descarta — no se le aplica
el `÷100` — y queda registrado en el diagnóstico nuevo con el nombre del
instrumento; y si algún día coexistieran ambos campos (migración a
medias), el campo confirmado tiene prioridad sobre el nuevo. 37/37 tests
pasan en el conjunto completo del proyecto tras este cambio, sin
regresiones.

## Resuelto (2026-09-19): Hallazgos #9 y #10 de la auditoría — filtro de Nado y ticker silencioso de MEXC/HTX

Dos conectores distintos, resueltos a la vez.

**Hallazgo #9 — Nado: un cambio de forma en el endpoint de status apagaba
el filtro entero, en silencio — CONFIRMADO.** `connectors/dex_nado.py`
cruza `/archive/v2/contracts` (precios/funding/OI) con
`/gateway/v1/query?type=symbols` (el único sitio con `trading_status`) para
descartar mercados no operables — el mismo problema ya visto con
Aster/RiseX. El código anterior:

```python
symbols_map = (symbols_payload.get("data") or {}).get("symbols") or {}
if not symbols_map:
    logger.warning(...)
...
if symbols_map:  # <- si está vacío, se salta TODO el filtro
    status = (symbols_map.get(base_currency) or {}).get("trading_status")
    if status != "live":
        continue
```

Si `data.symbols` llegaba vacío (el propio docstring del módulo documenta
que esto YA ha pasado dos veces con otro endpoint de Nado — la API cambia
de forma sin avisar), el filtro completo de "solo mercados `live`" se
desactivaba para TODOS los mercados a la vez, no solo para el símbolo
afectado — reintroduciendo exactamente el tipo de mercado fantasma/pausado/
no lanzado que este conector se construyó para excluir. Solo quedaba
constancia de un `logger.warning`, fácil de no ver, y el Ranking se
llenaría de mercados no confirmados sin ningún indicio de que algo había
fallado.

**Fix**: en vez de asumir "sin datos de status = todo vale", se trata igual
que la comprobación ya existente de `/archive/v2/contracts` vacío — un
fallo real y ruidoso. Si `data.symbols` llega vacío se lanza un
`RuntimeError` explícito. `core/data_service.py` ya captura cualquier
excepción por conector y sigue con el resto de exchanges (ver el propio
comentario de ese archivo: "un exchange que falla NO tira abajo a los
demás"), así que esto no rompe la app — solo hace que Nado desaparezca del
Ranking ese ciclo concreto (visible como error en la interfaz) en vez de
colar mercados sin confirmar en silencio.

**Verificado** con un test nuevo (`test_finding_9_and_10.py`): el caso
normal (con `trading_status` real) no se ve afectado; un mercado con
`trading_status` distinto de `"live"` se sigue excluyendo igual que antes;
y `data.symbols` vacío (o la clave `data` ausente del todo, otra forma real
de "cambió el shape") ahora lanza `RuntimeError` en vez de dejar pasar
mercados sin confirmar.

**Hallazgo #10 — MEXC/HTX: el endpoint de ticker tragaba en silencio una
respuesta vacía o rota — CONFIRMADO.** En ambos conectores, los otros
endpoints (`detail`/`funding_rate` en MEXC; `funding_rate`/`open_interest`
en HTX) SÍ lanzan `RuntimeError` si vienen vacíos o con otra forma — pero
el endpoint de ticker (de donde sale el OI entero de MEXC, y el
mark_price/volumen de HTX) solo tenía un `... or []` silencioso:

```python
ticker_rows = ticker_payload.get("data") or []  # MEXC — sin aviso si viene vacío
```
```python
ticker_rows = ticker_payload.get("ticks")
if ticker_rows is None:
    ticker_rows = ticker_payload.get("data") or []  # HTX — sin aviso si viene vacío
```

Si alguna vez ese endpoint responde vacío o cambia de forma (HTX ya lo ha
hecho una vez, según el propio docstring del módulo: la clave de nivel
superior pasó de `"data"` a `"ticks"`), las columnas que dependen de él
quedan en `None` para TODO el exchange, sin ningún aviso — indistinguible
de "este exchange simplemente no reporta esto".

**Fix**: a diferencia del Hallazgo #9, aquí NO se convierte en
`RuntimeError` — el `funding_rate` en sí sigue siendo válido y útil sin
OI/mark_price/volumen (no es un filtro de seguridad que, al desactivarse,
cuela datos falsos; es enriquecimiento que, al faltar, solo deja columnas
en blanco). Se añadió un `logger.warning` explícito en ambos conectores
cuando el ticker llega vacío, con las claves de nivel superior recibidas
para poder diagnosticar un cambio de forma real.

**Verificado** con el mismo test nuevo: el caso normal (ticker con filas)
no dispara ningún aviso nuevo; un ticker vacío (`"data": []` en MEXC,
`"ticks": []` en HTX) deja el `funding_rate` intacto pero OI/mark_price/
volumen en `None`, y ahora SÍ queda registrado explícitamente; y una
respuesta con la clave esperada ausente del todo también dispara el
aviso. En HTX se confirmó además que el OI (que viene de un endpoint
totalmente distinto, `swap_open_interest`) no se ve afectado por un
ticker vacío — el fallo queda aislado a las columnas que de verdad
dependen de ese endpoint. 46/46 tests pasan en el conjunto completo del
proyecto tras este cambio, sin regresiones.

## Resuelto (2026-09-19): Hallazgo #11 de la auditoría — MEXC usaba `apiAllowed` en vez de `state` como filtro de operable

`connectors/cex_mexc.py` usaba `apiAllowed` como único filtro de "¿este
contrato está vivo?" porque su nombre parecía autoexplicativo, dejando sin
usar el campo `state` porque su mapeo "no estaba documentado" (solo se
había confirmado un ejemplo en vivo, BTC_USDT, con `state=0`/
`apiAllowed=true`). La auditoría de bugs (Hallazgo #11) señaló que esto era
el mismo patrón exacto que el bug real ya arreglado en `dex_extended.py`:
un campo con nombre prometedor que no es el que de verdad marca
"delistado".

**Confirmado leyendo la documentación oficial de MEXC**
(mexcdevelop.github.io/apidocs/contract_v1_en/):

```
"state": 0, "status, 0:enabled,1:delivery, 2:completed, 3:offline, 4:pause"
"apiAllowed": bool, "whether support api"
```

`state` ES el campo de estado de listado — el equivalente exacto al
`status`/`orderBookState` que ya se usa en KuCoin/Extended/Lighter/etc.
`apiAllowed`, según la propia doc, es un flag de si la API soporta ese
contrato, no un estado de listado — y como este proyecto solo lee
endpoints públicos de solo lectura (no opera vía API), lo relevante es si
el contrato está listado y operable, no ese flag cuyo alcance exacto la
doc no termina de aclarar.

**Se intentó confirmar en vivo** si algún contrato real diverge (`state`
!= 0 con `apiAllowed=true`, o al revés) con dos llamadas WebFetch a
`/api/v1/contract/detail` — misma limitación de truncamiento en arrays
grandes ya documentada varias veces en este proyecto (KuCoin, Lighter):
solo se ve una porción parcial del array (probablemente >1000 contratos),
y en esa porción todos traían `state=0` y `apiAllowed=true`. No hay, de
momento, ningún caso real observado donde diverjan — misma situación que
el campo `funding_rate` sin confirmar de GRVT (Hallazgo #8): el riesgo
está confirmado por documentación, pero no cazado en producción todavía.

**Fix**: se usa `state == 0` como filtro principal de operable en vez de
`apiAllowed` — un `state` ausente NO descarta el símbolo (mismo criterio
de "sin evidencia de que esté mal" ya usado en el resto del proyecto).
`apiAllowed` se sigue capturando, pero solo para un diagnóstico: si algún
día un contrato trae los dos campos en desacuerdo, queda registrado en un
log en vez de perderse en silencio, en vez de exigir que ambos coincidan
para incluir un contrato — eso habría añadido una restricción sin
evidencia que la respalde, en la dirección contraria al criterio de "no
inventar" de este proyecto (podría esconder una oportunidad real basándose
en una suposición sin confirmar sobre qué mide `apiAllowed`).

**Verificado** con un test nuevo (`test_mexc_state_filter.py`): el caso
normal (`state=0`) sigue incluido igual que antes; un contrato con `state`
distinto de 0 (ej. `state=3`, "offline" según la doc) ahora SÍ se excluye,
aunque `apiAllowed=true` — antes se habría colado sin que nada lo pillara,
justo el escenario que señalaba el Hallazgo #11; un `state` ausente no se
descarta; y una divergencia sintética entre `state` y `apiAllowed` se
resuelve a favor de `state` (confirmado) y queda registrada en el
diagnóstico nuevo, sin perderse en silencio. 51/51 tests pasan en el
conjunto completo del proyecto tras este cambio, sin regresiones.

**Actualización (2026-09-19, confirmado con datos REALES de producción,
mismo despliegue que confirmó el Hallazgo #12 de abajo)**: el diagnóstico
nuevo SÍ disparó en el primer despliegue, con 10 contratos reales:
`LONG_USDT`, `MUSEBOOK_USDT`, `MCAT_USDT`, `ORBIO_USDT`, `PAID_USDT`,
`HOOKR_USDT`, `ROBIN_USDT`, `COOL_USDT`, `GSTOCK_USDT`, `PAIR_USDT` —
todos con `state=0` (operable según la doc oficial) pero
`apiAllowed=False`. Esto confirma con evidencia real la decisión de usar
`state` solo y NO exigir también `apiAllowed`: si se hubiera exigido que
los dos campos coincidieran (la alternativa más conservadora que se
consideró), estos 10 contratos reales y operables se habrían excluido del
Ranking por error — exactamente el falso negativo que se quería evitar al
no inventar una restricción sin confirmar sobre qué mide `apiAllowed`.

## Resuelto (2026-09-19): Hallazgo #12 de la auditoría — contratos inversos/coin-margined sin detectar en MEXC

`connectors/cex_mexc.py` solo recortaba el sufijo de la cotización al
normalizar el símbolo (`symbol.split("_")[0]`), sin ningún equivalente al
`isInverse`/`multiplier<0` que ya tiene `cex_kucoin.py` desde el bug real
de SOLUSDM. La auditoría de bugs (Hallazgo #12) señaló que era el mismo
hueco exacto: si MEXC lista algún contrato margined-en-moneda-base
("Coin-M") junto a uno USDT-margined para el mismo activo base, se
normalizarían al mismo símbolo y, como su fórmula de OI difiere, el número
saldría mal en silencio.

**Confirmado que MEXC sí tiene esa línea de producto separada**: su propio
blog/glosario oficial documenta "Coin-Margined Futures", y existe una
página de trading real para `BTC_USD` (`mexc.com/futures/coin-m/BTC_USD`),
distinta de `BTC_USDT`. La documentación oficial de la API confirma
además que `/api/v1/contract/detail` trae `baseCoin`, `quoteCoin` y
`settleCoin` por contrato — campos que solo tienen sentido si el endpoint
mezcla contratos linear (`quote == settle`, USDT) e inverse (`settle` en
el activo base) en el mismo array bulk.

**Lo que NO se pudo confirmar en vivo**: si un contrato Coin-M (ej.
`BTC_USD`) aparece de verdad en el array de `/api/v1/contract/detail` que
lee este conector — misma limitación de truncamiento en arrays grandes ya
documentada varias veces en este proyecto (KuCoin, Lighter, Hallazgo #11):
con WebFetch solo se pudo ver una porción parcial (~50 de probablemente
1000+ contratos), y curl directo está bloqueado por la política de red de
este sandbox. No hay una fila real observada con `settleCoin != quoteCoin`.

**Fix**: se excluyen ENTEROS los contratos donde `settleCoin != quoteCoin`
(ambos presentes) — mismo criterio que `isInverse` en KuCoin: un campo
real de la API, no el nombre del símbolo. `quoteCoin`/`settleCoin`
ausentes NO se tratan como inverso, sin evidencia de que lo sean. Se
registra un log con los símbolos excluidos si esto llega a pasar alguna
vez, para poder confirmarlo con datos reales de producción — igual que el
resto de guards puramente defensivos de esta sesión (Hallazgos #3, #6).

**Verificado** con un test nuevo (`test_mexc_inverse_guard.py`, con datos
sintéticos ya que no hay evidencia real observada, mismo criterio que los
Hallazgos #3 y #6): el caso normal (contrato linear) no se ve afectado; un
`BTC_USD` coin-margined sintético junto al `BTC_USDT` linear normal se
excluye ENTERO (no solo pierde OI) y queda registrado en el log nuevo;
`quoteCoin`/`settleCoin` ausentes no se tratan como inverso; y,
específicamente, se confirmó que el inverso NO contamina el OI del linear
del mismo activo base al colisionar en el símbolo normalizado ("BTC") —
el escenario exacto que señalaba el Hallazgo #12. 55/55 tests pasan en el
conjunto completo del proyecto tras este cambio, sin regresiones.

**Actualización (2026-09-19, CONFIRMADO con datos REALES de producción)**:
el primer despliegue de este fix confirmó exactamente el riesgo que
señalaba el Hallazgo #12 — 10 contratos Coin-M reales excluidos por el
guard nuevo: `ADA_USD`, `AVAX_USD`, `BTC_USD`, `DOGE_USD`, `ETH_USD`,
`LINK_USD`, `LTC_USD`, `SOL_USD`, `SUI_USD`, `XRP_USD`. Todos estos activos
YA tienen su contrato linear (`_USDT`) en el Ranking, así que antes de este
fix habrían colisionado de verdad en el símbolo normalizado (`"BTC"`,
`"ETH"`, etc.) con su hermano linear — el hallazgo pasa de SOSPECHOSO a
CONFIRMADO con evidencia real, no solo de documentación.

## Resuelto (2026-09-19): Hallazgos #13, #14 y #15 de la auditoría — `mark_price == 0` sin guardar, y dos diagnósticos de escala sin confirmar (Paradex, Vertex)

Los tres se estudiaron juntos antes de tocar código (petición explícita del
usuario: "Estudia el 13, 14 y 15"), y se implementaron los tres juntos tras
su aprobación ("vamos a hacer las 3").

**Hallazgo #13 (severidad baja, CONFIRMADO como gap real de código, aunque
nunca observado en vivo)**: el cálculo `open_interest_usd = X * mark_price`
en todos los conectores solo comprobaba `mark_price is not None`, nunca
`!= 0`. Un mark price explícito de 0 (en vez de campo ausente) pasaba ese
chequeo igual y producía `open_interest_usd = 0.0` — un mercado real
mostrado como si tuviera profundidad cero, indistinguible de un error real.

La auditoría citó 3 archivos (`cex_kucoin.py:261-266`, `cex_mexc.py:203-208`,
`cex_ccxt.py:233-244`), pero al estudiarlo se encontró que:
- La cita de `cex_ccxt.py:233-244` no es el sitio correcto — esas líneas son
  el fix de los Hallazgos #4/#5 (`next_funding_time`/`interval`). El hueco
  real está en `fetch_open_interest_usd()` (el fallback
  `openInterestAmount × mark_price` para exchanges como bitget).
- El mismo hueco exacto existía, sin excepción, en **9 conectores DEX** más:
  hyperliquid, backpack, apex, paradex, hibachi, risex, grvt, pacifica y
  lighter. El usuario decidió arreglar los 12 archivos, no solo los 3
  citados.

**Fix**: se trata `mark_price == 0` igual que un mark price ausente (se
descarta a `None`, no se calcula OI ni se publica un precio de 0) y se
registra una muestra de diagnóstico por conector, mismo criterio que la
guardia de OI negativo del Hallazgo #3. En `cex_ccxt.py`, al no tener el
mismo patrón de "muestra acumulada" que el resto, se reporta como un error
explícito por símbolo en vez de un valor `0.0` silencioso.

**Hallazgo #14 (SOSPECHOSO, sigue sin confirmar)**: la escala de
`funding_rate` en Paradex nunca se verificó contra un valor real —
`dex_paradex.py` usa el campo crudo tal cual, sin ningún escalado. Se
investigó en profundidad: la página de mecanismo de funding de Paradex trae
un ejemplo trabajado (0.0003 = 0.03% por 8h, decimal sin escalar, coincide
con el código actual), pero la página de referencia del endpoint describe
el campo como "funding rate **percentage**" con un ejemplo `"0.3"` que
tiene toda la pinta de ser un placeholder autogenerado del esquema OpenAPI
(varios campos vecinos en ese mismo ejemplo son números redondos poco
creíbles). Un intento de llamar en vivo a `api.prod.paradex.trade` lo
bloqueó el propio sandbox pidiendo una aprobación que no llegó a tiempo. Sin
poder confirmar ninguna de las dos lecturas, **no se aplicó ningún factor de
escala** (mismo criterio de siempre: mejor no tocar que adivinar). Se añadió
en su lugar un log de diagnóstico (`paradex DIAGNÓSTICO escala de
funding_rate`) con los primeros valores crudos de cada ciclo, para
confirmar la escala real con el próximo log de producción.

**Hallazgo #15 (SOSPECHOSO, sigue sin confirmar — ya sabíamos que Vertex es
el conector menos confirmado; esto lo concreta)**: tanto el ÷1e18 de
`funding_rate` como la suposición "`open_interests` ya viene en USD" en
`dex_vertex.py` dependen enteramente de la documentación oficial, sin
ningún dato en vivo — este conector nunca se ha podido alcanzar desde
ningún entorno de desarrollo de este proyecto. La investigación de esta
ronda (WebSearch + coinalyze.net) no aportó ningún valor numérico real
nuevo. Igual que con Paradex, no se cambió ninguna fórmula sin evidencia;
se añadió un log de diagnóstico (`vertex DIAGNÓSTICO escala
funding_rate/open_interest`) con los primeros valores crudos y ya
convertidos de cada ciclo. Nota práctica: si los últimos despliegues
muestran "vertex SSL" como error conocido, este diagnóstico no producirá
ningún dato hasta que esa conectividad se resuelva primero.

**Verificado** con tests nuevos: `test_finding_13_cex_zero_mark_price.py`
(7 tests: kucoin, mexc, ccxt — normal y con mark_price=0),
`test_finding_13_dex_zero_mark_price.py` (9 tests, uno por cada conector
DEX afectado) y `test_finding_14_and_15_scale_diagnostics.py` (3 tests:
el diagnóstico se genera correctamente y NINGÚN valor/fórmula existente se
ve alterado por él). 84/84 tests pasan en el conjunto completo del
proyecto tras este cambio, sin regresiones.

**Corrección tras el primer despliegue (2026-09-19)**: el primer log de
producción con este fix mostró que Paradex corrió sin ningún error, pero su
diagnóstico del Hallazgo #14 NO apareció en el log. Causa: los dos
diagnósticos nuevos (#14 y #15) se habían logueado a nivel `INFO`, y esta
app nunca llama a `logging.basicConfig()` en ningún sitio — así que el
logger raíz de Python se queda en su nivel por defecto (`WARNING`) y
cualquier `logger.info(...)` de todo el proyecto se descarta en silencio
antes de llegar a los logs de Streamlit Cloud. Se corrigieron ambos a nivel
`WARNING`, el mismo que ya usa el resto de diagnósticos del proyecto (MEXC
#11/#12, OI negativo, GRVT) — no es una excepción nueva, es alinearlos con
el patrón que ya funciona. Vertex, aparte de esto, sigue fallando por SSL
(`ssl.SSLEOFError` contra `gateway.prod.vertexprotocol.com`) antes siquiera
de llegar a ese código, confirmando lo que ya decía el docstring del módulo.

Nota para el futuro: este mismo problema de visibilidad (INFO nunca llega a
los logs) probablemente afecta también a logs ya existentes de sesiones
anteriores que se dejaron a nivel INFO a propósito (ej. la exclusión de
contratos inversos en `cex_kucoin.py`, la exclusión de mercados inactivos en
`dex_lighter.py`) — no se tocaron en este cambio por quedar fuera del
alcance de los Hallazgos #13/#14/#15, pero es el mismo "hueco de
visibilidad de logging" que ya se había marcado como futurible en una
sesión anterior, ahora con una causa raíz concreta identificada
(`logging.basicConfig()` nunca se llama) en vez de solo la sospecha.

**Actualización (2026-09-19, Hallazgo #14 confirmado con datos REALES de
producción, tercer despliegue)**: con el fix de logging ya corregido, el
diagnóstico `paradex DIAGNÓSTICO escala de funding_rate` apareció en el log
real de Streamlit Cloud, repetido en varios ciclos de refresco. Muestras
reales de `funding_rate` crudo: SUI-USD-PERP y ETH-USD-PERP ~0.0001;
NG/MRVL/VVV/kSHIB/XPT/NEAR/PUMP/ETHFI/PYTH/XPL-USD-PERP entre ~0.00003 y
~0.0001; BZ-USD-PERP ~0.000037; XAU-USD-PERP y US100-USD-PERP exactamente
0.00005 en los 3 ciclos muestreados (consistente con un funding-rate
mínimo/floor propio de mercados de índice/materia prima, no con un
problema de escala). Todas estas magnitudes son del orden de 0.003%-0.01%
por periodo de 8h, coincidiendo con la interpretación sin escalar (la que
ya usa el código) y descartando la lectura "número en porcentaje" que
sugería el ejemplo `"funding_rate": "0.3"` de la documentación de
referencia — confirma que ese "0.3" era en efecto el placeholder
autogenerado que se sospechaba. **Hallazgo #14 pasa de SOSPECHOSO a
CONFIRMADO. No hizo falta ningún cambio de fórmula** — el código ya hacía
lo correcto; solo se actualizó el docstring de `dex_paradex.py` con la
evidencia real. El log de diagnóstico se deja tal cual, como registro
histórico de la confirmación (mismo criterio que con los Hallazgos
#11/#12).

Hallazgo #15 (Vertex) sigue SOSPECHOSO — Vertex continúa bloqueado por
`ssl.SSLEOFError` antes de llegar a generar su diagnóstico, así que
todavía no hay datos reales que lo confirmen o lo descarten.

## Resuelto (2026-09-19): Hallazgos #16 y #18 de la auditoría — conversiones `float()` sin proteger en 7 conectores DEX, y símbolos que se perdían en silencio en MEXC/HTX

Se estudiaron los Hallazgos #16, #17 y #18 juntos (petición explícita del
usuario: "Vamos a ver 16, 17 y 18"), y se implementaron el #16 y el #18 tras
su aprobación — el #17 se sacó del lote a petición del usuario ("Quita de
momento la 17") y queda pendiente, sin tocar, para más adelante.

**Hallazgo #16 (CONFIRMADO, y peor de lo que decía la propia auditoría en un
caso)**: la auditoría citaba 7 conectores DEX (GRVT, Lighter, Hyperliquid,
Paradex, Extended, Pacifica, edgeX) donde `funding_rate`/`mark_price` se
convertían con `float()` sin `try/except`, a diferencia de OI/volumen (que
sí lo tenían). Al revisarlos uno a uno:

- **GRVT, Lighter, Paradex, Pacifica**: exactamente como decía la auditoría
  — `mark_price` ya estaba protegido (fix del Hallazgo #13), pero
  `funding_rate` no. Corregido: se descarta solo ese instrumento (con
  diagnóstico), en vez de tirar el conector entero.
- **Hyperliquid**: bug adicional no descrito por la auditoría — `mark_price`
  se convertía DOS VECES por separado: una vez protegida (solo para el
  cálculo de OI) y otra sin proteger, en el campo que de verdad rellena
  `FundingRate.mark_price` — y esa segunda conversión además ignoraba el
  guard de `mark_price == 0` del Hallazgo #13, así que un `markPx` explícito
  de 0 sí se publicaba como precio real. Corregido: una sola conversión
  protegida, su resultado (ya sin el caso `== 0`) se usa para las dos cosas.
- **edgeX**: ninguna de las tres conversiones relevantes (`mark_price`,
  `funding_rate`, `fundingRateIntervalMin`) estaba protegida — peor que la
  descripción de la auditoría, que solo menciona dos campos. Corregido.
- **Extended**: el peor caso de los 7 — NINGUNA de las cuatro conversiones
  (`mark_price`, `openInterest`, `dailyVolume`, `funding_rate`) tenía
  protección, ni siquiera OI/volumen. Corregido.

Para GRVT y edgeX (los dos que usan `ThreadPoolExecutor`), se confirmó que
la conversión sin proteger corría en el hilo PRINCIPAL, dentro del bucle
`for future in as_completed(...)`, tras `future.result()` — una excepción
ahí se escapaba del bucle entero y tiraba todo el conector, no solo el
instrumento problemático, contradiciendo el propio comentario del código
("un instrumento suelto no debe tirar todo el conector").

**Fix, igual en los 7**: cada conversión antes desprotegida ahora tiene su
propio `try/except (TypeError, ValueError)`. Un `funding_rate` no numérico
descarta ESE instrumento (sin funding no hay nada que publicar); un
`mark_price`/OI/volumen/intervalo no numérico solo descarta ese campo
concreto (queda en `None` o cae al valor por defecto), el instrumento se
sigue publicando con el resto de datos. Cada conector deja un diagnóstico
nuevo (`<exchange> DIAGNÓSTICO ... no numérico`) con una muestra acotada de
los valores crudos problemáticos.

**Hallazgo #18 (CONFIRMADO, mismo bug exacto en MEXC y HTX)**: en ambos
conectores, un símbolo/contract_code presente en el feed de funding pero
AUSENTE del todo del feed de metadata (`contract/detail` en MEXC,
`swap_contract_info` en HTX) caía en el mismo `.get(clave, valor_por_defecto
_no_operable)` que un contrato real marcado explícitamente como no operable
— y a diferencia de cada otro motivo de descarte en la misma función
(que sí se registra en `skipped` y sale en el log), este caso se perdía sin
pasar nunca por ahí.

**Fix, igual en los dos**: se distingue explícitamente "símbolo ausente de
metadata" (ahora se registra en `skipped`, visible en el log de "datos
incompletos") de "símbolo presente pero marcado no operable" (sigue
descartándose en silencio como siempre — es el filtro normal, no el bug).

**Hallazgo #17 (pendiente, sacado del lote a petición del usuario)**:
confirmado como hueco arquitectónico real — `core/opportunities.py` agrupa
oportunidades por coincidencia EXACTA de `symbol` (`by_symbol[r.symbol]`,
sin ninguna resolución de alias) y `core/normalize.py` copia `symbol` tal
cual, sin tocarlo; en todo `core/` no existe ninguna tabla de alias
genérica (solo el `XBT→BTC` local de `cex_kucoin.py`). ApeX mantiene
símbolos como "1000PEPE" literales por decisión explícita y documentada. No
se puede confirmar con evidencia si esto está costando parejas reales hoy
sin cruzar los símbolos que trae cada conector en producción — si se
retoma, el criterio sería diagnóstico-primero (loguear símbolos con
prefijo de multiplicador que nunca encuentran pareja) antes de construir
una tabla de alias a ciegas.

**Verificado** con tests nuevos: `test_finding_16_unprotected_float.py` (10
tests, uno o dos por cada uno de los 7 conectores afectados, incluido el
bug incidental de Hyperliquid) y `test_finding_18_missing_from_metadata.py`
(4 tests: símbolo ausente se registra, símbolo presente-pero-no-operable
sigue en silencio, para MEXC y HTX). 98/98 tests pasan en el conjunto
completo del proyecto tras este cambio, sin regresiones.

**Corrección tras el primer despliegue real de este fix (2026-09-19)**: el
primer log de producción con el diagnóstico del Hallazgo #18 activo mostró
que SÍ está capturando casos reales — 18 símbolos en MEXC, 4 contract_code
en HTX — pero reveló una interacción no prevista: en MEXC, 10 de esos 18
eran justo los MISMOS contratos inversos/coin-margined que el Hallazgo #12
ya excluye a propósito (BTC_USD, ETH_USD, XRP_USD, SOL_USD, SUI_USD,
ADA_USD, DOGE_USD, AVAX_USD, LTC_USD, LINK_USD) — como se excluyen ANTES de
entrar a `detail_by_symbol`, también "estaban ausentes" según el chequeo
del #18, y salían DOS VECES en el log con mensajes contradictorios (una vez
"excluido por inverso", otra "ausente sin explicación"). **Fix**: el
chequeo del #18 ahora consulta primero el conjunto de inversos ya
excluidos y no los vuelve a registrar. Los 8 símbolos restantes de ese
mismo log (USDGO_USDT, MX_USDT, USDE_USDT, WBTC_USDT, STETH_USDT,
MXSOL_USDT, TON_USDT, USD1_USDT) sí eran el caso real que el hallazgo
pretendía capturar y siguen reportándose con normalidad. En HTX, los 4
`contract_code` ausentes (ETH-USDT-260925, BTC-USDT-261002,
BTC-USDT-260925, ETH-USDT-261002) resultaron ser contratos de FUTUROS con
vencimiento fijo (trimestrales, por el sufijo con fecha) mezclados en el
mismo feed de funding que los perpetuos — `swap_contract_info` documenta
solo perpetuos, así que es coherente y esperado que no los conozca; no hizo
falta ningún cambio de código ahí, solo se documentó la evidencia real en
el docstring del módulo.

Verificado con 2 tests nuevos en `test_finding_18_missing_from_metadata.py`
(un inverso ya excluido no se duplica en el log; un símbolo genuinamente
sin explicación conocida se sigue reportando). 107/107 tests pasan en el
conjunto completo del proyecto, sin regresiones.

## Resuelto (2026-09-19): Hallazgos #19, #20, #21 y #22 de la auditoría — los cuatro de severidad baja/cosmética

Se continuó con "Seguimos con 19,20,21 y 22. Dejamos el 17 de momento" — el
Hallazgo #17 sigue aparcado (ver sección anterior), estos cuatro sí se
implementaron.

**Hallazgo #19 (CONFIRMADO)**: `core/history.py`, `historical_apr()` tenía
`WindowStat(apr_avg=avg if enough else avg, ...)` — las dos ramas del
if/else eran idénticas, así que `apr_avg` nunca llegaba a ser `None` pese a
que el propio campo está documentado como "`None` si no hay histórico
suficiente". Inofensivo hasta ahora porque los dos sitios que lo leen
(`pages/1_Funding_Rates.py`, `cli.py`) ya comprueban `enough_history` por su
cuenta antes de usar `apr_avg`, pero era una trampa para cualquier código
futuro que confiara solo en la documentación del campo. **Fix**: la rama
`else` ahora devuelve `None` de verdad.

**Hallazgo #20 (CONFIRMADO el desajuste, probablemente código muerto hasta
ahora)**: `connectors/dex_risex.py`, `_strip_quote_suffix()` (fallback de
última instancia, solo se alcanza si `_base_symbol()` no encuentra ningún
candidato) esperaba sufijos con guion (`"-USDC"`, `"-PERP"`...), pero el
formato CONFIRMADO en vivo para el par completo usa barra (`"BTC/USDC"` —
el mismo conector ya lo documentaba y ya lo manejaba correctamente en
`_base_symbol()`, solo este helper de repuesto se había quedado con la
suposición vieja). **Fix**: se prueba primero el separador confirmado
(barra), el guion se deja como fallback adicional por si el campo de origen
aquí (`display_name`/`config.name`, no necesariamente el mismo que
`base_asset_symbol`) llegara a usar otro formato algún día.

**Hallazgo #21 (CONFIRMADO, severidad baja)**: `connectors/dex_variational.py`
"deshace" el APY que reporta Variational a una tasa por intervalo, para que
`core/normalize.py` la re-anualice sin duplicar el anualizado — una
cancelación algebraica que solo es válida si el `HOURS_PER_YEAR` que usa
cada lado del cálculo es EXACTAMENTE el mismo valor. Antes cada archivo
definía su propia constante por separado (ambas en 8760, pero sin nada que
las mantuviera iguales si una cambiaba sin la otra) — no era una regla
exigida por el código, solo una coincidencia que se sostenía porque nadie
había tocado ninguna de las dos. **Fix**: `dex_variational.py` ahora
importa `HOURS_PER_YEAR` directamente de `core/normalize.py` en vez de
redefinirla — la cancelación queda garantizada por construcción, no por
coincidencia.

**Hallazgo #22 (CONFIRMADO, cosmético)**: el docstring de
`dex_variational.py` decía "el techo teórico de APY bajo el límite
'2%/hora' es 0.02 × 8760 = 17.52 (1752%)" — la cuenta real da 175.2, no
17.52 (un error de un orden de magnitud), o sea 17520%, no 1752%. Ningún
código dependía de ese número, solo el comentario. **Fix**: corregido el
cálculo en el comentario.

**Verificado** con tests nuevos: `test_finding_19_20_21.py` (7 tests: #19
con y sin histórico suficiente; #20 el separador confirmado, el fallback de
guion conservado, y un caso end-to-end en RiseX sin `base_asset_symbol`;
#21 confirma que `dex_variational.HOURS_PER_YEAR` es el MISMO objeto que
`core.normalize.HOURS_PER_YEAR`, y un end-to-end con dos intervalos
distintos que confirma que el APR final sigue siendo `apy × 100`). El #22
no tiene código que testear (solo un comentario). 105/105 tests pasan en el
conjunto completo del proyecto tras este cambio, sin regresiones.

### Hallazgo nuevo (2026-09-19, no viene de la auditoría): `gate` nunca traía Open Interest del top N — mismo hueco de ccxt que Aster, pero SÍ se puede arreglar

Verificando el export CSV completo del Ranking que pidió el usuario ("comprueba
que todo esté correcto aquí también"), todos los checks automatizados (mismo
exchange long/short, Price Spread implausible, OI $0 confirmado, piso de
liquidez, Cuello de botella/Lado coherentes con las piernas, Spread APR = Short
− Long) salieron limpios en las 683 filas. Las dos filas que parecían raras a
simple vista se confirmaron con datos en vivo, no eran bugs:

- **EMBER (gate long, rank #1 del ranking, Spread APR 17.563,8%)**: confirmado
  contra la API real de gate.io que EMBER_USDT tiene intervalo de funding de 1h
  y un **cap del propio exchange del 2%/hora** en el funding rate
  (`funding_rate_limit: "0.02"` en `/api/v4/futures/usdt/contracts/EMBER_USDT`).
  -17.520% es exactamente ese cap anualizado (0,02 × 8760 × 100) — la tasa
  cruda tocó su techo en el momento del snapshot (`last_funding_rate: "-0.02"`
  confirmado también vía `contract_stats`, ver más abajo). No es un bug de
  escala del proyecto, es un dato real y extremo — y volátil por el intervalo
  de 1h (para cuando se verificó ya había bajado a -0,84%/hora).
- **ZEC (hyperliquid long, OI ~$800M)**: confirmado por Coinglass que ZEC tiene
  ~$3,15B de Open Interest agregado en todos los exchanges en este momento —
  $800M en uno de los venues más grandes (Hyperliquid) es plausible, no un
  error de parseo.

Investigando por qué **ninguna** fila con `gate` como pierna traía OI Depth
pese a que `gate` está en `CEX_FACTORY_BY_NAME` (debería pedirse para el top
10 — `OI_ENRICH_TOP_N` en `pages/1_Funding_Rates.py`) se encontró un hueco
real: **`ccxt.gate().has['fetchOpenInterest']` es `False`** — confirmado en
vivo, exactamente el mismo problema ya documentado para Aster más arriba en
esta misma sección. `CexConnector.fetch_open_interest_usd()` llama a
`self._client.fetch_open_interest(raw_symbol)`, que para gate **siempre**
lanza `NotSupported`, sea cual sea el símbolo — no es un fallo puntual, es
estructural. En el CSV verificado, las dos únicas piernas de `gate` dentro del
top 10 (EMBER en rank #1, COOL en rank #6) salían las dos con OI "—" por este
motivo.

A diferencia de Aster (donde se probó también un bypass REST directo y falló
con 400 incluso en un símbolo real y activo como BTCUSDT — ver más arriba, así
que se aceptó como límite real del proyecto), aquí el bypass **sí funciona**:
el REST propio de gate.io expone el Open Interest ya resuelto en USD —
confirmado en vivo contra
`GET /api/v4/futures/usdt/contract_stats?contract=EMBER_USDT&interval=5m&limit=1`
→ `open_interest_usd: 185664.336` (con `last_funding_rate: "-0.02"` en la misma
respuesta — confirmación cruzada contra el propio EMBER del punto anterior, es
el mismo contrato real).

**Fix**: `CexConnector._fetch_gate_open_interest_usd()` (nuevo, en
`connectors/cex_ccxt.py`) — cuando `self.ccxt_id == "gate"`,
`fetch_open_interest_usd()` bypasea `self._client.fetch_open_interest()` (que
fallaría siempre) y llama directamente a `contract_stats`, resolviendo primero
el id nativo del contrato (ej. `"EMBER_USDT"`, con guion bajo) a partir del
símbolo unificado de ccxt (ej. `"EMBER/USDT:USDT"`) vía
`self._client.market(raw_symbol)["id"]` — confirmado leyendo
`parse_contract_market()` en el propio código fuente de ccxt, que asigna
`market["id"] = name` sin transformar, y ccxt ya tiene los markets cacheados
tras el `fetch_funding_rates()` masivo, así que esto no cuesta ninguna llamada
de red aparte. Igual que el resto del proyecto: si `contract_stats` no trae
filas o falta `open_interest_usd`, se reporta como error explícito por símbolo
(visible en el expander de diagnóstico), nunca como un "—" mudo ni como un
0.0 inventado. El resto de `CEX_FACTORY_BY_NAME` (binance/bybit/okx/
bitget/aster) no se ve afectado — el bypass solo aplica a `gate`.

**Verificado** con `test_gate_open_interest_rest_bypass.py` (6 tests: el
bypass evita por completo `ccxt.fetch_open_interest()` para gate, el id nativo
se resuelve vía `self._client.market()` y no por parseo del símbolo, filas
vacías y campo `open_interest_usd` ausente se reportan como error explícito
en vez de perderse en silencio, un fallo de red se reporta por símbolo sin
tirar el resto del top N, y el resto de exchanges de `CEX_FACTORY_BY_NAME`
—probado con bitget— sigue usando el camino normal de ccxt sin verse
afectado). 113/113 tests pasan en el conjunto completo del proyecto tras este
cambio, sin regresiones.

#### Corrección sobre el fix anterior (2026-09-20): el bypass sí llegó a producción, pero fallaba un paso antes — `self._client.markets` nunca estaba cargado

El fix de arriba se desplegó, y el propio panel de diagnóstico "OI Depth no
disponible" de la app en producción lo confirmó indirectamente: el mensaje de
error para `gate` cambió de `"NotSupported: gate fetchOpenInterest() is not
supported yet"` (el hueco original de ccxt, ya evitado) a
**`"ExchangeError: gate markets not loaded"`** — para **las 125 piernas de
`gate`** del export CSV completo del Ranking, sin ninguna excepción. Ese
cambio de mensaje ya era la prueba de que el bypass nuevo SÍ estaba
ejecutándose (el mensaje viejo es estructuralmente imposible de producir con
el código nuevo, que nunca llama a `fetch_open_interest()` de ccxt para
gate) — pero fallaba en el paso siguiente.

**Causa raíz**: el docstring original de `_fetch_gate_open_interest_usd()`
asumía que `self._client.markets` ya estaría cacheado "tras el
`fetch_funding_rates()` masivo, no hace falta una llamada de red aparte solo
para esto" — cierto para el conector que trae los funding rates del universo
completo, pero **falso para esta ruta de llamada en concreto**:
`core/opportunities.py::fetch_oi_for_targets()` (línea ~183) no reutiliza ese
conector — construye uno **nuevo** desde cero solo para pedir OI Depth del top
N, vía `factory()` (`CEX_FACTORY_BY_NAME`). Ese `ccxt.gate()` nuevo nunca
había llamado a `load_markets()`, así que `self._client.market(raw_symbol)`
lanzaba el error real de ccxt cuando `self.markets` está vacío.

**Reproducido en local, sin red, con un cliente `ccxt.gate()` de verdad** (no
un mock que se salte el problema):

```python
>>> import ccxt
>>> g = ccxt.gate({"enableRateLimit": True})
>>> g.markets
None
>>> g.market("EMBER/USDT:USDT")
ExchangeError: gate markets not loaded
```

Mensaje idéntico, carácter por carácter, al del panel de diagnóstico en
producción — confirmación cruzada de que es exactamente esta causa y no otra.

**Fix**: una línea, `self._client.load_markets()` antes de
`self._client.market(raw_symbol)` en `_fetch_gate_open_interest_usd()`. No
añade una llamada de red por símbolo dentro del mismo ciclo de OI: leyendo
`Exchange.load_markets()` en el propio ccxt instalado, `load_markets(reload=
False)` (el valor por defecto) devuelve `self.markets` directamente si ya
está poblado, sin volver a pedir nada — solo la primera pierna de `gate` del
batch paga la llamada real, el resto la reutiliza gratis.

**Verificado** con `test_gate_load_markets_fix.py` (3 tests nuevos): (1)
reproduce el error real de producción con un `ccxt.gate()` de verdad sin
`load_markets()`, carácter por carácter igual al del panel de diagnóstico;
(2) confirma que `_fetch_gate_open_interest_usd()` llama a `load_markets()`
antes que a `market()`, no después; (3) end-to-end con un conector recién
construido (`markets` vacío, igual que llega desde `factory()`) y
`fetch_markets()`/`fetch_currencies()` de red sustituidos por un fixture fijo,
confirmando que `fetch_open_interest_usd()` ya no revienta. 126/126 tests
pasan en el conjunto completo del proyecto tras este cambio, sin regresiones.

**Confirmado en producción (2026-09-20)**, con un export CSV nuevo generado
después del redeploy con este fix: las dos piernas de `gate` dentro del top
10 (EMBER, rank #1: OI $188.520,33; LAPTOP, rank #7: OI $736.790,88) ya traen
Open Interest real en vez de "—". El panel de diagnóstico "OI Depth no
disponible" de la app ya no muestra ningún error de `gate` — solo quedan los
de `aster` (hueco distinto, ya documentado y aceptado más arriba en esta
misma sección), confirmando que el problema de `gate` quedó resuelto del
todo. Las otras 116 piernas de `gate` del export sin OI son las que caen
fuera del top N enriquecido (comportamiento esperado, no relacionado con este
bug — ver el docstring de `fetch_open_interest_usd()`, que deliberadamente no
pide OI para el universo completo).

### Hallazgo #17 (auditoría 2026-09-19) — RESUELTO parcialmente a propósito: sin tabla de alias para símbolos con prefijo de multiplicador ("1000PEPE")

La auditoría original lo marcó "SOSPECHOSO": ningún sitio de `core/` tiene una
tabla de alias de símbolos (solo el caso puntual XBT→BTC en `cex_kucoin.py`),
así que un mismo activo listado como "1000PEPE" en un exchange y "PEPE" en
otro nunca se emparejaría en `compute_opportunities()` (que agrupa por símbolo
EXACTO) — oportunidades reales perdidas en silencio, sin error ni aviso.

**Confirmado con datos reales** verificando el mismo export CSV del Ranking
que motivó el resto de esta sección: PEPE (`okx +10,9%` / `mexc +31,0%`) y
1000PEPE (`edgex +10,9%` / `extended +30,7%`) son el mismo activo — APR casi
idéntico en las dos piernas de cada fila (el funding rate en % no depende de
si el contrato agrupa 1 o 1000 tokens) — pero salían como dos oportunidades
separadas sin ninguna relación aparente entre sí.

**El riesgo real, y por qué no es un simple alias**: el propio mark_price de
la pierna con multiplicador normalmente está cotizado a la escala del LOTE
completo (1000 PEPE), no del token suelto — confirmado así para ApeX
contrastando `openInterest × markPrice` contra un notional plausible (ver
`connectors/dex_apex.py`). Si solo se hiciera un alias de símbolo sin tocar
nada más, el Price Spread de esas filas saldría con un número inventado
(~99.900%, o peor, algo pequeño pero falso si por casualidad cayera bajo
`IMPLAUSIBLE_SPREAD_PCT`) — justo la clase de bug que el resto de esta sección
lleva toda la auditoría evitando. Confirmarlo en vivo, exchange por exchange
(edgeX, extended, MEXC, Bitget, Gate...), para poder ajustar el precio con
seguridad es un trabajo bastante mayor que el propio alias — se le planteó la
disyuntiva al usuario y se decidió ir por la opción segura ahora, dejando el
ajuste de precio para más adelante si hace falta.

**Fix (`core/normalize.py`)**: `_canonical_symbol()` recorta un prefijo de
multiplicador de una lista CERRADA de prefijos conocidos (`1000`, `10000`,
`100000`, `1000000`, `1M` — los mismos que ya aparecen en producción en este
proyecto: 1000000MOG, 1000RATS, 1000PEPE, 1000BONK, 1MBABYDOGE, 1000CHEEMS,
1000SHIB, 1000FLOKI, ver el export CSV), NO un regex genérico tipo `^\d+` —
símbolos reales de ese mismo universo como "0G" (0G Labs) o "2Z" empiezan por
dígito sin ser un multiplicador, y un regex genérico los rompería (verificado:
ninguno de esos dos coincide con la lista cerrada). Se aplica una sola vez, en
`normalize()` — el único funnel por el que pasan todos los `FundingRate` de
todos los conectores — así que ningún conector nuevo puede olvidarse de
aplicarlo. El símbolo canónico (sin prefijo) es el que ve
`compute_opportunities()`, y el multiplicador queda registrado aparte en
`NormalizedRate.symbol_multiplier` (1.0 si no tenía prefijo).

`core/scoring.py::price_spread()` usa ese campo para negarse a comparar el
mark_price de dos piernas con `symbol_multiplier` distinto — devuelve
`spread_pct=None` directamente en vez de calcular nada, así que esas filas
salen con Price Spread "—" en la interfaz (mismo criterio que mark_price
ausente o IMPLAUSIBLE_SPREAD_PCT). El Spread APR, que es el dato principal del
ranking, no se ve afectado en ningún caso.

**Verificado** con `test_finding_17_symbol_multiplier.py` (10 tests):
`_canonical_symbol()` recorta bien los 8 prefijos vistos en producción, prefiere
el prefijo más largo cuando hay solape (1000000 sobre 1000), y NO toca "0G"/
"2Z"/"4STOCK"; `normalize()` deja el símbolo canónico y el multiplicador
correctos; `price_spread()` da `None` cuando los multiplicadores difieren
(incluso si el ratio de precios "cuadra" con lo esperado — no se asume, se
exige confirmación en vivo que hoy no existe), sigue calculándose con
normalidad si las dos piernas comparten el mismo multiplicador, y no cambia
nada cuando ninguna de las dos tiene prefijo; y un caso end-to-end que
confirma que PEPE (okx/mexc) y 1000PEPE (edgex/extended) ahora SÍ se
emparejan en una única oportunidad vía `compute_opportunities()`. 123/123
tests pasan en el conjunto completo del proyecto tras este cambio, sin
regresiones.

**Confirmado en producción (2026-09-20)**, con un export CSV nuevo del
Ranking generado tras el deploy: en las 690 filas del export no queda ni un
solo símbolo con prefijo de multiplicador conocido (`1000X`, `1M X`...) sin
fusionar — comprobado programáticamente contra la lista cerrada de prefijos
de `_canonical_symbol()`. El caso PEPE que motivó este hallazgo ya sale como
una única fila fusionada (antes salían "PEPE" y "1000PEPE" como dos
oportunidades separadas), con Price Spread en blanco — exactamente el
comportamiento esperado de la opción segura elegida.

## Importante sobre dónde correr esto

Este proyecto se ha construido en un entorno cloud con acceso a internet restringido
(solo puede alcanzar registros de paquetes como PyPI, no las APIs de los exchanges).
Por eso el desarrollo aquí se prueba con `--offline` usando fixtures grabadas.

**Para usarlo con datos reales, corre este proyecto en tu propio ordenador o servidor**,
donde tengas salida a internet normal hacia Binance, Bybit, Hyperliquid, etc.

## Instalación

```bash
pip install -r requirements.txt
cp .env.example .env   # rellena tus claves si vas a usar ejecución más adelante
```

## Uso (CLI)

```bash
# datos en vivo (requiere internet real hacia los exchanges)
python cli.py

# modo demo con datos grabados, para probar sin depender de la red
python cli.py --offline
```

## Uso (web)

```bash
streamlit run streamlit_app.py
```

## Desplegar en Streamlit Cloud

Streamlit Cloud SÍ tiene internet normal hacia los exchanges (a diferencia del
entorno de desarrollo donde se construyó esto) — una vez desplegada, la app
puede usar el modo "En vivo" de verdad.

Ver la guía paso a paso en el mensaje de Claude, o resumen rápido:

1. Sube este proyecto a un repositorio de GitHub (nuevo repo → "uploading an
   existing file" → arrastra la carpeta).
2. Entra en share.streamlit.io con tu cuenta de GitHub.
3. "New app" → selecciona el repo → main file: `streamlit_app.py` → Deploy.

**Limitación a tener en cuenta:** el disco de la app en el plan gratuito de
Streamlit Cloud no es permanente — la base de datos de histórico
(`data/funding_history.db`) puede reiniciarse en cada redeploy o reinicio del
contenedor. Para un histórico de verdad a largo plazo, en algún momento
convendrá mover esa tabla a una base de datos externa (ej. Supabase, que
tiene un plan gratuito) en vez del SQLite local.
