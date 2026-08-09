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
              API de Anthropic (claude-haiku-4-5, 4 llamadas):
              · 3 de selección, una por sección (dedup + continente)
              · 1 de resumen, agrupada por idioma
                                          ▼
                        dedup determinista por titular
                                          ▼
                                 data/YYYY-MM-DD.json
                                          │
                              scripts/build.py
                                          ▼
              site/ (index.html + data/ + feed.xml + PWA) ──► GitHub Pages
```

- **Coste**: ~0,025 $/día en tokens (Haiku, prompts compactos, solo titulares
  + extractos como entrada). El coste real de cada día queda registrado en el
  campo `cost_usd` del JSON.
- **Selección por secciones**: con las 9 celdas en una sola llamada, Haiku
  dejaba pasar duplicados y confundía continentes (DeepMind en Asia, Colombia
  en Europa). Una llamada por sección lo corrige y cuesta lo mismo, porque cada
  noticia se envía una sola vez.
- **Deduplicación en dos pasadas**: el modelo agrupa las versiones de una misma
  noticia y elige la fuente más autorizada; después, `dedupe_cells()` compara
  titulares por solapamiento de palabras (bigramas de caracteres en chino) y
  elimina los repetidos que se le hayan escapado, incluso entre celdas distintas.
- **Control de idioma**: los resúmenes se piden agrupados por idioma (un campo
  `lang` por línea no bastaba: el modelo mezclaba idiomas entre ítems vecinos) y
  después se validan; cualquier resumen sospechoso se registra en el log.
- **Tolerancia a fallos**: los feeds caídos se registran y aparecen en el pie
  de página ("N fuentes no disponibles hoy"). Si la API de Anthropic falla,
  se publica igualmente un brief con titulares sin resumen (`mode:
  "headlines-only"`).

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
