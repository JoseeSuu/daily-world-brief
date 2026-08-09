# -*- coding: utf-8 -*-
"""Auditoría de la selección: enseña, por celda, qué se publicó y qué se
descartó, para juzgar si el modelo elige bien o se queda corto.

    python scripts/review.py                 # el día más reciente
    python scripts/review.py 2026-08-12      # un día concreto
    python scripts/review.py --week          # resumen de los últimos 7 días
"""
import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAND = ROOT / "candidates"
SECTIONS = ["economia", "politica", "tecnologia"]
CONTINENTS = ["asia", "europa", "america"]


def load(date: str) -> dict:
    return json.loads((CAND / f"{date}.json").read_text(encoding="utf-8"))


def available() -> list:
    return sorted(f.stem for f in CAND.glob("????-??-??.json"))


def show_day(date: str, max_discarded: int) -> None:
    d = load(date)
    print(f"\n{'=' * 72}\n{date}  ·  {d['total']} recogidas  ·  "
          f"{d['seen_by_model']} vistas por el modelo  ·  {d['selected']} publicadas")
    if d["failed_sources"]:
        print(f"fuentes caidas: {', '.join(f['name'] for f in d['failed_sources'])}")
    print("=" * 72)

    by_section = collections.defaultdict(list)
    for it in d["items"]:
        by_section[it["section"]].append(it)

    for sec in SECTIONS:
        pool = by_section.get(sec, [])
        sel = [i for i in pool if i["selected"]]
        seen = [i for i in pool if i["seen_by_model"]]
        unseen = [i for i in pool if not i["seen_by_model"]]
        print(f"\n### {sec.upper()}  —  {len(pool)} candidatas, "
              f"{len(seen)} vistas, {len(sel)} publicadas")

        print(f"\n  PUBLICADAS ({len(sel)}):")
        for i in sel:
            print(f"    ✓ [{i['lang']}] {i['source'][:22]:22s} {i['title'][:74]}")

        rejected = [i for i in seen if not i["selected"]]
        print(f"\n  DESCARTADAS aun habiendolas visto ({len(rejected)}"
              f", muestro {min(len(rejected), max_discarded)}):")
        for i in rejected[:max_discarded]:
            print(f"    · [{i['lang']}] {i['source'][:22]:22s} {i['title'][:74]}")

        if unseen:
            print(f"\n  NUNCA LLEGARON AL MODELO ({len(unseen)}, "
                  f"cortadas por el tope de {d['seen_by_model']}):")
            for i in unseen[:max_discarded]:
                print(f"    ? [{i['lang']}] {i['source'][:22]:22s} {i['title'][:74]}")


def show_week(dates: list) -> None:
    print(f"\n{'fecha':12s} {'recogidas':>10s} {'vistas':>7s} {'publicadas':>11s} "
          f"{'% visto':>8s}")
    tot = collections.Counter()
    src_sel, src_all = collections.Counter(), collections.Counter()
    for date in dates:
        d = load(date)
        pct = 100 * d["seen_by_model"] / d["total"] if d["total"] else 0
        print(f"{date:12s} {d['total']:10d} {d['seen_by_model']:7d} "
              f"{d['selected']:11d} {pct:7.0f}%")
        tot["total"] += d["total"]
        tot["seen"] += d["seen_by_model"]
        tot["sel"] += d["selected"]
        for it in d["items"]:
            src_all[it["source"]] += 1
            if it["selected"]:
                src_sel[it["source"]] += 1

    print(f"\nTOTAL {len(dates)} dias: {tot['total']} recogidas, "
          f"{tot['seen']} vistas, {tot['sel']} publicadas")

    print("\nTasa de publicacion por fuente (publicadas / recogidas):")
    rows = [(s, src_sel.get(s, 0), n, 100 * src_sel.get(s, 0) / n)
            for s, n in src_all.items() if n >= 5]
    for s, sel, n, pct in sorted(rows, key=lambda r: -r[3]):
        print(f"  {s[:30]:30s} {sel:4d}/{n:4d}  {pct:5.1f}%")
    dead = [s for s, sel, n, _ in rows if sel == 0]
    if dead:
        print(f"\nFuentes que NUNCA se publicaron: {', '.join(dead)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD (por defecto, el ultimo)")
    ap.add_argument("--week", action="store_true", help="resumen de los ultimos 7 dias")
    ap.add_argument("-n", type=int, default=15, help="cuantas descartadas mostrar")
    args = ap.parse_args()

    dates = available()
    if not dates:
        raise SystemExit("No hay nada en candidates/ todavia. Se archiva a partir "
                         "de la primera ejecucion posterior a este cambio.")
    if args.week:
        show_week(dates[-7:])
    else:
        show_day(args.date or dates[-1], args.n)


if __name__ == "__main__":
    main()
