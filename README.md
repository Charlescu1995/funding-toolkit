# Funding Toolkit

Herramienta propia de funding rates (cripto + RWA), construida combinando lo mejor de
ProFunding, Loris Tools y el selector delta-neutral de John5Cripto.

## Estado del build

Vamos construyéndola paso a paso. Progreso:

- [x] Paso 1 — Arquitectura del proyecto y modelo de datos común
- [x] Paso 2 — Conectores de datos: 8 CEX (Binance, Bybit, OKX, Bitget, Gate vía ccxt; KuCoin/MEXC/HTX con conector propio — ccxt no soporta `fetchFundingRates()` para estos tres, ver sexta tanda más abajo) + 15 DEX (Hyperliquid, Lighter, Paradex, Extended, Pacifica, Aster, edgeX, GRVT, Variational, RiseX, Backpack, Nado, Hibachi, Vertex y ApeX vía API directa/ccxt) — los 13 primeros DEX confirmados devolviendo datos reales en producción (Variational: 547 pares, RiseX: 30 pares tras dos rondas de fix, Backpack/Nado/Hibachi sin ningún error en su primer despliegue real, ver más abajo); Vertex y ApeX son la quinta tanda — ApeX confirmado en vivo, Vertex construido solo a partir de documentación (no se pudo alcanzar su API desde este entorno) y CONFIRMADO en producción bloqueado por red (mismo patrón que Binance/Bybit — ver sección dedicada). Se investigó también Drift Protocol y se descartó: su API quedó inutilizable tras el hackeo de ~$285-295M de abril 2026 y sus dominios oficiales redirigen a un fork no oficial ("Velocity Exchange") que no es Drift — ver sección dedicada
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

**Pendiente, NO tocado en esta ronda**: la pierna de GRVT sigue mostrando
sistemáticamente APR ~0.0% en las capturas del usuario (`funding_rate_8h_curr`
× `CENTIBEEPS_TO_DECIMAL`, ÷1e6) — el mismo patrón de "posible sobre-escalado"
que ya vimos tres veces con otros campos, pero esta vez sin un valor crudo
todavía confirmado en un log real para probarlo. Se añadió el valor crudo de
`funding_rate_8h_curr` (y el calculado) al mismo diagnóstico, así que el
próximo log ya lo va a traer sin pedir nada nuevo. No se cambió la fórmula a
ciegas porque, a diferencia de los tres campos de arriba, aquí el riesgo de
equivocarse en la otra dirección (pasar de "demasiado pequeño" a "un millón
de veces demasiado grande") es peor que dejarlo pendiente un despliegue más.

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

**Hallazgo secundario, no tocado todavía (bajo impacto)**: el conector de
Lighter (`connectors/dex_lighter.py`) guarda `raw_symbol=str(market_id)` —
un ID numérico interno ("20", "86"...) en vez de un ticker legible. No
causa el bug de arriba (el campo `symbol`, que es el que se usa para
emparejar oportunidades, sí viene correcto — "BERA", "APEX"), pero hace
que el nuevo panel de diagnóstico enseñe cosas como `raw=20` para Lighter,
que no dice nada por sí solo. Cosmético por ahora, pendiente si molesta en
la práctica.

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
