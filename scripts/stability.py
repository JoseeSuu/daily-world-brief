# -*- coding: utf-8 -*-
"""Estabilidad de la selección: lanza la fase 1 de summarize.py dos veces
sobre las candidatas archivadas y mide cuánto coinciden las dos pasadas.

Si con la misma entrada el modelo elige cosas distintas, compensa seleccionar
dos veces y quedarse con lo que coincide (la "doble puntuación" de AIHOT).
Si coincide casi todo, no.

    python scripts/stability.py                  # últimos 7 días
    python scripts/stability.py --days 14
    python scripts/stability.py 2026-10-20 -v    # un día, enseñando qué cambia
    python scripts/stability.py --shuffle        # 2ª pasada con el orden barajado
    python scripts/stability.py --dry-run        # sin API: días y coste estimado

Necesita ANTHROPIC_API_KEY. Cada día son dos selecciones (~0,025 $ con
Haiku 4.5). El detalle se guarda en work/stability-<fecha>.json.
"""
import argparse
import datetime as dt
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import summarize  # noqa: E402

CAND = ROOT / "candidates"
OUTPUT_PER_CALL = 150  # tokens de salida aproximados de una llamada de selección


def load(date: str) -> dict:
    return json.loads((CAND / f"{date}.json").read_text(encoding="utf-8"))


def available() -> list:
    return sorted(f.stem for f in CAND.glob("????-??-??.json"))


def model_input(day: dict) -> list:
    """La lista que vio el selector, en el mismo orden. El archivo guarda las
    candidatas en el orden de collect.py, así que basta con repetir el
    intercalado y el tope de summarize.py."""
    items = summarize.interleave_by_source(day["items"])[:summarize.MAX_INPUT_ITEMS]
    seen = {i["id"] for i in day["items"] if i["seen_by_model"]}
    if {i["id"] for i in items} != seen:
        print(f"[AVISO] {day['date']}: la entrada reconstruida no coincide con "
              "seen_by_model (¿cambió MAX_INPUT_ITEMS?)", file=sys.stderr)
    return items


def compare(ids_a, ids_b, titles: dict) -> dict:
    """Coincidencia entre dos selecciones. Por noticia: el mismo id. Por
    evento: además, ids distintos cuyo titular cuenta lo mismo (el modelo
    eligió otro medio). same_story no reconoce la misma noticia en dos
    idiomas, así que la coincidencia por evento, si acaso, se queda corta."""
    same_id = set(ids_a) & set(ids_b)
    only_a = [i for i in ids_a if i not in same_id]
    only_b = [i for i in ids_b if i not in same_id]
    twins = []
    for i in list(only_a):
        j = next((j for j in only_b if summarize.same_story(titles[i], titles[j])), None)
        if j:
            only_a.remove(i)
            only_b.remove(j)
            twins.append((i, j))
    return {"a": len(ids_a), "b": len(ids_b), "same_id": len(same_id),
            "same_event": len(same_id) + len(twins),
            "only_a": only_a, "only_b": only_b, "twins": twins}


def dice(same: int, a: int, b: int) -> float:
    """Parte de lo elegido que se repite en la otra pasada."""
    return 2 * same / (a + b) if a + b else 1.0


def run_day(client, date: str, shuffle: bool) -> dict:
    day = load(date)
    items = model_input(day)
    runs, usage = [], [0, 0]
    for n in range(2):
        pool = items
        if n == 1 and shuffle:
            pool = items[:]
            random.Random(date).shuffle(pool)
        chosen, u = summarize.select_items(client, pool)
        usage = [usage[0] + u[0], usage[1] + u[1]]
        runs.append(chosen)
    a, b = runs
    titles = {i["id"]: i["title"] for i in day["items"]}
    production = [i["id"] for i in day["items"] if i["selected"]]
    by_section = {}
    for section in summarize.SECTIONS:
        sa = [i for i in a if a[i][0] == section]
        sb = [i for i in b if b[i][0] == section]
        by_section[section] = compare(sa, sb, titles)
    return {"date": date, "a": a, "b": b, "usage": usage,
            "ab": compare(list(a), list(b), titles),
            "moved": sorted(i for i in a.keys() & b.keys() if a[i] != b[i]),
            "production": compare(list(a), production, titles),
            "by_section": by_section}


def estimate(dates: list) -> None:
    """Coste aproximado sin llamar a la API (≈4 caracteres latinos o
    1 carácter chino por token)."""
    prompt = len(summarize.SELECT_PROMPT) // 4
    total = 0.0
    print(f"{'fecha':12s} {'vistas':>7s} {'tokens/pasada':>14s}")
    for date in dates:
        items = model_input(load(date))
        listing = summarize.compact_listing(items)
        cjk = summarize.cjk_count(listing)
        tokens = cjk + (len(listing) - cjk) // 4 + 3 * prompt
        total += 2 * (tokens * summarize.PRICE_IN + 3 * OUTPUT_PER_CALL * summarize.PRICE_OUT) / 1e6
        print(f"{date:12s} {len(items):7d} {tokens:14d}")
    print(f"\nCoste estimado de {len(dates)} días (2 pasadas/día): ~${total:.2f}")


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def show_detail(r: dict, titles: dict, cells: dict) -> None:
    def line(mark, i):
        sec, cont = cells[i]
        print(f"    {mark} {sec[:4]}|{cont[:4]} {titles[i]['source'][:20]:20s} "
              f"{titles[i]['title'][:70]}")
    print(f"\n--- {r['date']} ---")
    for i in r["ab"]["only_a"]:
        line("A", i)
    for i in r["ab"]["only_b"]:
        line("B", i)
    for i, j in r["ab"]["twins"]:
        print(f"    = {titles[i]['source'][:20]} ⇄ {titles[j]['source'][:20]}: "
              f"{titles[i]['title'][:60]}")
    for i in r["moved"]:
        print(f"    ↔ {'|'.join(r['a'][i])} → {'|'.join(r['b'][i])}: {titles[i]['title'][:60]}")


