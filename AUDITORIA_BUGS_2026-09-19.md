# Auditoría de bugs — Funding Toolkit (2026-09-19)

Barrido completo del código (todos los conectores CEX/DEX + el núcleo de
`core/` + la página de Streamlit) buscando fallos lógicos, no solo estilo.
Cada hallazgo está marcado como **CONFIRMADO** (demostrable leyendo/probando
el código, sin necesitar datos en vivo) o **SOSPECHOSO** (huele mal, pero
hace falta un dato real de producción para confirmarlo — mismo criterio que
se ha usado todo este proyecto: nunca inventar, siempre confirmar).

No se ha tocado ni una línea de código todavía — esto es solo el listado,
para decidir juntos por dónde empezar.

---

## 🔴 Crítico

### 1. La app en producción arranca en modo Demo (datos falsos/viejos), no en vivo — CONFIRMADO

**`pages/1_Funding_Rates.py:126-133`**

```python
source = st.radio(
    "Origen",
    ["Demo (offline)", "En vivo"],
    index=0,
    help="Este entorno de desarrollo no tiene salida a internet hacia los exchanges — "
         "usa Demo aquí. En vivo funcionará cuando esto corra en un servidor con internet normal.",
)
offline = source.startswith("Demo")
```

`index=0` hace que el radio arranque siempre en "Demo (offline)". Ese texto
de ayuda fue escrito pensando en ESTE sandbox de desarrollo (que sí está
bloqueado a internet) — pero el mismo código, con el mismo `index=0`, es el
que se ha desplegado a Streamlit Cloud. No hay ningún `if` en todo el
proyecto (comprobado con grep en busca de `os.environ`/`getenv`/detección de
entorno) que cambie ese valor por defecto en producción.

`offline=True` hace que `core/data_service.py` llame a
`build_connectors(offline=True)`, que devuelve SOLO 3 conectores de fixture
(`binance_offline()/bybit_offline()/hyperliquid_offline()` leyendo JSON de
`tests/fixtures/`) — ninguno de los 20+ exchanges reales, con números que
pueden llevar quién sabe cuánto tiempo congelados.

**Por qué importa**: cualquiera que abra la app en una pestaña nueva (tú
mismo si no lo tienes memorizado, o cualquier otra persona con el link) ve
"oportunidades de arbitraje" con datos de mentira, sin ningún aviso de que
son de mentira más allá de la etiqueta pequeña del radio button — para una
herramienta que existe para decidir dónde meter dinero de verdad, esto es
serio. Es prácticamente seguro que TÚ ya sabes cambiarlo a "En vivo" cada
vez (por eso todos los logs que has pegado hasta ahora muestran fetches
reales), pero el comportamiento por defecto sigue siendo el peligroso.

