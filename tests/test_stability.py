# -*- coding: utf-8 -*-
"""Tests de scripts/stability.py sin llamar a la API."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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
    """Elige siempre los dos primeros ids de la lista que recibe."""
    def __init__(self):
        self.listings = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, messages, **_):
        rows = messages[0]["content"].split("ITEMS:\n", 1)[1].splitlines()
        ids = [r.split("|", 1)[0] for r in rows]
        self.listings.append(ids)
        text = json.dumps({"cells": [{"continent": "asia", "item_ids": ids[:2]}]})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
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
