# -*- coding: utf-8 -*-
"""Selección, deduplicación, resumen y traducción con la API de Anthropic
(claude-haiku-4-5, dos llamadas compactas para mantener el coste < 0,05 $/día).

Entrada:  work/collected.json  (de collect.py)
Salida:   data/YYYY-MM-DD.json (esquema trilingüe)

Si la API falla, genera un brief "headlines-only" (titulares sin resumen)
para no dejar de publicar. La API key se lee de ANTHROPIC_API_KEY.
"""
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from market import get_market  # noqa: E402

MODEL = os.environ.get("DWB_MODEL", "claude-haiku-4-5")
SECTIONS = ["economia", "politica", "tecnologia"]
CONTINENTS = ["asia", "europa", "america"]
MAX_INPUT_ITEMS = 200      # tope de titulares que entran en la llamada 1
MIN_PER_CELL, MAX_PER_CELL = 3, 4  # 4 mantiene el coste diario < 0,05 $

# Precios Haiku 4.5 ($/MTok) para el log de coste
PRICE_IN, PRICE_OUT = 1.0, 5.0

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "cells": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": SECTIONS},
                    "continent": {"type": "string", "enum": CONTINENTS},
                    "item_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["section", "continent", "item_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cells"],
    "additionalProperties": False,
}

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "headline_es": {"type": "string"},
                    "headline_en": {"type": "string"},
                    "headline_zh": {"type": "string"},
                    "summary_es": {"type": "string"},
                    "summary_en": {"type": "string"},
                    "summary_zh": {"type": "string"},
                },
                "required": ["id", "headline_es", "headline_en", "headline_zh",
                             "summary_es", "summary_en", "summary_zh"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["stories"],
    "additionalProperties": False,
}

SELECT_PROMPT = """You are the editor of a daily world news brief with a 3x3 matrix:
sections [economia (economy/markets/central banks), politica (politics & geopolitics),
tecnologia (technology & AI)] x continents [asia, europa, america].

Below is a list of news items from the last 24h, one per line:
id|source|section_hint|continent_hint|title

Select the {min_c}-{max_c} MOST IMPORTANT items for EACH of the 9 cells.
Rules:
- Deduplicate: if several sources cover the same story, pick ONE (the most authoritative
  source: official bodies > FT/Economist/BBC-tier > others).
- Assign each story to the continent it is ABOUT (not where the outlet is based).
  continent_hint "global" means you must decide from the title.
- Prefer hard, factual news over opinion, features or listicles.
- A cell may have fewer items only if there genuinely isn't enough relevant news.
- Use only ids from the list.

ITEMS:
{items}"""

SUMMARY_PROMPT = """You write a trilingual (Spanish, English, Simplified Chinese) daily news brief.
For each item below (id|source|title|excerpt), produce:
- headline_es / headline_en / headline_zh: a clear, factual headline (max 12 words / 20 characters for zh).
- summary_es / summary_en / summary_zh: 2 short factual sentences (max 35 words total;
  zh: max 55 characters): what happened, who/where, and why it matters.
  No opinion, no speculation, no clickbait, no filler.
Write natural, native-quality prose in each language. Base yourself ONLY on the title
and excerpt given; do not invent facts, numbers or quotes.

ITEMS:
{items}"""


def interleave_by_source(items: list) -> list:
    """Alterna ítems de cada fuente para que el recorte a MAX_INPUT_ITEMS
    no deje fuera fuentes enteras (las últimas del feeds.yaml)."""
    queues = defaultdict(list)
    for i in items:
        queues[i["source"]].append(i)
    out, added = [], True
    while added:
        added = False
        for q in queues.values():
            if q:
                out.append(q.pop(0))
                added = True
    return out


