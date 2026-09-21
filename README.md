# Daily World Brief

Agregador de noticias diario, automático, gratuito y accesible desde el móvil.
Cada día a las **05:30 UTC** genera una página estática en GitHub Pages con las
noticias más importantes en una matriz de **3 secciones × 3 continentes**:

|            | Asia | Europa | América |
|------------|------|--------|---------|
| Economía   | 3-5  | 3-5    | 3-5     |
| Política y Geopolítica | 3-5 | 3-5 | 3-5 |
| Tecnología e IA | 3-5 | 3-5 | 3-5 |

**Cada noticia se lee en su idioma original** (español, inglés o chino
simplificado), sin traducciones: las fuentes son trilingües y el resumen se
escribe en el idioma del artículo. Los botones **ES / EN / 中文** de la cabecera
son un *filtro* — activan o desactivan idiomas — no un traductor. Además: tema
claro/oscuro según el sistema, archivo de los últimos 30 días, PWA instalable,
feed RSS propio y "dato del día" con indicadores de mercado.

> **Por qué sin traducción**: traducir cada noticia a tres idiomas triplicaba
> los tokens de salida sin aportar nada a un lector que entiende los tres.
> Quitarlo redujo el coste diario a la mitad y permitió añadir fuentes en chino.

## Cómo funciona

```
feeds.yaml ──► scripts/collect.py ──► work/collected.json
                                          │
              API de Anthropic (claude-haiku-4-5, hasta 6 llamadas):
              · 3 de selección, una por sección (dedup + continente)
              · 1 de resumen, agrupada por idioma
                                          ▼
              · 1 de agrupación y comparación con ayer
              · 1 opcional para Radar IA
                                          ▼
                                 data/YYYY-MM-DD.json
                                          │
                              scripts/build.py
                                          ▼
              site/ (index.html + data/ + feed.xml + PWA) ──► GitHub Pages
```

- **Coste estimado**: `cost_usd` registra el total calculado con los tokens y
  precios configurados en el código; no es una factura. Incluye la agrupación
  (`events.cost_usd`) y el Radar (`radar_cost_usd`).
- **Selección por secciones**: con las 9 celdas en una sola llamada, Haiku
  dejaba pasar duplicados y confundía continentes (DeepMind en Asia, Colombia
  en Europa). Una llamada por sección lo corrige y cuesta lo mismo, porque cada
  noticia se envía una sola vez.
- **Una tarjeta por acontecimiento**: tras seleccionar y resumir, una llamada
  agrupa coberturas del mismo hecho entre secciones e idiomas. Conserva todas
  las fuentes seleccionadas y sus enlaces originales en `sources`; el filtro
  de idioma puede mostrar otra fuente de la misma tarjeta. Hechos distintos
  sobre un mismo protagonista deben mantenerse separados.
- **Qué cambió desde ayer**: compara los titulares y resúmenes con la edición
  del día anterior. Distingue nuevo en el brief, con novedades, sin cambios
  detectados y cambio sin confirmar. Solo describe una novedad si encuentra
  un hecho concreto; coloca las tarjetas sin cambios al final de cada bloque
  y enlaza la edición anterior. Si falta esa edición, no afirma una novedad.
  No lee artículos completos y la comparación puede equivocarse. Si la
  agrupación falla o pierde alguna fuente, publica las tarjetas originales
  y muestra que la comparación no está disponible.
- **Control de idioma**: los resúmenes se piden agrupados por idioma (un campo
  `lang` por línea no bastaba: el modelo mezclaba idiomas entre ítems vecinos) y
  después se validan; cualquier resumen sospechoso se registra en el log.
- **Tolerancia a fallos**: los feeds caídos se registran y aparecen en el pie
  de página ("N fuentes no disponibles hoy"). Si la API de Anthropic falla,
  se publica igualmente un brief con titulares sin resumen (`mode:
  "headlines-only"`).

## Radar IA

El bloque inicial ofrece **hasta tres descubrimientos**, con una posible utilidad
para aprender Python, analizar documentos o estudiar gobernanza de IA, y una
comprobación pendiente concreta. Conserva los filtros de idioma y aparece en el RSS.

- **GitHub**: API oficial, repositorios con etiqueta `llm` creados en los últimos
  30 días y al menos 10 estrellas; hasta 15 candidatos, ordenados por estrellas
  totales. Es descubrimiento de proyectos recientes, no el ranking Trending ni
  una medida de crecimiento diario.
- **Hacker News**: API oficial, hasta 60 publicaciones de la lista principal;
  conserva hasta 15 relacionadas con IA publicadas en las últimas 48 horas.
  Enlaza tanto el recurso compartido como la conversación.

La selección usa títulos, descripciones de repositorios y el texto del post de
HN cuando existe. **No lee artículos completos, README, código ni comentarios**;
las utilidades son hipótesis y la popularidad no equivale a calidad. Puede
publicar menos de tres resultados o ninguno. Excluye duplicados del brief actual
y del Radar de los siete días anteriores, por URL o similitud de título.

Añade como máximo una llamada al modelo actual; su coste queda registrado en
`radar_cost_usd` y sumado al total. Los candidatos y la selección quedan en el
archivo diario `candidates/`. Si una fuente falla, se conserva la otra; si no se
puede completar la selección, el Radar muestra su estado sin inventar resultados
ni impedir el brief habitual. No necesita nuevas claves ni dependencias.

