# -*- coding: utf-8 -*-
"""Estabilidad de la selección: lanza la fase 1 de summarize.py dos veces
sobre las candidatas archivadas y mide cuánto coinciden las dos pasadas.

Si con la misma entrada el modelo elige cosas distintas, compensa seleccionar
varias veces y quedarse con lo que coincide (la "doble puntuación" de AIHOT).
Si coincide casi todo, no.

    python scripts/stability.py                  # últimos 7 días, como la web
    python scripts/stability.py --days 14
    python scripts/stability.py 2026-10-20 -v    # un día, enseñando qué cambia
    python scripts/stability.py --shuffle        # 2ª pasada con el orden barajado
    python scripts/stability.py --dry-run        # sin API: días y coste estimado
    python scripts/stability.py --model claude-haiku-4-5 --effort none --vote 1

Por defecto mide la selección de la web (SELECT_MODEL, SELECT_EFFORT y
SELECT_PASSES de summarize.py); el último ejemplo mide la de antes. Con
votación, cada pasada es una selección votada (6 selecciones por día con
3 votos), y la tabla enseña también cuánto coinciden las pasadas sueltas.

Necesita ANTHROPIC_API_KEY. El detalle se guarda en work/stability-<fecha>.json,
que judge.py puede auditar después.
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
OUTPUT_PER_CALL = 150     # tokens de salida aproximados de una llamada de selección
THINKING_PER_CALL = 2000  # razonamiento aproximado de los modelos 5.x por llamada


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


def pooled(rows: list) -> float:
    return dice(sum(r["same_event"] for r in rows), sum(r["a"] for r in rows),
                sum(r["b"] for r in rows))


def run_day(client, date: str, shuffle: bool, vote: int = 1, **options) -> dict:
    day = load(date)
    items = model_input(day)
    finals, raws, usage = [], [], [0, 0]
    for n in range(2):
        pool = items
        if n == 1 and shuffle:
            pool = items[:]
            random.Random(date).shuffle(pool)
        if vote > 1:
            chosen, u, runs = summarize.select_by_vote(client, pool, vote, **options)
        else:
            chosen, u = summarize.select_items(client, pool, **options)
            runs = [chosen]
        usage = [usage[0] + u[0], usage[1] + u[1]]
        finals.append(chosen)
        raws.append(runs)
    a, b = finals
    titles = {i["id"]: i["title"] for i in day["items"]}
    production = [i["id"] for i in day["items"] if i["selected"]]
    by_section = {}
    for section in summarize.SECTIONS:
        sa = [i for i in a if a[i][0] == section]
        sb = [i for i in b if b[i][0] == section]
        by_section[section] = compare(sa, sb, titles)
    result = {"date": date, "a": a, "b": b, "usage": usage,
              "ab": compare(list(a), list(b), titles),
              "moved": sorted(i for i in a.keys() & b.keys() if a[i] != b[i]),
              "production": compare(list(a), production, titles),
              "by_section": by_section}
    if vote > 1:
        # Pasadas sueltas emparejadas (1ª de A con 1ª de B…): la estabilidad sin votar.
        result["raw_a"], result["raw_b"] = raws
        result["single"] = [compare(list(x), list(y), titles) for x, y in zip(*raws)]
    return result


def estimate(dates: list, model: str, vote: int) -> None:
    """Coste aproximado sin llamar a la API (≈4 caracteres latinos o
    1 carácter chino por token; los modelos 5.x cuentan ~30 % más tokens
    y además razonan)."""
    newer = model != "claude-haiku-4-5"
    price_in, price_out = summarize.PRICES.get(model, (0.0, 0.0))
    prompt = len(summarize.SELECT_PROMPT) // 4
    out_per_call = OUTPUT_PER_CALL + (THINKING_PER_CALL if newer else 0)
    passes = 2 * vote
    total = 0.0
    print(f"{'fecha':12s} {'vistas':>7s} {'tokens/selección':>17s}")
    for date in dates:
        items = model_input(load(date))
        listing = summarize.compact_listing(items)
        cjk = summarize.cjk_count(listing)
        tokens = int((cjk + (len(listing) - cjk) // 4 + 3 * prompt) * (1.3 if newer else 1))
        total += passes * (tokens * price_in + 3 * out_per_call * price_out) / 1e6
        print(f"{date:12s} {len(items):7d} {tokens:17d}")
    if model not in summarize.PRICES:
        print(f"\nNo conozco los precios de {model}; no puedo estimar el coste.")
        return
    print(f"\nCoste estimado con {model} de {len(dates)} días "
          f"({passes} selecciones/día): ~${total:.2f}")


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
    voted = "single" in results[0]
    single_head = f" {'1 pasada':>9s}" if voted else ""
    print(f"\n{'fecha':12s} {'A':>4s} {'B':>4s} {'misma noticia':>14s} "
          f"{'mismo evento':>13s}{single_head} {'cambia celda':>13s} {'A vs publicado*':>16s}")
    tot = {"a": 0, "b": 0, "same_id": 0, "same_event": 0, "moved": 0,
           "pa": 0, "pb": 0, "p_same": 0}
    for r in results:
        ab, p = r["ab"], r["production"]
        single = f" {pct(pooled(r['single'])):>9s}" if voted else ""
        print(f"{r['date']:12s} {ab['a']:4d} {ab['b']:4d} "
              f"{pct(dice(ab['same_id'], ab['a'], ab['b'])):>14s} "
              f"{pct(dice(ab['same_event'], ab['a'], ab['b'])):>13s}{single} "
              f"{len(r['moved']):13d} {pct(dice(p['same_event'], p['a'], p['b'])):>16s}")
        for k in ("a", "b", "same_id", "same_event"):
            tot[k] += ab[k]
        tot["moved"] += len(r["moved"])
        tot["pa"] += p["a"]
        tot["pb"] += p["b"]
        tot["p_same"] += p["same_event"]
    event = dice(tot["same_event"], tot["a"], tot["b"])
    single = (f" {pct(pooled([s for r in results for s in r['single']])):>9s}"
              if voted else "")
    print(f"{'TOTAL':12s} {tot['a']:4d} {tot['b']:4d} "
          f"{pct(dice(tot['same_id'], tot['a'], tot['b'])):>14s} {pct(event):>13s}{single} "
          f"{tot['moved']:13d} {pct(dice(tot['p_same'], tot['pa'], tot['pb'])):>16s}")
    print("* Aproximado: lo publicado pasó además por el resumen y la agrupación, "
          "y días antiguos pueden ser de otra versión del prompt.")
    if voted:
        print("  1 pasada = mismo evento entre selecciones sueltas, antes de votar.")

    print("\nPor sección (mismo evento):")
    for section in summarize.SECTIONS:
        rows = [r["by_section"][section] for r in results]
        a, b = sum(x["a"] for x in rows), sum(x["b"] for x in rows)
        print(f"  {section:11s} {pct(pooled(rows)):>5s}  ({a} + {b} elegidas)")

    if voted:
        verdict = (f"Con votación coincide el {pct(event)} (sin votar, el "
                   f"{pct(pooled([s for r in results for s in r['single']]))}). "
                   "85 % o más es estable.")
    elif event >= 0.85:
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
    ap.add_argument("--model", default=summarize.SELECT_MODEL, help="modelo del selector")
    ap.add_argument("--effort", default=summarize.SELECT_EFFORT,
                    choices=["none", "low", "medium", "high", "xhigh", "max"],
                    help="esfuerzo de razonamiento (none para Haiku 4.5, que no lo admite)")
    ap.add_argument("--vote", type=int, default=summarize.SELECT_PASSES,
                    help="selecciones por pasada, combinadas por mayoría (1 = sin votar)")
    ap.add_argument("--shuffle", action="store_true",
                    help="barajar el orden de la lista en la 2ª pasada")
    ap.add_argument("--dry-run", action="store_true", help="solo estimar el coste, sin API")
    ap.add_argument("--workers", type=int, default=4, help="días en paralelo")
    ap.add_argument("-v", "--verbose", action="store_true", help="enseñar qué cambia cada día")
    args = ap.parse_args()
    if args.effort == "none":
        args.effort = None

    dates = args.dates or available()[-args.days:]
    if not dates:
        raise SystemExit("No hay nada en candidates/.")
    if args.dry_run:
        estimate(dates, args.model, args.vote)
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit('Falta ANTHROPIC_API_KEY. En PowerShell: $env:ANTHROPIC_API_KEY = "..."')

    import anthropic
    client = anthropic.Anthropic()
    print(f"Modelo {args.model}{f' (esfuerzo {args.effort})' if args.effort else ''} · "
          f"{len(dates)} días · {'votación de ' + str(args.vote) if args.vote > 1 else '1 selección'}"
          f" por pasada · 2ª pasada {'barajada' if args.shuffle else 'con la misma entrada'}")

    def safe(date):
        try:
            return run_day(client, date, args.shuffle, args.vote,
                           model=args.model, effort=args.effort)
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
    if args.model in summarize.PRICES:
        price_in, price_out = summarize.PRICES[args.model]
        cost = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
        print(f"\nTokens: {tokens_in} in / {tokens_out} out -> ${cost:.4f}")
    else:
        print(f"\nTokens: {tokens_in} in / {tokens_out} out (sin precios para {args.model})")

    out = ROOT / "work" / f"stability-{dt.datetime.now():%Y-%m-%dT%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": args.model, "effort": args.effort, "vote": args.vote,
                               "shuffle": args.shuffle, "days": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Detalle: {out}")


if __name__ == "__main__":
    main()
