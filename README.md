# Daily World Brief

Agregador de noticias diario, automático, gratuito y accesible desde el móvil.
Cada día a las **05:30 UTC** genera una página estática en GitHub Pages con las
noticias más importantes en una matriz de **3 secciones × 3 continentes**:

|            | Asia | Europa | América |
|------------|------|--------|---------|
| Economía   | 3-5  | 3-5    | 3-5     |
| Política y Geopolítica | 3-5 | 3-5 | 3-5 |
| Tecnología e IA | 3-5 | 3-5 | 3-5 |

Trilingüe (**ES / EN / 中文**) con selector de idioma sin recarga, tema
claro/oscuro según el sistema, archivo de los últimos 30 días, PWA instalable,
feed RSS propio y "dato del día" con indicadores de mercado.

## Cómo funciona

```
feeds.yaml ──► scripts/collect.py ──► work/collected.json
                                          │
                     API de Anthropic (claude-haiku-4-5, 2 llamadas)
                     selección + dedup, resumen + traducción ES/EN/ZH
                                          ▼
                                 data/YYYY-MM-DD.json
                                          │
                              scripts/build.py
                                          ▼
              site/ (index.html + data/ + feed.xml + PWA) ──► GitHub Pages
```

- **Coste**: ~0,02-0,05 $/día en tokens (Haiku, prompts compactos, solo
  titulares + extractos como entrada). El coste real de cada día queda
  registrado en el campo `cost_usd` del JSON.
- **Deduplicación**: si varios medios cubren la misma noticia, el modelo
  elige una sola entrada con la fuente más autorizada.
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
  lang: en                      # idioma del feed (informativo)
```

`continent: global` significa que el feed cubre varios continentes y la IA
asigna cada noticia al suyo. Para validar que un feed funciona:

```bash
python scripts/collect.py --check
```

Fuentes descartadas en la verificación inicial (2026-08-09) por no tener RSS
público operativo: Caixin Global, FMI, AP News, NHK World (inglés), blog de
Anthropic, Banco Mundial, BIS y OCDE. En su lugar se usan Nikkei/SCMP (Asia),
NPR/Japan Times/CNA y Google AI Blog.

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

## Tests

```bash
pytest -q
```

Cubren: parseo y limpieza de feeds, validación del esquema de `feeds.yaml`,
esquema del JSON diario (modo fallback) y build completo del HTML/RSS/PWA.

## Configuración del repositorio (una sola vez)

1. **Secret**: Settings → Secrets and variables → Actions → New repository
   secret → nombre `ANTHROPIC_API_KEY`, valor tu clave de
   [console.anthropic.com](https://console.anthropic.com/settings/keys).
2. **Pages**: Settings → Pages → Source: **GitHub Actions**.
3. Lanza una ejecución manual (ver arriba) para publicar el primer brief.
