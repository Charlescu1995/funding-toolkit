# Funding Toolkit

Herramienta propia de funding rates (cripto + RWA), construida combinando lo mejor de
ProFunding, Loris Tools y el selector delta-neutral de John5Cripto.

## Estado del build

Vamos construyéndola paso a paso. Progreso:

- [x] Paso 1 — Arquitectura del proyecto y modelo de datos común
- [x] Paso 2 — Conectores de datos: 8 CEX vía ccxt (Binance, Bybit, OKX, Bitget, KuCoin, Gate, MEXC, HTX) + 5 DEX vía API directa (Hyperliquid, Lighter, Paradex, Extended, Pacifica)
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

- **Intervalo de funding**: Lighter/Extended/Pacifica = 1h, Paradex = 8h (documentado
  explícitamente). Si el APR de alguno sale desproporcionado (ej. x8 de más o de
  menos), revisa `INTERVAL_HOURS` en `connectors/dex_<nombre>.py`.
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

Aparte del bug, quedaba una duda sobre si los valores de Lighter que SÍ pasan el
filtro están en la escala correcta: al comparar `/funding-rates` (tasa "actual") con
`/fundings` (histórico de eventos de funding ya liquidados) para BTC, salían números
distintos. La hipótesis más probable, respaldada por campos `funding_clamp_small` /
`funding_clamp_big` que trae la API, es que `/funding-rates` enseña la tasa YA
aplicada (con el clamp/tope de Lighter puesto), mientras que `/fundings` puede reflejar
la prima cruda antes del clamp — y al mirar una muestra más amplia de símbolos, los
valores variaban genuinamente de uno a otro (no eran todos la misma constante), lo que
apoya que son datos reales por mercado y no un fallo compartido. Aun así, esto no se ha
podido confirmar al 100% desde este entorno: **antes de operar con ellos, compara al
menos una tasa de Lighter (ej. BTC) contra la propia interfaz oficial de Lighter.**

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
