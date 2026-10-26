# -*- coding: utf-8 -*-
"""Tests de scripts/stability.py sin llamar a la API."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import stability  # noqa: E402
import summarize  # noqa: E402

TOPICS = ["Fed raises interest rates", "Japan election results announced",
          "Nvidia unveils new chip", "ECB holds deposit rate", "India monsoon floods",
          "Apple antitrust ruling", "Brazil cuts fuel taxes", "Korea export surge"]


def _collected():
    return {"collected_at": "2026-01-01T05:00:00+00:00", "failed_sources": [], "items": [
        {"id": f"id{n}", "source": f"Src{n % 3}", "section": s, "continent": "global",
         "lang": "en", "title": f"{TOPICS[n % len(TOPICS)]} {s} {n}",
         "url": f"https://e.com/{n}", "published": None, "excerpt": "x"}
        for n, s in enumerate(["economia", "politica", "tecnologia"] * 4)]}


class FakeClient:
    """Elige siempre los dos primeros ids de la lista que recibe. Responde
    como los modelos 5.x: un bloque thinking vacío antes del texto."""
    def __init__(self, stop_reason="end_turn"):
        self.listings, self.calls, self.stop_reason = [], [], stop_reason
        self.messages = SimpleNamespace(create=self.create)

    def create(self, messages, **kwargs):
        self.calls.append(kwargs)
        rows = messages[0]["content"].split("ITEMS:\n", 1)[1].splitlines()
        ids = [r.split("|", 1)[0] for r in rows]
        self.listings.append(ids)
        text = json.dumps({"cells": [{"continent": "asia", "item_ids": ids[:2]}]})
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=text)],
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def test_compare_matches_events_across_outlets():
    titles = {"a1": "Fed raises interest rates by a quarter point",
              "a2": "Japan election results announced",
              "b1": "Fed raises interest rates by quarter point again",
              "b2": "Nvidia unveils new chip"}
    r = stability.compare(["a1", "a2", "x"], ["b1", "b2", "x"],
                          {**titles, "x": "Brazil cuts fuel taxes"})
    assert r["same_id"] == 1
    assert r["twins"] == [("a1", "b1")] and r["same_event"] == 2
    assert r["only_a"] == ["a2"] and r["only_b"] == ["b2"]
    assert stability.dice(r["same_event"], r["a"], r["b"]) == 2 * 2 / 6


def test_model_input_reproduces_what_the_selector_saw(tmp_path, monkeypatch):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    monkeypatch.setattr(summarize, "MAX_INPUT_ITEMS", 7)
    summarize.save_candidates(_collected(), {}, "2026-01-01")
    monkeypatch.setattr(stability, "CAND", tmp_path / "candidates")
    items = stability.model_input(stability.load("2026-01-01"))
    assert [i["id"] for i in items] == [
        i["id"] for i in summarize.interleave_by_source(_collected()["items"])[:7]]


def test_run_day_compares_two_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    collected = _collected()
    cells = {"economia|asia": [{**collected["items"][0], "summary": ""}]}
    summarize.save_candidates(collected, cells, "2026-01-01")
    monkeypatch.setattr(stability, "CAND", tmp_path / "candidates")

    client = FakeClient()
    r = stability.run_day(client, "2026-01-01", shuffle=False)
    assert len(client.listings) == 6  # 3 secciones x 2 pasadas
    assert client.listings[:3] == client.listings[3:]  # misma entrada en ambas
    assert r["a"] == r["b"] and r["ab"]["same_id"] == len(r["a"]) == 6
    assert r["moved"] == [] and r["usage"] == [600, 60]
    assert r["production"]["b"] == 1 and r["production"]["same_id"] == 1

    shuffled = FakeClient()
    stability.run_day(shuffled, "2026-01-01", shuffle=True)
    assert shuffled.listings[:3] != shuffled.listings[3:]
    assert [sorted(x) for x in shuffled.listings[:3]] == [sorted(x) for x in shuffled.listings[3:]]


def test_call_json_reads_text_after_thinking_and_passes_effort():
    client = FakeClient()
    prompt = "ITEMS:\nid1|Src|global|Fed raises interest rates"
    result, _ = summarize.call_json(client, prompt, {}, 100, model="claude-haiku-5-5", effort="high")
    assert result["cells"][0]["item_ids"] == ["id1"]
    assert client.calls[0]["model"] == "claude-haiku-5-5"
    assert client.calls[0]["output_config"]["effort"] == "high"
    summarize.call_json(client, prompt, {}, 100)  # Haiku 4.5 rechaza effort: no se envía
    assert "effort" not in client.calls[1]["output_config"]
    assert client.calls[1]["model"] == summarize.MODEL


def test_call_json_rejects_incomplete_answers():
    for reason in ("max_tokens", "refusal"):
        with pytest.raises(RuntimeError, match=reason):
            summarize.call_json(FakeClient(stop_reason=reason), "ITEMS:\nx|y|z|w", {}, 100)


def test_combine_votes_keeps_the_majority():
    e = "economia"
    runs = [{"x": (e, "asia"), "y": (e, "asia"), "z": (e, "europa")},
            {"x": (e, "europa"), "y": (e, "asia")},
            {"x": (e, "europa"), "w": (e, "asia")}]
    assert summarize.combine_votes(runs) == {"x": (e, "europa"), "y": (e, "asia")}
    # empate de continente: gana el de la primera pasada que la eligió
    assert summarize.combine_votes([{"x": (e, "asia")}, {"x": (e, "europa")},
                                    {}]) == {"x": (e, "asia")}


def test_combine_votes_caps_cells_by_votes_then_order():
    e, cell = "economia", ("economia", "asia")
    full = {f"id{n}": cell for n in range(7)}
    runs = [full, full, {"id6": cell, "id5": cell}]
    kept = summarize.combine_votes(runs)
    assert len(kept) == summarize.MAX_PER_CELL
    assert {"id5", "id6"} <= set(kept)  # tres votos van primero
    assert {"id0", "id1", "id2"} <= set(kept)


def test_run_day_with_vote(tmp_path, monkeypatch):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    summarize.save_candidates(_collected(), {}, "2026-01-01")
    monkeypatch.setattr(stability, "CAND", tmp_path / "candidates")
    client = FakeClient()
    r = stability.run_day(client, "2026-01-01", False, vote=3,
                          model="claude-haiku-5-5", effort="high")
    assert len(client.listings) == 18  # 2 pasadas x 3 votos x 3 secciones
    assert all(c["model"] == "claude-haiku-5-5" for c in client.calls)
    assert len(r["raw_a"]) == len(r["raw_b"]) == len(r["single"]) == 3
    assert r["a"] == r["b"] and r["usage"] == [1800, 180]
    assert stability.pooled(r["single"]) == 1.0


class FlakyClient(FakeClient):
    """Falla las primeras `fail` llamadas con un rechazo del modelo."""
    def __init__(self, fail):
        super().__init__()
        self.fail = fail

    def create(self, messages, **kwargs):
        reply = super().create(messages, **kwargs)
        if len(self.calls) <= self.fail:
            reply.stop_reason = "refusal"
        return reply


def _economia_items():
    return [{"id": f"id{n}", "source": "Src", "section": "economia", "continent": "global",
             "title": TOPICS[n]} for n in range(4)]


def test_select_by_vote_survives_a_failed_pass():
    # una sola sección: la 1ª pasada falla y quedan 2, que deben coincidir
    chosen, usage, runs = summarize.select_by_vote(FlakyClient(fail=1), _economia_items(), 3)
    assert len(runs) == 2 and set(chosen) == {"id0", "id1"} and usage == [200, 20]
    # si solo queda una pasada, vale lo que eligió
    chosen, _, runs = summarize.select_by_vote(FlakyClient(fail=2), _economia_items(), 3)
    assert len(runs) == 1 and set(chosen) == {"id0", "id1"}
    with pytest.raises(RuntimeError, match="refusal"):
        summarize.select_by_vote(FlakyClient(fail=3), _economia_items(), 3)


def test_run_selection_falls_back_to_the_old_selection():
    client = FlakyClient(fail=3)
    chosen, cost, meta = summarize.run_selection(client, _economia_items())
    assert set(chosen) == {"id0", "id1"}
    assert meta["model"] == summarize.MODEL and meta["passes_ok"] == 1
    assert client.calls[-1]["model"] == summarize.MODEL
    assert "effort" not in client.calls[-1]["output_config"]
    assert cost == pytest.approx((100 * summarize.PRICE_IN + 10 * summarize.PRICE_OUT) / 1e6)

    chosen, cost, meta = summarize.run_selection(FakeClient(), _economia_items())
    assert meta == {"model": "claude-haiku-5-5", "effort": "high", "passes": 3,
                    "passes_ok": 3, "cost_usd": round(cost, 4)}