def report(results: list, verbose: bool) -> None:
    print(f"\n{'fecha':12s} {'A':>4s} {'B':>4s} {'misma noticia':>14s} "
          f"{'mismo evento':>13s} {'cambia celda':>13s} {'A vs publicado*':>16s}")
    tot = {"a": 0, "b": 0, "same_id": 0, "same_event": 0, "moved": 0,
           "pa": 0, "pb": 0, "p_same": 0}
    for r in results:
        ab, p = r["ab"], r["production"]
        print(f"{r['date']:12s} {ab['a']:4d} {ab['b']:4d} "
              f"{pct(dice(ab['same_id'], ab['a'], ab['b'])):>14s} "
              f"{pct(dice(ab['same_event'], ab['a'], ab['b'])):>13s} "
              f"{len(r['moved']):13d} {pct(dice(p['same_event'], p['a'], p['b'])):>16s}")
        for k in ("a", "b", "same_id", "same_event"):
            tot[k] += ab[k]
        tot["moved"] += len(r["moved"])
        tot["pa"] += p["a"]
        tot["pb"] += p["b"]
        tot["p_same"] += p["same_event"]
    event = dice(tot["same_event"], tot["a"], tot["b"])
    print(f"{'TOTAL':12s} {tot['a']:4d} {tot['b']:4d} "
          f"{pct(dice(tot['same_id'], tot['a'], tot['b'])):>14s} {pct(event):>13s} "
          f"{tot['moved']:13d} {pct(dice(tot['p_same'], tot['pa'], tot['pb'])):>16s}")
    print("* Aproximado: lo publicado pasó además por el resumen y la agrupación, "
          "y días antiguos pueden ser de otra versión del prompt.")

    print("\nPor sección (mismo evento):")
    for section in summarize.SECTIONS:
        rows = [r["by_section"][section] for r in results]
        a, b = sum(x["a"] for x in rows), sum(x["b"] for x in rows)
        print(f"  {section:11s} {pct(dice(sum(x['same_event'] for x in rows), a, b)):>5s}"
              f"  ({a} + {b} elegidas)")

    if event >= 0.85:
        verdict = "ESTABLE: dos pasadas apenas cambian nada; la doble selección no compensa."
    elif event >= 0.70:
        verdict = ("ZONA GRIS: mira con -v si lo que cambia son noticias flojas del "
                   "final de cada celda o noticias importantes.")
    else:
        verdict = "INESTABLE: compensa seleccionar dos veces y quedarse con lo que coincide."
    print(f"\n{verdict}")

    if verbose:
        print("\nA/B = solo en esa pasada · = mismo evento, otro medio · ↔ cambia de celda")
        for r in results:
            day = load(r["date"])
            titles = {i["id"]: i for i in day["items"]}
            show_detail(r, titles, {**r["b"], **r["a"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*", help="YYYY-MM-DD (por defecto, los últimos --days)")
    ap.add_argument("--days", type=int, default=7, help="cuántos días recientes probar")
    ap.add_argument("--shuffle", action="store_true",
                    help="barajar el orden de la lista en la 2ª pasada")
    ap.add_argument("--dry-run", action="store_true", help="solo estimar el coste, sin API")
    ap.add_argument("--workers", type=int, default=4, help="días en paralelo")
    ap.add_argument("-v", "--verbose", action="store_true", help="enseñar qué cambia cada día")
    args = ap.parse_args()

    dates = args.dates or available()[-args.days:]
    if not dates:
        raise SystemExit("No hay nada en candidates/.")
    if args.dry_run:
        estimate(dates)
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit('Falta ANTHROPIC_API_KEY. En PowerShell: $env:ANTHROPIC_API_KEY = "..."')

    import anthropic
    client = anthropic.Anthropic()
    print(f"Modelo {summarize.MODEL} · {len(dates)} días · 2ª pasada "
          f"{'barajada' if args.shuffle else 'con la misma entrada'}")

    def safe(date):
        try:
            return run_day(client, date, args.shuffle)
        except Exception as ex:
            print(f"[AVISO] {date}: {type(ex).__name__}: {ex}", file=sys.stderr)
            return None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = [r for r in pool.map(safe, dates) if r]
    if not results:
        raise SystemExit("Ningún día terminó; revisa los avisos.")

    report(results, args.verbose)
    tokens_in = sum(r["usage"][0] for r in results)
    tokens_out = sum(r["usage"][1] for r in results)
    cost = tokens_in / 1e6 * summarize.PRICE_IN + tokens_out / 1e6 * summarize.PRICE_OUT
    print(f"\nTokens: {tokens_in} in / {tokens_out} out -> ${cost:.4f} (precios de Haiku 4.5)")

    out = ROOT / "work" / f"stability-{dt.datetime.now():%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": summarize.MODEL, "shuffle": args.shuffle,
                               "days": results}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"Detalle: {out}")


if __name__ == "__main__":
    main()
