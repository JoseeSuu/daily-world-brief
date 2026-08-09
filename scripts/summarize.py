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
MAX_INPUT_ITEMS = 250      # tope de titulares que entran en la llamada 1
MIN_PER_CELL, MAX_PER_CELL = 3, 5  # sin traducción x3 el coste queda ~0,03 $/día

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
                    "continent": {"type": "string", "enum": CONTINENTS},
                    "item_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["continent", "item_ids"],
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
                    "summary": {"type": "string"},
                },
                "required": ["id", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["stories"],
    "additionalProperties": False,
}

SELECT_PROMPT = """You are the editor of the "{section_label}" section of a daily world
news brief. This section is split into three continents: asia, europa, america.

Below are today's candidate items, one per line:
id|source|continent_hint|title

Pick the {min_c}-{max_c} MOST IMPORTANT items for EACH of the three continents.

DEDUPLICATION (most important rule):
Several outlets cover the same event, sometimes in different languages and with
different wordings. Group them mentally first, then emit ONE id per event —
the most authoritative source (official bodies > FT/Economist/BBC-tier > others).
The SAME event must never appear twice in your answer, not even under two
different continents. Example: "Netanyahu rejects US Gaza plan" and "Israel
rejects Trump's 15-point plan for Gaza" are the same event: choose one.

CONTINENT — assign by the place the news is ABOUT, never by where the outlet is:
- asia: China, Japan, India, Korea, SE Asia, Central Asia, and the Middle East.
- europa: EU, UK, Switzerland, Norway, Balkans, Ukraine, Russia, Turkey.
- america: USA, Canada, Mexico, Central and South America.
An organisation's home country decides it (Google DeepMind → europa/america,
never asia). "continent_hint: global" means you must decide from the title.
If a story is about relations between two continents, file it under the one
where the main action happens.

QUALITY — include only hard, factual news of international significance. Reject
opinion and analysis pieces, listicles, product reviews, consumer surveys,
routine service notices, sport, lifestyle, travel, food and celebrity items.
Leaving a continent short is better than padding it with a weak story.

Use only ids from the list below.

ITEMS:
{items}"""

SECTION_LABELS = {
    "economia": "Economy, markets and central banks",
    "politica": "Politics and geopolitics",
    "tecnologia": "Technology and AI",
}

SUMMARY_PROMPT = """You write summaries for a daily news brief. For each item below
(id|source|title|excerpt), produce a "summary": 2 short factual sentences
(max 35 words total; for Chinese: max 55 characters): what happened, who/where,
and why it matters. No opinion, no speculation, no clickbait, no filler.

CRITICAL — language: the items are grouped under headings by language. Write
each summary ENTIRELY in the language of ITS OWN heading, with correct native
grammar, and never mix languages inside a summary. An item under the Spanish
heading gets a fully Spanish summary ("turistas franceses y alemanes", never
"French y German tourists"); an item under the English heading gets a fully
English summary, even if the group above it was Spanish.

Base yourself ONLY on the title and excerpt given; do not invent facts,
numbers or quotes. Return one entry per id, keeping the ids exactly as given.

{items}"""

LANG_HEADINGS = {
    "es": "### GROUP 1 — write every summary below in SPANISH (español)",
    "en": "### GROUP 2 — write every summary below in ENGLISH",
    "zh": "### GROUP 3 — write every summary below in SIMPLIFIED CHINESE (简体中文)",
}


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


# Jerarquía de fuentes para elegir superviviente al deduplicar (menor = mejor).
SOURCE_RANK = {
    "BCE (notas de prensa)": 0, "Reserva Federal (notas de prensa)": 0,
    "Financial Times": 1, "FT Global Economy": 1, "FT中文网": 1,
    "The Economist (Finance)": 1, "BBC World": 1, "BBC中文": 1,
    "Bloomberg Economics": 2, "Bloomberg Markets": 2, "Nikkei Asia": 2,
    "Le Monde International": 2, "The Guardian World": 2, "El País Economía": 2,
    "El País Internacional": 2, "Deutsche Welle World": 2, "纽约时报中文网": 2,
}
DEFAULT_RANK = 5

