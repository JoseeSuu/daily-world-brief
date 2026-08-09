# -*- coding: utf-8 -*-
"""Generación del sitio estático en site/ a partir de data/*.json.

- Inyecta el brief más reciente en index.html (primer render sin fetch).
- Copia los JSON de los últimos 30 días a site/data/ para el selector de fechas.
- Genera feed.xml (RSS del brief), manifest.json, sw.js e icon.svg.
"""
import datetime as dt
import email.utils
import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"
KEEP_DAYS = 30

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<rect width="100" height="100" rx="20" fill="#2563eb"/>
<circle cx="50" cy="50" r="28" fill="none" stroke="#fff" stroke-width="6"/>
<path d="M22 50h56M50 22c-10 9-10 47 0 56M50 22c10 9 10 47 0 56" fill="none" stroke="#fff" stroke-width="5"/>
</svg>"""

MANIFEST = {
    "name": "Daily World Brief",
    "short_name": "WorldBrief",
    "start_url": ".",
    "display": "standalone",
    "background_color": "#0f1115",
    "theme_color": "#2563eb",
    "icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml"}],
}

SW_JS = """// Service worker: cache-first para la carcasa, network-first para datos.
const CACHE = "dwb-v1";
const SHELL = ["./", "index.html", "manifest.json", "icon.svg"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.pathname.includes("/data/") || url.pathname.endsWith("index.html") || url.pathname.endsWith("/")) {
    e.respondWith(
      fetch(e.request).then(r => {
        const copy = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
        return r;
      }).catch(() => caches.match(e.request))
    );
  } else {
    e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
  }
});
"""


def recent_briefs() -> list:
    """JSONs de data/ ordenados descendente, limitados a KEEP_DAYS."""
    files = sorted(DATA.glob("????-??-??.json"), reverse=True)
    return files[:KEEP_DAYS]


def build_rss(brief: dict, site_url: str) -> str:
    items_xml = []
    for key, items in sorted(brief.get("cells", {}).items()):
        for it in items:
            title = html.escape(it["title"])
            desc = html.escape(it["summary"] or it["title"])
            link = html.escape(it["url"])
            pub = ""
            if it.get("published"):
                try:
                    d = dt.datetime.fromisoformat(it["published"])
                    pub = f"<pubDate>{email.utils.format_datetime(d)}</pubDate>"
                except ValueError:
                    pass
            items_xml.append(
                f"<item><title>{title}</title><link>{link}</link>"
                f"<description>{desc} ({html.escape(it['source'])})</description>{pub}"
                f"<guid isPermaLink=\"true\">{link}</guid></item>"
            )
    now = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        f"<title>Daily World Brief</title><link>{html.escape(site_url)}</link>"
        "<description>Resumen diario: economía, política y tecnología en Asia, Europa y "
        "América. Cada noticia en su idioma original (ES/EN/中文).</description>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        + "".join(items_xml) + "</channel></rss>"
    )


def build(site_url: str = "https://example.github.io/daily-world-brief/") -> Path:
    briefs = recent_briefs()
    if not briefs:
        raise SystemExit("No hay ningún JSON en data/ — ejecuta antes collect + summarize.")
    latest = json.loads(briefs[0].read_text(encoding="utf-8"))
    dates = [f.stem for f in briefs]

    SITE.mkdir(exist_ok=True)
    (SITE / "data").mkdir(exist_ok=True)
    for f in briefs:
        shutil.copy2(f, SITE / "data" / f.name)
    # purgar del sitio los JSON fuera de la ventana de 30 días
    for f in (SITE / "data").glob("????-??-??.json"):
        if f.stem not in dates:
            f.unlink()

    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    page = (template
            .replace("__BRIEF_JSON__", json.dumps(latest, ensure_ascii=False))
            .replace("__DATES_JSON__", json.dumps(dates)))
    (SITE / "index.html").write_text(page, encoding="utf-8")

    (SITE / "feed.xml").write_text(build_rss(latest, site_url), encoding="utf-8")
    (SITE / "manifest.json").write_text(json.dumps(MANIFEST, indent=1), encoding="utf-8")
    (SITE / "sw.js").write_text(SW_JS, encoding="utf-8")
    (SITE / "icon.svg").write_text(ICON_SVG, encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Sitio generado en {SITE} (brief {latest['date']}, {len(dates)} días en archivo)")
    return SITE / "index.html"


if __name__ == "__main__":
    import sys
    build(sys.argv[1] if len(sys.argv) > 1 else "https://example.github.io/daily-world-brief/")