Para comprobar solo la recogida del Radar:

```bash
python -c "import sys; sys.path.insert(0, 'scripts'); from radar import collect_radar; print(collect_radar())"
```

## Añadir o quitar fuentes

Edita [`feeds.yaml`](feeds.yaml). Cada fuente tiene:

```yaml
- name: Nombre del medio        # se muestra en la página
  url: https://…/rss.xml        # URL del feed RSS/Atom
  section: economia             # economia | politica | tecnologia
  continent: global             # asia | europa | america | global
  lang: en                      # es | en | zh — idioma en que se resumirá
```

`continent: global` significa que el feed cubre varios continentes y la IA
asigna cada noticia al suyo. `lang` sí es funcional: determina el idioma del
resumen (si el titular es claramente chino, se detecta automáticamente aunque
el feed diga otra cosa). `section` decide en qué llamada de selección entra el
feed, así que conviene acertar. Para validar que un feed funciona:

```bash
python scripts/collect.py --check
```

Fuentes en chino: **FT中文网** y **經濟日報 台灣** (economía), **BBC中文**,
**德國之聲中文**, **中央社 CNA** y **纽约时报中文网** (política), **科技新報
TechNews** e **iThome 台灣** (tecnología).

> **Cuidado con los medios de China continental**: IT之家 se retiró aunque su
> feed funciona desde un ordenador normal — rechaza las IPs de GitHub Actions
> (`ConnectionError`), así que fallaba cada día en producción y dejaba la celda
> tecnología/Asia vacía. Por eso las fuentes en chino son de Taiwán, Hong Kong o
> internacionales. Si añades un medio chino, comprueba que funciona **desde el
> workflow**, no solo en local.

Fuentes descartadas en la verificación (2026-08-09) por no tener RSS público
operativo: Caixin Global, FMI, AP News, NHK World (inglés), blog de Anthropic,
Banco Mundial, BIS, OCDE, 36Kr, Sina Tech, 聯合早報, 香港01, 數位時代 y
PingWest. En su lugar se usan Nikkei/SCMP (Asia), NPR/Japan Times/CNA y
Google AI Blog.

## Cambiar el horario

Edita el cron en [`.github/workflows/daily.yml`](.github/workflows/daily.yml):

```yaml
schedule:
  - cron: "30 5 * * *"   # minuto hora * * * (en UTC)
```

Ojo: GitHub Actions puede retrasar los crons unos minutos en horas punta.

## Forzar una ejecución manual

En GitHub: **Actions → Daily brief → Run workflow**. O con la CLI:

```bash
gh workflow run daily.yml
```

## Ejecución local

```bash
pip install -r requirements.txt
python scripts/collect.py
set ANTHROPIC_API_KEY=sk-ant-…   # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-…"
python scripts/summarize.py
python scripts/build.py
# abre site/index.html en el navegador
```

Sin `ANTHROPIC_API_KEY` el brief se genera en modo titulares (sin resúmenes
ni traducción).

## Auditar la selección

Cada ejecución archiva en `candidates/YYYY-MM-DD.json` **todas** las noticias
recogidas ese día (unas 380), marcando dos cosas por cada una: si llegó a verla
el modelo (`seen_by_model`) y si acabó publicada (`selected`). Sirve para juzgar
si el selector acierta o se queda corto:

```bash
python scripts/review.py            # último día: publicadas vs descartadas, por sección
python scripts/review.py 2026-08-12 # un día concreto
python scripts/review.py --week     # 7 días + tasa de publicación por fuente
```

`--week` incluye la lista de fuentes que **nunca** se publicaron, útil para
detectar feeds que solo aportan ruido.

> **Por qué se archiva en vez de reconstruirlo después**: un RSS es una ventana
> deslizante, no un archivo. Medido el 2026-08-09, 27 de los 37 feeds retienen
> menos de 7 días, y los más activos mucho menos: Al Jazeera y 中央社 tiran una
> noticia a las **9 horas** de publicarla, Financial Times a las 12, Japan Times
> a las 15. Lo que no se guarda el mismo día es irrecuperable.

Ocupa ~165 KB al día (~5 MB al mes). Se omite el extracto a propósito: el
selector tampoco lo ve, así que el archivo refleja exactamente la información
que tuvo delante. Si el repositorio crece demasiado, se pueden borrar los
`candidates/` antiguos sin afectar a la web.

## Tests

```bash
pytest -q
```

Cubren: parseo y limpieza de feeds, validación del esquema de `feeds.yaml`,
detección de idioma, validación de resúmenes en idioma equivocado,
deduplicación (incluida la de titulares en chino), esquema del JSON diario en
modo fallback y build completo del HTML/RSS/PWA.

### Limitación conocida

Un artículo en chino sobre un país europeo (por ejemplo, IT之家 cubriendo los
tribunales británicos) puede acabar clasificado en Asia: el modelo usa el
`continent_hint` del feed cuando el titular no es concluyente.

## Configuración del repositorio (una sola vez)

1. **Secret**: Settings → Secrets and variables → Actions → New repository
   secret → nombre `ANTHROPIC_API_KEY`, valor tu clave de
   [console.anthropic.com](https://console.anthropic.com/settings/keys).
2. **Pages**: Settings → Pages → Source: **GitHub Actions**.
3. Lanza una ejecución manual (ver arriba) para publicar el primer brief.