# Palabras vacías que no aportan al comparar titulares.
TITLE_STOP = ES_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "by",
    "with", "from", "is", "are", "was", "were", "be", "its", "it", "that",
    "de", "la", "el", "los", "las", "un", "una", "en", "y", "que", "por",
    "para", "con", "del", "al", "se", "su", "sus", "es", "más", "como",
}

ES_WORDS = {"de", "la", "el", "que", "en", "los", "las", "para", "con", "del",
            "una", "por", "se", "su", "al", "es", "y"}
EN_WORDS = {"the", "and", "of", "with", "to", "in", "for", "is", "was", "on",
            "after", "its", "has", "will"}


def cjk_count(text: str) -> int:
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def summary_language_ok(summary: str, lang: str) -> bool:
    """Detección conservadora de resúmenes en el idioma equivocado.
    Solo marca casos claros, para no dar falsos positivos con nombres propios."""
    if not summary:
        return True
    if lang == "zh":
        return cjk_count(summary) >= 3
    if cjk_count(summary) >= 3:
        return False  # chino donde no toca
    words = {w.strip(".,;:¿?¡!()\"'").lower() for w in summary.split()}
    es_hits, en_hits = len(words & ES_WORDS), len(words & EN_WORDS)
    if lang == "es":
        return not (en_hits >= 2 and en_hits > es_hits)
    if lang == "en":
        return not (es_hits >= 2 and es_hits > en_hits)
    return True


def title_tokens(title: str) -> set:
    """Tokens comparables de un titular. Para chino, bigramas de caracteres
    (no hay espacios entre palabras)."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in title.lower())
    words = {w for w in cleaned.split() if len(w) > 2 and w not in TITLE_STOP}
    cjk = [ch for ch in title if "一" <= ch <= "鿿"]
    words |= {cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)}
    return words


def same_story(a: str, b: str) -> bool:
    """Solapamiento de tokens entre dos titulares, sobre el más corto.
    Los titulares en chino usan bigramas de caracteres, que se solapan menos
    que las palabras, así que llevan un umbral más bajo."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return False
    both_cjk = cjk_count(a) >= 4 and cjk_count(b) >= 4
    threshold = 0.35 if both_cjk else 0.45
    return len(ta & tb) / min(len(ta), len(tb)) >= threshold


def dedupe_cells(cells: dict) -> tuple:
    """Segunda pasada determinista: elimina noticias casi idénticas que el
    modelo dejó pasar, en toda la matriz. Conserva la fuente más autorizada."""
    kept, removed = [], 0
    out = {k: [] for k in cells}
    for key in cells:
        for story in cells[key]:
            twin = next((s for s in kept if same_story(s["story"]["title"], story["title"])), None)
            if twin is None:
                kept.append({"key": key, "story": story})
                continue
            removed += 1
            new_rank = SOURCE_RANK.get(story["source"], DEFAULT_RANK)
            old_rank = SOURCE_RANK.get(twin["story"]["source"], DEFAULT_RANK)
            if new_rank < old_rank:      # la nueva fuente es más autorizada
                twin["key"], twin["story"] = key, story
    for entry in kept:
        out[entry["key"]].append(entry["story"])
    return {k: v for k, v in out.items() if v}, removed


