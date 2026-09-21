"""Test source failures, evidence boundaries and radar publication."""
import datetime as dt
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import radar
import summarize
import build


def candidate(i, title=None):
    return {"id": str(i), "title": title or f"Project {i}", "url": f"https://example.org/{i}",
            "source": "GitHub", "kind": "project", "published": None, "lang": "en",
            "discussion_url": "", "excerpt": "Document extraction", "signal": {"stars_total": 20}}


def test_hn_recency_and_failed_items(monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    rows = {1: {"id": 1, "title": "Show HN: LLM document analysis", "type": "story", "time": now.timestamp()},
            2: {"id": 2, "title": "AI old post", "type": "story", "time": (now-dt.timedelta(days=5)).timestamp()},
            3: {"id": 3, "title": "AI hidden", "dead": True},
            4: {"id": 4, "title": "AI link", "type": "story", "time": now.timestamp(), "url": "javascript:alert(1)"}}

    def fetch(url):
        if "topstories" in url:
            return [1, 2, 3, 4, 5]
        iid = int(url.rsplit("/", 1)[1].split(".")[0])
        if iid == 5:
            raise requests.Timeout()
        return rows[iid]

    monkeypatch.setattr(radar, "get_json", fetch)
    items, failed = radar.hn_items(now)
    assert [i["id"] for i in items] == ["hn-1"]
    assert failed == 1
    assert items[0]["url"] == items[0]["discussion_url"]


def test_source_failure_keeps_other_source(monkeypatch):
    def fail(now):
        raise requests.HTTPError()
    monkeypatch.setattr(radar, "github_items", fail)
    monkeypatch.setattr(radar, "hn_items", lambda now: ([candidate(1)], 2))
    result = radar.collect_radar()
    assert len(result["items"]) == 1
    assert {f["name"] for f in result["failed_sources"]} == {"GitHub", "Hacker News"}


def test_github_search_is_recent_not_daily_trending(monkeypatch):
    def fetch(url, **kwargs):
        assert "created:>=2026-08-22" in kwargs["params"]["q"]
        return {"items": [{"id": 7, "full_name": "org/extract", "html_url": "https://github.com/org/extract",
                           "created_at": "2026-09-20T10:00:00Z", "description": "中文 OCR",
                           "stargazers_count": 50}]}
    monkeypatch.setattr(radar, "get_json", fetch)
    rows = radar.github_items(dt.datetime(2026, 9, 21, tzinfo=dt.timezone.utc))
    assert rows[0]["signal"] == {"stars_total": 50}
    assert rows[0]["lang"] == "zh"


def test_selection_rejects_unknown_duplicate_ids_and_caps_three(monkeypatch, tmp_path):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    def select(client, prompt, schema, max_tokens):
        assert "no README" in prompt and "not reliability" in prompt
        return {"stories": [{"id": str(i), "summary": "The author describes an extraction tool.",
                              "usefulness": "It could help compare document workflows.",
                              "to_verify": "Check extraction accuracy on a public sample."}
                             for i in [99, 0, 0, 1, 2, 3]]}, SimpleNamespace(input_tokens=100, output_tokens=100)
    monkeypatch.setattr(summarize, "call_json", select)
    items = [candidate(i, title) for i, title in enumerate(["Amber", "Birch", "Cedar", "Dune"])]
    result, cost = summarize.run_radar({"radar": {"items": items}}, {})
    assert [i["id"] for i in result["items"]] == ["0", "1", "2"]
    assert cost > 0 and result["mode"] == "full"


@pytest.mark.parametrize("with_key", [False, True])
def test_unavailable_selection_never_fabricates_cards(monkeypatch, tmp_path, with_key):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    if with_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    def fail(*args):
        raise RuntimeError("API unavailable")
    monkeypatch.setattr(summarize, "call_json", fail)
    result, cost = summarize.run_radar({"radar": {"items": [candidate(1)]}}, {})
    assert result["mode"] == "unavailable" and result["items"] == [] and cost == 0


def test_radar_excludes_current_news_and_previous_week(monkeypatch, tmp_path):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    yesterday = dt.date.today() - dt.timedelta(days=1)
    (tmp_path / "data" / f"{yesterday}.json").write_text(json.dumps({"radar": {"items": [candidate(2)]}}))
    result, _ = summarize.run_radar({"radar": {"items": [candidate(1), candidate(2)]}},
                                  {"tecnologia|america": [candidate(1)]})
    assert result["items"] == [] and result["mode"] == "empty"


@pytest.mark.parametrize("field", ["summary", "usefulness", "to_verify"])
def test_rejects_wrong_language_in_any_radar_field(monkeypatch, tmp_path, field):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    item = {**candidate(1), "lang": "zh"}
    row = {"id": "1", "summary": "作者介绍了一个工具。", "usefulness": "可能帮助分析文档。",
           "to_verify": "需要验证结果准确性。"}
    row[field] = "The author describes a tool for document extraction."
    def select(client, prompt, schema, max_tokens):
        assert "every field below in SIMPLIFIED CHINESE" in prompt
        return {"stories": [row]}, SimpleNamespace(input_tokens=100, output_tokens=100)
    monkeypatch.setattr(summarize, "call_json", select)
    result, cost = summarize.run_radar({"radar": {"items": [item]}}, {})
    assert result["items"] == [] and result["mode"] == "unavailable" and cost > 0


def test_build_includes_radar_rss_and_escapes_script_end(monkeypatch, tmp_path):
    monkeypatch.setattr(build, "DATA", tmp_path / "data")
    monkeypatch.setattr(build, "SITE", tmp_path / "site")
    build.DATA.mkdir()
    item = {**candidate(1), "title": "</script><script>alert(1)</script>",
            "summary": "An author claim", "usefulness": "Potential use", "to_verify": "Not tested"}
    brief = {"date": "2026-09-21", "cells": {}, "radar": {"items": [item], "failed_sources": [], "mode": "full"}}
    (build.DATA / "2026-09-21.json").write_text(json.dumps(brief))
    page = build.build().read_text(encoding="utf-8")
    assert item["title"] not in page and "\\u003c/script>" in page
    assert item["url"] in (build.SITE / "feed.xml").read_text(encoding="utf-8")


def test_candidate_audit_records_radar_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    item = candidate(1)
    collected = {"items": [], "collected_at": "2026-09-21", "failed_sources": [], "radar": {"items": [item]}}
    selection = {"seen_ids": ["1"], "items": [item]}
    path = summarize.save_candidates(collected, {}, "2026-09-21", selection)
    row = json.loads(path.read_text(encoding="utf-8"))["radar"]["items"][0]
    assert row["seen_by_model"] and row["selected"] and row["excerpt"] == item["excerpt"]


def test_main_publishes_news_when_radar_cannot_run(monkeypatch, tmp_path):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(summarize, "get_market", lambda: None)
    item = {**candidate(1), "section": "tecnologia", "continent": "europa"}
    collected = {"items": [item], "collected_at": "2026-09-21", "failed_sources": [],
                 "radar": {"items": [candidate(2, "Different discovery")]}}
    path = tmp_path / "collected.json"
    path.write_text(json.dumps(collected))
    monkeypatch.setattr(sys, "argv", ["summarize.py", str(path)])
    summarize.main()
    brief = json.loads(next((tmp_path / "data").glob("*.json")).read_text())
    assert brief["cells"]["tecnologia|europa"][0]["url"] == item["url"]
    assert brief["radar"]["mode"] == "unavailable"
    assert brief["cost_usd"] == 0 and brief["radar_cost_usd"] == 0
