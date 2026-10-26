# -*- coding: utf-8 -*-
"""Recolección: lee feeds.yaml, descarga cada feed RSS/Atom y guarda los ítems
de las últimas 24 h en work/collected.json.

Uso:
    python scripts/collect.py            # recolecta
    python scripts/collect.py --check    # solo verifica qué feeds responden
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import json
import re
import sys
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DailyWorldBrief/1.0 (+https://github.com)"
WINDOW_HOURS = 28  # 24 h + margen para feeds con relojes imprecisos
MAX_ITEMS_PER_FEED = 25
EXCERPT_CHARS = 180

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(raw: str, limit: int) -> str:
    text = WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", raw or ""))).strip()
    return text[:limit].rsplit(" ", 1)[0] if len(text) > limit else text


def parse_feed(feed: dict, now: dt.datetime) -> dict:
    """Descarga y parsea un feed. Devuelve {'items': [...]} o {'error': str}."""
    try:
        r = requests.get(feed["url"], headers={"User-Agent": UA}, timeout=25)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        parsed = feedparser.parse(r.content)
        if not parsed.entries:
            return {"error": "sin entradas"}
        items = []
        for e in parsed.entries[:MAX_ITEMS_PER_FEED * 2]:
            t = e.get("published_parsed") or e.get("updated_parsed")
            published = dt.datetime(*t[:6], tzinfo=dt.timezone.utc) if t else None
            # Sin fecha: lo aceptamos (algunos feeds, p. ej. Nikkei, no fechan)
            if published and (now - published) > dt.timedelta(hours=WINDOW_HOURS):
                continue
            title = clean_text(e.get("title", ""), 200)
            url = e.get("link", "")
            if not title or not url:
                continue
            items.append({
                "id": hashlib.sha1(url.encode()).hexdigest()[:10],
                "source": feed["name"],
                "section": feed["section"],
                "continent": feed["continent"],
                "lang": feed.get("lang", "en"),
                "title": title,
                "url": url,
                "published": published.isoformat() if published else None,
                "excerpt": clean_text(e.get("summary", "") or e.get("description", ""), EXCERPT_CHARS),
            })
            if len(items) >= MAX_ITEMS_PER_FEED:
                break
        # Un feed que parsea pero no tiene ítems en la ventana no está "caído":
        # simplemente no publicó nada reciente (p. ej. BCE en fin de semana).
        return {"items": items}
    except requests.RequestException as ex:
        return {"error": f"{type(ex).__name__}"}
    except Exception as ex:  # feed corrupto, etc.
        return {"error": f"{type(ex).__name__}: {ex}"[:100]}


def load_feeds() -> list:
    with open(ROOT / "feeds.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["feeds"]


def collect() -> dict:
    feeds = load_feeds()
    now = dt.datetime.now(dt.timezone.utc)
    all_items, failed, seen_urls = [], [], set()
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(lambda f: (f, parse_feed(f, now)), feeds))
    for feed, res in results:
        if "error" in res:
            failed.append({"name": feed["name"], "error": res["error"]})
            print(f"[FALLO] {feed['name']}: {res['error']}", file=sys.stderr)
        else:
            for item in res["items"]:
                if item["url"] not in seen_urls:  # dedup exacto por URL
                    seen_urls.add(item["url"])
                    all_items.append(item)
            print(f"[OK]    {feed['name']}: {len(res['items'])} ítems")
    return {
        "collected_at": now.isoformat(),
        "items": all_items,
        "failed_sources": failed,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="solo verificar feeds")
    ap.add_argument("--out", default=str(ROOT / "work" / "collected.json"))
    args = ap.parse_args()

    data = collect()
    print(f"\nTotal: {len(data['items'])} ítems, {len(data['failed_sources'])} feeds caídos")
    if args.check:
        return
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Guardado en {out}")


if __name__ == "__main__":
    main()