def load_collected(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_listing(items: list) -> str:
    return "\n".join(
        f"{i['id']}|{i['source']}|{i['continent']}|{i['title']}" for i in items
    )


def detect_lang(item: dict) -> str:
    """Idioma del ítem: el declarado en feeds.yaml, salvo que el titular sea
    claramente CJK (algunos feeds mezclan)."""
    title = item.get("title", "")
    if sum(1 for ch in title if "一" <= ch <= "鿿") >= 2:
        return "zh"
    lang = item.get("lang", "en")
    return lang if lang in ("es", "en", "zh") else "en"


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

    # ── Fase 1: una llamada de selección POR SECCIÓN ──
    # Con las 9 celdas en una sola llamada, Haiku dejaba pasar duplicados y
    # confundía continentes; con ~1/3 de los ítems por llamada acierta mucho más,
    # y el coste total es casi idéntico (cada ítem se envía una sola vez).
    chosen = {}  # id -> (section, continent)
    for section in SECTIONS:
        pool = [i for i in items if i["section"] == section]
        if not pool:
            continue
        sel, usage = call_json(
            client,
            SELECT_PROMPT.format(section_label=SECTION_LABELS[section],
                                 min_c=MIN_PER_CELL, max_c=MAX_PER_CELL,
                                 items=compact_listing(pool)),
            SELECT_SCHEMA, max_tokens=1200,
        )
        usage_total[0] += usage.input_tokens
        usage_total[1] += usage.output_tokens
        pool_ids = {i["id"] for i in pool}
        for cell in sel["cells"]:
            for iid in cell["item_ids"][:MAX_PER_CELL]:
                if iid in pool_ids and iid not in chosen:
                    chosen[iid] = (section, cell["continent"])
    if not chosen:
        raise RuntimeError("la selección no devolvió ítems válidos")

    # ── Fase 2: una sola llamada de resumen, agrupando por idioma ──
    # Agrupar es mucho más fiable que un campo "lang" inline: con el campo
    # inline el modelo mezclaba idiomas entre ítems vecinos.
    by_lang = defaultdict(list)
    for iid in chosen:
        by_lang[detect_lang(by_id[iid])].append(iid)
    blocks = []
    for lang in ("es", "en", "zh"):
        ids = by_lang.get(lang)
        if not ids:
            continue
        rows = "\n".join(
            f"{iid}|{by_id[iid]['source']}|{by_id[iid]['title']}|{by_id[iid]['excerpt']}"
            for iid in ids
        )
        blocks.append(f"{LANG_HEADINGS[lang]}\n{rows}")
    listing = "\n\n".join(blocks)
    summ, u2 = call_json(
        client, SUMMARY_PROMPT.format(items=listing),
        SUMMARY_SCHEMA, max_tokens=16000,
    )
    usage_total[0] += u2.input_tokens
    usage_total[1] += u2.output_tokens

    stories_by_id = {s["id"]: s for s in summ["stories"]}
    cells = defaultdict(list)
    mismatches = 0
    for iid, (section, continent) in chosen.items():
        s, item = stories_by_id.get(iid), by_id[iid]
        if not s:
            continue
        lang = detect_lang(item)
        summary = s["summary"]
        if not summary_language_ok(summary, lang):
            mismatches += 1
            print(f"[AVISO] resumen en idioma distinto de '{lang}': {item['title'][:60]}",
                  file=sys.stderr)
        cells[f"{section}|{continent}"].append({
            "title": item["title"],
            "summary": summary,
            "lang": lang,
            "source": item["source"],
            "url": item["url"],
            "published": item["published"],
        })
    if mismatches:
        print(f"[AVISO] {mismatches}/{len(chosen)} resúmenes con idioma dudoso",
              file=sys.stderr)

    cells, removed = dedupe_cells(dict(cells))
    if removed:
        print(f"Deduplicación: {removed} noticias repetidas eliminadas")

    cost = usage_total[0] / 1e6 * PRICE_IN + usage_total[1] / 1e6 * PRICE_OUT
    print(f"Tokens: {usage_total[0]} in / {usage_total[1]} out -> ${cost:.4f}")
    return cells, "full", cost


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
                cells[f"{section}|{continent}"].append({
                    "title": i["title"], "summary": "", "lang": detect_lang(i),
                    "source": i["source"], "url": i["url"],
                    "published": i["published"],
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
