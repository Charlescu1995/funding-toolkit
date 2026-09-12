# Funding Toolkit

Herramienta propia de funding rates (cripto + RWA), construida combinando lo mejor de
ProFunding, Loris Tools y el selector delta-neutral de John5Cripto.

## Estado del build

Vamos construyéndola paso a paso. Progreso:

- [x] Paso 1 — Arquitectura del proyecto y modelo de datos común
- [x] Paso 2 — Conectores de datos: 8 CEX vía ccxt (Binance, Bybit, OKX, Bitget, KuCoin, Gate, MEXC, HTX) + 10 DEX (Hyperliquid, Lighter, Paradex, Extended, Pacifica, Aster, edgeX, GRVT, Variational y RiseX vía API directa/ccxt) — los primeros 8 DEX confirmados devolviendo datos reales en producción; Variational y RiseX son los dos más recientes, aún sin contrastar contra un despliegue real (ver más abajo)
- [x] Paso 3 — Normalización de intervalos y cálculo de APR anualizado
- [x] Paso 4 — Snapshots históricos (SQLite) → APR histórico real 1h/24h/7d/30d
- [x] Paso 5 — Consistency Score y OI Depth (con fallback contratos×mark_price para exchanges que no dan el USD directo)
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

### DEX nuevos, tercera tanda (Variational, RiseX) — pendientes de verificar en vivo

Estos dos, a diferencia de la segunda tanda (Aster/edgeX/GRVT), se construyeron sin
poder probar el redespliegue real todavía en el momento de escribir esto — todo lo de
abajo está respaldado por datos en vivo (se pudo llamar a las APIs públicas de ambos
directamente desde este entorno vía fetch HTTP, aunque no hay conexión de red directa
normal), pero sin el contraste final contra la interfaz oficial de cada exchange que sí
se pudo hacer con Lighter y GRVT. Ambos son, en diseño, los conectores más simples de
todo el proyecto: un único endpoint bulk trae funding rate + mark price + open interest
de todos los mercados de golpe — sin pool de hilos, sin llamada aparte a metadata.

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

**Asunciones sin verificar, a revisar en cuanto haya un despliegue real** (mismo
espíritu que se hizo con Lighter/GRVT — comparar al menos un símbolo, idealmente BTC,
contra la interfaz oficial de cada exchange):

- **Variational — Open Interest**: se comprobaron en vivo BTC (mark_price ≈ 77,396,
  `long_open_interest` ≈ 80.66M) y PEOPLE (mark_price ≈ 0.008, `long_open_interest` ≈
  3,697). 80.66 millones de BTC de open interest es físicamente imposible (el supply
  total de BTC ronda los 19.5M), así que se descartó la lectura literal "unidades del
  activo base" y se asumió que el campo ya viene en USD (igual que `openInterest` en
  Extended) — se usa directamente, sin multiplicar por mark price. Si el OI de
  Variational sale desproporcionadamente bajo en el ranking, esta es la primera
  sospechosa.
- **RiseX — Open Interest**: a diferencia de Variational, aquí la ambigüedad es de la
  propia documentación (no hay evidencia en ningún sentido) — la spec documenta "18
  decimales" para los campos de funding pero no dice nada sobre la escala o
  denominación de `open_interest` ni `mark_price`. Se aplicó la asunción por defecto
  usada para Hyperliquid/Lighter/Paradex/Pacifica: unidades del activo base,
  convertidas a USD multiplicando por mark price. Si resulta que RiseX en realidad ya
  da el OI en USD (como Extended o Variational), el OI de RiseX saldría duplicado por
  error — primera cosa a comprobar.
- **RiseX — escala de `current_funding_rate`**: la documentación dice "decimal string,
  18 decimales", interpretado aquí como "hasta 18 decimales de precisión en el string"
  (no como un entero de punto fijo que haya que dividir entre 1e18), en base a que el
  propio ejemplo de la spec ya es una fracción decimal legible. Sin contrastar contra
  una respuesta real todavía — si el APR de RiseX sale con un orden de magnitud
  absurdo, esto es lo primero a revisar.

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