**Fix directo**: cambiar `index=0` por `index=1` (que arranque en "En
vivo"), y quizás mover el modo Demo a una opción secundaria más explícita.

---

### 2. Dos piernas del mismo exchange pueden aparecer como una "oportunidad" real — el filtro existe pero nunca se aplica — CONFIRMADO

**`core/opportunities.py:61-63`** (la causa) + **`pages/1_Funding_Rates.py:211-244`** (el diagnóstico que detecta pero no filtra)

```python
long_leg = min(group, key=lambda r: r.apr_pct)
short_leg = max(group, key=lambda r: r.apr_pct)
```

Nunca comprueba `long_leg.exchange != short_leg.exchange`. Ya sabíamos esto
(está anotado en el propio código desde el 2026-09-18, con 121 filas reales
tipo SSV/CIFR en okx-vs-okx/gate-vs-gate, todas con Spread APR = 0.0%) y
decidimos dejarlo "en observación" porque el panel de diagnóstico llevaba
muchos despliegues seguidos sin marcar ninguna. Pero mirando el código de
`pages/1_Funding_Rates.py` línea por línea, hay algo que no sabíamos:

```python
same_exchange_pairs = [o for o in opportunities if o.long_exchange == o.short_exchange]
# ... nunca se reasigna `opportunities` para quitarlas, a diferencia de
# implausible_pairs, dead_liquidity y low_liquidity, que SÍ se quitan justo
# después de calcularse (líneas 253-254, 277-278, 299-300)
```

O sea: aunque el diagnóstico las detecte, **nunca se sacan del Ranking**.
Si alguna vez aparecen, se quedan arriba en la tabla principal tal cual,
compitiendo por posición con oportunidades reales.

Además, probé en Python el comportamiento exacto de `min()`/`max()` con
empate:

```python
>>> group = [fila_bitget_A, fila_bitget_A_duplicada]  # mismo apr_pct
>>> min(group, key=...) is max(group, key=...)
True
```

Cuando hay exactamente dos filas con el `apr_pct` idéntico, `long_leg` y
`short_leg` acaban siendo **literalmente el mismo objeto** — así es como
salen los casos de Spread APR = 0.0% que ya viste. Pero si algún exchange
alguna vez aporta dos filas DISTINTAS para el mismo símbolo normalizado con
APR diferente (dos contratos reales colapsando al mismo símbolo — el propio
comentario del código ya sospecha de esto en MEXC/KuCoin, o un perpetuo +
un futuro con vencimiento en ccxt), el Spread APR saldría **distinto de
cero** y esa fila se colaría en el Ranking principal como si fuera un
arbitraje cruzado real, sin ningún aviso salvo abrir el expander de
diagnóstico al final de la página.

**Por qué esto cambia la decisión anterior**: dejarlo "en observación"
asumía que, si nunca pasa, no hace daño. Pero el código confirma que SI
pasa, no hay red de seguridad — se ve exactamente igual que una oportunidad
real en la tabla principal. Dado que ya tenemos prueba de que el caso
Spread=0% ocurre en producción (121 filas), y que el mecanismo de fondo
(min/max sin filtrar por exchange) es el mismo para el caso peligroso
(Spread≠0%), esto ya no parece tan "hipotético" como pensábamos.

**Fix directo**: añadir `opportunities = [o for o in opportunities if o.long_exchange != o.short_exchange]` justo después de calcular `same_exchange_pairs`, mismo patrón que los otros tres filtros.

---

## 🟠 Alto

### 3. MEXC y HTX no tienen la guardia de OI negativo que se le añadió a KuCoin — mismo bug, sin arreglar aquí — CONFIRMADO (el hueco) / SOSPECHOSO (si dispara)

**`connectors/cex_mexc.py:201-208`**, **`connectors/cex_htx.py:225-232`**

Ninguno de los dos comprueba `if open_interest_usd < 0` tras calcularlo —
a diferencia de `cex_kucoin.py`, que ahora sí lo hace (línea 273-281) justo
por el bug real de SOLUSDM. La fórmula de MEXC (`holdVol × contractSize ×
fairPrice`) tiene la misma forma que la de KuCoin que dio negativo; la de
HTX pasa un campo (`value`) directo de la API sin ninguna validación.

**Confirmaría/refutaría**: una captura en vivo de `/contract/detail` +
`/contract/ticker` de MEXC y `/swap_open_interest` de HTX buscando algún
valor negativo — no lo he podido comprobar desde aquí.

**Fix directo**: copiar la misma guardia (descartar a `None` + loggear el
payload crudo) en los tres conectores restantes que calculan OI con una
fórmula parecida (MEXC, y como red de seguridad también en `cex_ccxt.py`
para el `fetch_open_interest_usd` del top-10).

### 4. `cex_ccxt.py` guarda `next_funding_time` como texto, no como fecha — CONFIRMADO (el tipo), pero inofensivo ahora mismo

**`connectors/cex_ccxt.py:166,177`** vs **`connectors/base.py:34`** (`next_funding_time: Optional[datetime] = None`)

ccxt devuelve `fundingDatetime` como string ISO-8601, y se guarda tal cual
en un campo declarado como `datetime`. Comprobé si algo lo usa —
**no lo consume nada en todo el proyecto** (ni `core/`, ni `pages/`, ni
`cli.py`), así que hoy no rompe nada. Pero es una trampa para el futuro: si
algún día alguien usa este campo (para mostrar "próximo funding en Xh",
por ejemplo) para binance/bybit/okx/bitget/gate/aster, explota con un
`TypeError` en cuanto se intente restar o formatear como fecha.

**Fix directo**: parsear `fundingDatetime` con `datetime.fromisoformat()` en `cex_ccxt.py`, o quitar el campo hasta que haga falta de verdad.

### 5. El intervalo de funding de 6 CEX (vía ccxt) está fijo, no por símbolo — reconocido en el propio comentario, pero nunca resuelto — SOSPECHOSO

**`connectors/cex_ccxt.py:27-45`**

```python
DEFAULT_INTERVAL_HOURS = {"binance": 8, "bybit": 8, "okx": 8, "bitget": 8, "gate": 8, "aster": 8}
```

El propio comentario ya avisa: "ccxt no siempre expone el intervalo real
por símbolo (algunos exchanges lo variaron por par en 2024-2025) ... lo
iremos refinando en el Paso 3 si ccxt trae el dato". Es decir: es un hueco
YA CONOCIDO desde el principio del proyecto, nunca cerrado. Binance y Bybit
en particular son conocidos por tener perpetuos con intervalos de 1h/2h/4h
para algunos altcoins, no solo 8h — si eso afecta a algún símbolo que este
proyecto está rankeando, el APR de ESE símbolo en ESE exchange sale mal
por un factor de 2x-8x, silenciosamente (no es un fallo que se note, un
número plausible pero incorrecto).

**Confirmaría/refutaría**: imprimir una fila cruda de
`fetch_funding_rates()` de binanceusdm/bybit para un símbolo conocido por
tener intervalo no estándar, y ver si ccxt expone algún campo tipo
`fundingInterval` que hoy se está ignorando.

**Nota**: a diferencia de los demás hallazgos, este SÍ está documentado como limitación conocida en el propio código — no es una sorpresa oculta, es deuda técnica pendiente desde el principio.

### 6. Lighter no usa el campo `status` que ya sabíamos que existía — mercados "inactive" sin filtrar — CONFIRMADO (el hueco)

**`connectors/dex_lighter.py`**, todo el bucle de `depth_by_market` (líneas ~110-125)

Ya lo habíamos anotado como pendiente al arreglar el bug de `raw_symbol`
(ver README): `orderBookDetails` trae un campo `status` ("active"/
"inactive") que este conector no usa. Confirmé que sigue sin usarse en
ningún punto del archivo — cualquier mercado marcado "inactive" que
todavía tenga una fila en `/funding-rates` con `exchange="lighter"` entra
al ranking igual que uno activo. Mismo patrón exacto que el bug ya
arreglado de Extended (mercados DELISTED).

**Fix directo**: mismo patrón que Extended — excluir filas cuyo `status` de `orderBookDetails` no sea `"active"`.

---

## 🟡 Medio

### 7. GRVT: el fallback de `funding_rate` se rompe si la API manda `null` explícito en vez de omitir el campo — CONFIRMADO (el comportamiento de Python) / SOSPECHOSO (si GRVT lo hace)

**`connectors/dex_grvt.py:475-478`**

```python
rate_raw = ticker.get("funding_rate", ticker.get("funding_rate_8h_curr", ticker.get("funding_rate_curr")))
```

`dict.get(clave, default)` solo usa `default` cuando la clave NO EXISTE. Si
GRVT alguna vez manda `"funding_rate": null` (en vez de no mandar la clave),
`.get()` devuelve `None` directamente y el resto de la cadena de fallback
nunca se ejecuta — ese instrumento se descarta aunque
`funding_rate_8h_curr` (el campo ya confirmado y usado en producción)
tuviera un valor perfectamente válido.

### 8. GRVT: el ÷100 solo está confirmado para `funding_rate_8h_curr`, pero se aplica igual si viniera de `funding_rate` (potencialmente "centibeeps", otra unidad) — SOSPECHOSO

**`connectors/dex_grvt.py:475-478, 547`**

Toda la evidencia real (AAVE/ADA/ARB/etc.) que justificó el ÷100 se sacó
específicamente del campo `funding_rate_8h_curr`. Si `funding_rate` (el
campo nuevo, que la propia documentación de GRVT llama "centibeeps")
empezara a venir con datos, se le aplicaría el mismo ÷100 sin verificar que
sea la unidad correcta para ESE campo.

### 9. Nado: si el endpoint de estado cambia de forma, el filtro de mercados delistados se desactiva entero, en silencio — CONFIRMADO

**`connectors/dex_nado.py:156-185`**

```python
symbols_map = (symbols_payload.get("data") or {}).get("symbols") or {}
if not symbols_map:
    logger.warning(...)
...
if symbols_map:  # <- si está vacío, se salta TODO el filtro de trading_status
    status = (symbols_map.get(base_currency) or {}).get("trading_status")
    if status != "live":
        continue
```

Si ese endpoint cambia de forma (ya ha pasado dos veces con otro endpoint
de Nado, según el propio docstring del archivo), `symbols_map` queda vacío
y el filtro completo de "solo mercados `live`" se apaga para TODOS los
mercados, no solo para el que falló — reintroduciendo exactamente el tipo
de mercado fantasma que este conector se construyó para evitar. Solo se
avisa con un `logger.warning`, fácil de no ver.

### 10. MEXC/HTX tragan en silencio una respuesta de ticker vacía o rota — CONFIRMADO

**`connectors/cex_mexc.py:146-149`**, **`connectors/cex_htx.py:172-179`**

Los otros endpoints de estos dos conectores SÍ lanzan `RuntimeError` si
vienen vacíos; el endpoint de ticker (que es de donde sale el OI de MEXC
entero, y el volumen/mark price de HTX) no tiene ese mismo chequeo — si
alguna vez responde vacío o con otra forma, esas columnas se quedan en
`None` para TODO el exchange, sin ningún aviso, indistinguible de "este
exchange simplemente no reporta esto".

### 11. `apiAllowed` de MEXC es un proxy sin confirmar para "no está delistado" — SOSPECHOSO

**`connectors/cex_mexc.py:38-45, 132, 172-174`**

El único filtro de "¿este mercado está vivo?" en MEXC es `apiAllowed`,
confirmado en vivo solo para UN contrato activo (BTC_USDT). Existe otro
campo (`state`) que parece más parecido al `status` de KuCoin/Extended,
pero se ignora a propósito porque su mapeo "no está documentado". Si
`apiAllowed` resulta ser un permiso de API y no un estado de listado, un
mercado delistado con `apiAllowed=true` se colaría sin que nada lo pille —
mismo patrón que el bug ya arreglado de Extended.

### 12. Contratos inversos/coin-margined en MEXC — mismo hueco que causó el bug de SOL en KuCoin, no revisado aquí — SOSPECHOSO

**`connectors/cex_mexc.py:118-133, 218`**

MEXC solo recorta el sufijo de la cotización al normalizar el símbolo — no
hay ningún equivalente al `isInverse`/`multiplier<0` que se añadió a
KuCoin. Si MEXC lista algún contrato margined-en-moneda-base junto a uno
USDT-margined para el mismo activo base, se normalizarían al mismo símbolo
y, si su fórmula de OI difiere (como pasó en KuCoin), el número saldría mal
en silencio.

### 13. `mark_price == 0` no está guardado en ningún conector — solo se comprueba `is not None` — SOSPECHOSO, severidad baja

**`connectors/cex_ccxt.py:233-244`, `cex_kucoin.py:261-266`, `cex_mexc.py:203-208`**

Un `markPrice: 0` explícito (en vez de campo ausente) pasaría el chequeo
`is not None` y produciría `open_interest_usd = 0.0` — un mercado real
mostrado como si tuviera profundidad cero, en vez de un error visible.

### 14. Paradex: la escala de `funding_rate` nunca se verificó contra un valor real, a diferencia de todo lo demás en este archivo — SOSPECHOSO

**`connectors/dex_paradex.py:83, 121`**

El docstring dedica varios párrafos a las dudas sobre OI y volumen, pero no
dice absolutamente nada sobre si `funding_rate` necesita escalado — el
único campo de los tres sin ninguna cita de valor real confirmado.

### 15. Vertex: tanto el escalado de `funding_rate` (÷1e18) como la interpretación de `open_interest` en USD dependen de suposiciones sin verificar en vivo — SOSPECHOSO (ya sabíamos que Vertex es el conector menos confirmado; esto lo concreta)

**`connectors/dex_vertex.py:57-83`**

El OI tiene al menos un ejemplo numérico citado para calibrar; el
`funding_rate` no tiene NINGÚN valor real citado, solo "Vertex suele usar
punto fijo de 18 decimales". Y la calibración del propio OI asume que el
`product_id` de BTC en Vertex coincide con el de Nado ("heredando la
numeración") — pero el mismo docstring, en otra sección, advierte
explícitamente que el `trading_status` de Nado NO se hereda de Vertex, así
que asumir que la numeración de producto sí se hereda es una suposición
adicional sin apoyo.

### 16. Conversiones `float()` de `funding_rate`/`mark_price` sin `try/except` en 7 conectores DEX — un solo instrumento raro puede tirar el fetch entero, incluso donde el código dice que no debería — CONFIRMADO

**GRVT, Lighter, Hyperliquid, Paradex, Extended, Pacifica, edgeX** — en todos, el OI/volumen sí están protegidos con `try/except (TypeError, ValueError)`, pero `funding_rate`/`mark_price` justo al lado no lo están. En GRVT y edgeX esto ocurre DENTRO de un `ThreadPoolExecutor` cuyo propio comentario dice "un instrumento suelto no debe tirar todo el conector" — pero el `try/except` ahí solo envuelve la llamada de red, no el parseo de después, así que un instrumento con un valor no numérico SÍ tira el conector entero, contradiciendo la intención declarada en el propio comentario.

### 17. Sin tabla de alias para símbolos con prefijo de multiplicador (ej. "1000PEPE") — posibles pares reales que nunca se cruzan — SOSPECHOSO

**`connectors/cex_ccxt.py:165`, `connectors/dex_apex.py` (símbolo tal cual, con el "1000" literal)**

No existe ninguna tabla de alias en todo `core/` (solo existe el caso puntual XBT→BTC en KuCoin). Si un mismo activo aparece como "1000PEPE" en un exchange y "PEPE" en otro (convención real y conocida en el sector), nunca se emparejan entre sí — sin error, sin aviso, solo oportunidades perdidas en silencio.

### 18. MEXC/HTX descartan en silencio símbolos presentes en el feed de funding pero ausentes en el feed de metadata — sin aparecer en el diccionario de "descartados" — CONFIRMADO

**`connectors/cex_mexc.py:172-174`, `connectors/cex_htx.py:202-204`**

A diferencia de cada otro motivo de descarte en la misma función (que sí se registra en `skipped` y sale en el log), un símbolo simplemente ausente de la tabla de metadata se pierde con un `continue` silencioso, antes de llegar a registrarse en ningún sitio.

---

## 🟢 Bajo / cosmético

### 19. `WindowStat.apr_avg` — un `if/else` que no hace nada — CONFIRMADO, pero inofensivo hoy

**`core/history.py:106`**: `apr_avg=avg if enough else avg` — las dos ramas son iguales, así que en la práctica es `apr_avg=avg` siempre, aunque el campo está documentado como "`None` si no hay histórico suficiente". Hoy no causa ningún número mal mostrado porque los dos sitios que lo leen (`pages/1_Funding_Rates.py`, `cli.py`) vuelven a comprobar `enough_history` por su cuenta — pero es una trampa para quien use este campo en el futuro confiando en su propia documentación.

### 20. RiseX: helper de recorte de sufijo con un formato que no coincide con el confirmado en vivo — probablemente código muerto — CONFIRMADO el desajuste, SOSPECHOSO el impacto

**`connectors/dex_risex.py:236-240`**: espera sufijos tipo `"-USDC"` pero el formato real confirmado en vivo usa barra (`"BTC/USDC"`). Solo se alcanza como último recurso si el símbolo no trae ninguno de los campos habituales — parece no haberse disparado nunca todavía, pero si pasara, el símbolo saldría mal formado silenciosamente.

### 21. Variational: la protección contra un `interval_hours` corrupto es casualidad algebraica, no una regla exigida — CONFIRMADO, severidad baja

**`connectors/dex_variational.py:174-175`** vs **`core/normalize.py:70-74`**: ambos usan la misma constante `HOURS_PER_YEAR=8760` definida por separado en cada archivo y la misma fórmula, así que un `interval_hours` corrupto se cancela matemáticamente y no afecta al APR mostrado — hoy. Pero no hay ningún test ni comentario que ate esas dos fórmulas entre sí; si una cambia sin la otra en el futuro, la protección desaparece sin que nada avise.

### 22. Variational: error aritmético en un comentario del docstring (no afecta al código) — CONFIRMADO

**`connectors/dex_variational.py:33-34`**: dice que el techo teórico es "0.02 × 8760 = 17.52 (1752%)" — la cuenta real da 175.2, o sea 17520%, no 1752%. Solo el comentario está mal, ningún código depende de ese número.

---

## Resumen para decidir

Lo más urgente de verdad, en mi opinión, son los dos del bloque 🔴: el modo
Demo por defecto (un cambio de una palabra) y que el filtro de
same-exchange nunca se aplique de verdad (otra línea). Ninguno de los dos
necesita esperar a "ver qué pasa en producción" — son arreglables ya,
con evidencia de sobra.

El resto son extensiones del mismo tipo de bug que ya hemos cazado esta
sesión (guardias de OI negativo, filtros de status, escalas sin confirmar)
aplicados a conectores que todavía no habían pasado por esta lupa —
algunos confirmados por el código, otros solo sospechosos hasta que
aparezcan en un log real.

Dime por cuáles quieres que empecemos.
