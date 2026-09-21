"""Evaluate event grouping on archived public news without publishing."""
import json
from pathlib import Path
import anthropic
from events import group_events
from summarize import call_json, summary_language_ok, PRICE_IN, PRICE_OUT

root = Path(__file__).resolve().parent.parent
today = json.loads((root / "data/2026-09-21.json").read_text())
yesterday = json.loads((root / "data/2026-09-20.json").read_text())
out = root / "evaluation"
out.mkdir(exist_ok=True)
def capture(*args):
    result, usage = call_json(*args)
    (out / "groups.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result, usage
cells, meta = group_events(today["cells"], yesterday, anthropic.Anthropic(), capture, summary_language_ok)
meta["cost_usd"] = round(meta["input_tokens"] / 1e6 * PRICE_IN + meta["output_tokens"] / 1e6 * PRICE_OUT, 4)
today.update(cells=cells, events=meta)
(out / "2026-09-21.json").write_text(json.dumps(today, ensure_ascii=False, indent=2))
cards = [s for stories in cells.values() for s in stories]
assert meta["mode"] == "full", meta
assert sum(len(s["sources"]) for s in cards) == meta["articles"]
fine = [s for s in cards if any("403" in p["title"] and "Google" in p["title"] for p in s["sources"])]
visits = [s for s in cards if any("Beijing confirms" in p["title"] or "China confirms Xi" in p["title"] for p in s["sources"])]
assert len(fine) == 1 and len(fine[0]["sources"]) >= 2, "Google fine duplicates remain"
assert len(visits) == 1 and len(visits[0]["sources"]) == 2, "Xi visit not grouped narrowly"
assert all("rare earth" not in p["title"].lower() and "AI" not in p["title"] for p in visits[0]["sources"])
print(json.dumps(meta))
for key, stories in cells.items():
    for s in stories:
        print(json.dumps({"cell": key, "status": s["status"], "change": s["change_summary"],
                          "titles": [p["title"] for p in s["sources"]]}, ensure_ascii=False))
