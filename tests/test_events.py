"""Regression tests for coverage preservation and daily event comparisons."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build
import events
import summarize


def story(url, title, lang="en"):
    return {"url": url, "title": title, "summary": title, "lang": lang,
            "source": "Test", "published": "2026-09-21T05:00:00+00:00"}


def group(ids, previous=(), status="new", change="", section="economia"):
    return {"item_ids": ids, "primary_id": ids[0], "section": section,
            "continent": "europa", "previous_ids": list(previous),
            "status": status, "change_summary": change}


@pytest.fixture
def cells():
    return {"economia|europa": [story("https://a", "La UE multa a Google con 403 millones", "es")],
            "tecnologia|america": [story("https://b", "EU fines Google €403 million"),
                                   story("https://c", "Google appeals an earlier fine")]}


def test_cross_section_sources_preserved_and_distinct_appeal_kept(cells):
    out = events.apply_groups([group(["c0", "c1"]), group(["c2"])],
                              events.current_items(cells), {}, "2026-09-20", summarize.summary_language_ok)
    cards = out["economia|europa"]
    assert len(cards) == 2
    assert [s["url"] for s in cards[0]["sources"]] == ["https://a", "https://b"]
    assert cards[0]["sources"][1]["title"] == cells["tecnologia|america"][0]["title"]
    assert cards[1]["url"] == "https://c"
    assert all(c["status"] == "new" for c in cards)


@pytest.mark.parametrize("groups", [
    [group(["c0", "c1"])],
    [group(["c0", "c1"]), group(["c1", "c2"])],
    [group(["c0", "c1", "invented"])],
    [group(["c0", "c0", "c1", "c2"])],
    [{**group(["c0", "c1", "c2"]), "primary_id": "invented"}],
    [group(["c0", "c1", "c2"], previous=["invented"])],
])
def test_invalid_model_groups_keep_all_original_coverage(cells, groups):
    call = lambda *args: ({"groups": groups}, SimpleNamespace(input_tokens=100, output_tokens=50))
    out, meta = events.group_events(cells, None, None, call, summarize.summary_language_ok)
    assert out == cells
    assert meta["mode"] == "unavailable"
    assert meta["input_tokens"] == 100


def test_api_failure_keeps_original_coverage(cells):
    def fail(*args):
        raise RuntimeError("Test failure")
    out, meta = events.group_events(cells, None, None, fail, summarize.summary_language_ok)
    assert out == cells and meta["mode"] == "unavailable"


def test_stable_event_id_and_unchanged_last(cells):
    previous = {"p0": {**story("https://old", "Same fine"), "event_id": "stable"}}
    out = events.apply_groups([group(["c0", "c1"], ["p0"], "unchanged"), group(["c2"])],
                              events.current_items(cells), previous, "2026-09-20", summarize.summary_language_ok)
    cards = out["economia|europa"]
    assert cards[0]["url"] == "https://c"
    assert cards[1]["event_id"] == "stable"
    assert cards[1]["previous_event_id"] == "stable"
    assert cards[1]["previous_date"] == "2026-09-20"


@pytest.mark.parametrize("previous_date,status,change,expected", [
    (None, "updated", "La multa sube a 403 millones de euros.", "uncompared"),
    ("2026-09-20", "updated", "La multa sube a 403 millones de euros.", "updated"),
    ("2026-09-20", "updated", "", "unclear"),
    ("2026-09-20", "updated", "The fine is now higher than the previous fine.", "unclear"),
    ("2026-09-20", "new", "", "unclear"),
])
def test_comparison_does_not_claim_unverified_change(cells, previous_date, status, change, expected):
    out = events.apply_groups([group(["c0", "c1", "c2"], ["p0"], status, change)],
                              events.current_items(cells), {"p0": story("https://old", "Old fine")},
                              previous_date, summarize.summary_language_ok)
    card = out["economia|europa"][0]
    assert card["status"] == expected
    assert card["change_summary"] == (change if expected == "updated" else "")


def test_duplicate_url_is_preserved_once(cells):
    cells["politica|asia"] = cells["economia|europa"]
    assert len(events.current_items(cells)) == 3


def test_secondary_sources_marked_selected_and_present_in_rss(cells, monkeypatch, tmp_path):
    grouped = events.apply_groups([group(["c0", "c1"]), group(["c2"])],
                                  events.current_items(cells), {}, "2026-09-20", summarize.summary_language_ok)
    collected = {"items": [{"id": i, **s} for i, s in events.current_items(cells).items()],
                 "collected_at": "2026-09-21T05:00:00+00:00", "failed_sources": []}
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    saved = json.loads(summarize.save_candidates(collected, grouped, "2026-09-21").read_text())
    assert saved["selected"] == 3
    brief = {"date": "2026-09-21", "cells": grouped}
    xml = ElementTree.fromstring(build.build_rss(brief, "https://example.com/"))
    items = xml.findall("channel/item")
    assert len(items) == 2
    assert "https://b" in items[0].findtext("description")
    assert items[0].find("guid").attrib["isPermaLink"] == "false"
    assert items[0].findtext("guid").startswith("2026-09-21:")
