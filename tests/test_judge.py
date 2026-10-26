# -*- coding: utf-8 -*-
"""Tests de scripts/judge.py sin llamar a la API."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import judge  # noqa: E402
import stability  # noqa: E402
import summarize  # noqa: E402

ITEMS = [{"id": "id1", "source": "BBC World", "title": "Fed raises interest rates"},
         {"id": "id2", "source": "The Economist", "title": "Why the euro needs reform"},
         {"id": "id3", "source": "NPR World", "title": "Messi scores twice in final"}]


class FakeJudge:
    """Marca como opinión id2 y como deporte id3, tras un bloque thinking."""
    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = json.dumps({"breaks": [{"id": "id2", "kind": "opinion_analysis"},
                                      {"id": "id3", "kind": "sport"},
                                      {"id": "inventado", "kind": "other"}]})
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=text)],
            stop_reason="end_turn", usage=SimpleNamespace(input_tokens=500, output_tokens=50))


def _archive(tmp_path, monkeypatch):
    day = {"date": "2026-01-01", "items": [
        {**i, "selected": i["id"] == "id1"} for i in ITEMS]}
    (tmp_path / "2026-01-01.json").write_text(json.dumps(day), encoding="utf-8")
    monkeypatch.setattr(stability, "CAND", tmp_path)


def test_judge_day_uses_quality_rules_and_fallback():
    client = FakeJudge()
    breaks, usage = judge.judge_day(client, ITEMS)
    assert breaks == {"id2": "opinion_analysis", "id3": "sport"}  # descarta ids inventados
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["extra_body"] == {"fallbacks": "default"}
    assert "server-side-fallback-2026-07-01" in call["betas"]
    assert summarize.QUALITY_RULES in call["messages"][0]["content"]
    assert "id2|The Economist|Why the euro needs reform" in call["messages"][0]["content"]


def test_versions_and_pending(tmp_path, monkeypatch):
    _archive(tmp_path, monkeypatch)
    test = {"model": "claude-haiku-5-5", "vote": 3, "effort": "high", "days": [
        {"date": "2026-01-01", "a": {"id1": ["economia", "america"]},
         "b": {"id2": ["economia", "europa"]}}]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(test), encoding="utf-8")
    tests = judge.load_tests([path])
    assert tests[0][0] == "1. claude-haiku-5-5 ×3 high"
    selected = judge.versions(tests, ["2026-01-01"])
    assert selected["publicado"] == {"2026-01-01": ["id1"]}
    assert selected["1. claude-haiku-5-5 ×3 high"] == {"2026-01-01": ["id1", "id2"]}
    todo = judge.pending(selected, {})
    assert [i["id"] for i in todo["2026-01-01"]] == ["id1", "id2"]
    # lo ya juzgado no se vuelve a pagar
    cache = {"2026-01-01": {"judged": ["id1", "id2"], "breaks": {"id2": "opinion_analysis"}}}
    assert judge.pending(selected, cache) == {}