def load_collected(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_listing(items: list) -> str:
    return "\n".join(
        f"{i['id']}|{i['source']}|{i['section']}|{i['continent']}|{i['title']}"
        for i in items
    )


def call_json(client, prompt: str, schema: dict, max_tokens: int) -> tuple:
    """Llamada con salida estructurada. Devuelve (dict, usage)."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": prompt}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text), resp.usage


def run_ai(collected: dict) -> tuple:
    """Devuelve (cells, mode, cost). Lanza excepción si la API falla."""
    import anthropic
    client = anthropic.Anthropic()

    items = interleave_by_source(collected["items"])[:MAX_INPUT_ITEMS]
    by_id = {i["id"]: i for i in items}
    usage_total = [0, 0]

    # ── Llamada 1: selección + dedup + asignación de continente ──
    sel, u1 = call_json(
        client,
        SELECT_PROMPT.format(min_c=MIN_PER_CELL, max_c=MAX_PER_CELL,
                             items=compact_listing(items)),
        SELECT_SCHEMA, max_tokens=2500,
    )
    usage_total[0] += u1.input_tokens
    usage_total[1] += u1.output_tokens

    chosen = {}  # id -> (section, continent)
    for cell in sel["cells"]:
        for iid in cell["item_ids"][:MAX_PER_CELL]:
            if iid in by_id and iid not in chosen:
                chosen[iid] = (cell["section"], cell["continent"])
    if not chosen:
        raise RuntimeError("la selección no devolvió ítems válidos")

    # ── Llamada 2: resumen + traducción trilingüe ──
    listing = "\n".join(
        f"{iid}|{by_id[iid]['source']}|{by_id[iid]['title']}|{by_id[iid]['excerpt']}"
        for iid in chosen
    )
    summ, u2 = call_json(
        client, SUMMARY_PROMPT.format(items=listing),
        SUMMARY_SCHEMA, max_tokens=16000,
    )
    usage_total[0] += u2.input_tokens
    usage_total[1] += u2.output_tokens

    stories_by_id = {s["id"]: s for s in summ["stories"]}
    cells = defaultdict(list)
    for iid, (section, continent) in chosen.items():
        s, item = stories_by_id.get(iid), by_id[iid]
        if not s:
            continue
        cells[f"{section}|{continent}"].append({
            "headline": {"es": s["headline_es"], "en": s["headline_en"], "zh": s["headline_zh"]},
            "summary": {"es": s["summary_es"], "en": s["summary_en"], "zh": s["summary_zh"]},
            "original_title": item["title"],
            "source": item["source"],
            "url": item["url"],
            "published": item["published"],
        })

    cost = usage_total[0] / 1e6 * PRICE_IN + usage_total[1] / 1e6 * PRICE_OUT
    print(f"Tokens: {usage_total[0]} in / {usage_total[1]} out -> ${cost:.4f}")
    return dict(cells), "full", cost


def run_fallback(collected: dict) -> dict:
    """Sin API: titulares sin resumen, agrupados por el continente del feed."""
    cells = defaultdict(list)
    pools = defaultdict(list)
    for i in collected["items"]:
        pools[(i["section"], i["continent"])].append(i)
    for section in SECTIONS:
        for continent in CONTINENTS:
            pool = pools.get((section, continent), [])[:MAX_PER_CELL]
            # completar con ítems "global" de la misma sección
            extra = pools.get((section, "global"), [])
            while len(pool) < MAX_PER_CELL and extra:
                pool.append(extra.pop(0))
            for i in pool:
                t = {"es": i["title"], "en": i["title"], "zh": i["title"]}
                cells[f"{section}|{continent}"].append({
                    "headline": t, "summary": {"es": "", "en": "", "zh": ""},
                    "original_title": i["title"], "source": i["source"],
                    "url": i["url"], "published": i["published"],
                })
    return dict(cells)


def main():
    collected_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "work" / "collected.json"
    collected = load_collected(collected_path)
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    cost = 0.0
    try:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY no definida")
        cells, mode, cost = run_ai(collected)
    except Exception as ex:
        print(f"[AVISO] Fallback sin IA: {ex}", file=sys.stderr)
        cells, mode = run_fallback(collected), "headlines-only"

    brief = {
        "date": today,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": mode,
        "cost_usd": round(cost, 4),
        "market": get_market(),
        "failed_sources": [f["name"] for f in collected["failed_sources"]],
        "cells": cells,
    }
    out = ROOT / "data" / f"{today}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(len(v) for v in cells.values())
    print(f"Brief {today} ({mode}): {n} noticias -> {out}")


if __name__ == "__main__":
    main()
