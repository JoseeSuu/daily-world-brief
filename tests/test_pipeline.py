# -*- coding: utf-8 -*-
"""Tests mínimos: parseo de feeds, esquema del JSON diario y build del HTML."""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build as build_mod  # noqa: E402
import collect  # noqa: E402
import summarize  # noqa: E402

RSS_SAMPLE = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>Titular de prueba &amp; algo</title>
<link>https://example.com/a</link>
<description><![CDATA[<p>Un   extracto con <b>HTML</b> que limpiar.</p>]]></description>
<pubDate>{date}</pubDate></item>
</channel></rss>"""


class FakeResponse:
    status_code = 200
    def __init__(self, content): self.content = content


def test_parse_feed(monkeypatch):
    now = dt.datetime.now(dt.timezone.utc)
    date = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
    monkeypatch.setattr(collect.requests, "get",
                        lambda *a, **k: FakeResponse(RSS_SAMPLE.replace(b"{date}", date.encode())))
    feed = {"name": "Test", "url": "https://x", "section": "economia", "continent": "asia", "lang": "es"}
    res = collect.parse_feed(feed, now)
    assert "items" in res and len(res["items"]) == 1
    item = res["items"][0]
    assert item["title"] == "Titular de prueba & algo"
    assert "<" not in item["excerpt"] and "HTML" in item["excerpt"]
    assert item["section"] == "economia" and item["continent"] == "asia"


def test_parse_feed_discards_old(monkeypatch):
    # Ítems más viejos que la ventana se descartan, pero el feed NO cuenta
    # como caído (p. ej. BCE en fin de semana): devuelve lista vacía.
    now = dt.datetime.now(dt.timezone.utc)
    old = (now - dt.timedelta(days=10)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    monkeypatch.setattr(collect.requests, "get",
                        lambda *a, **k: FakeResponse(RSS_SAMPLE.replace(b"{date}", old.encode())))
    feed = {"name": "Test", "url": "https://x", "section": "economia", "continent": "asia"}
    assert collect.parse_feed(feed, now) == {"items": []}


def test_feeds_yaml_schema():
    feeds = collect.load_feeds()
    assert len(feeds) >= 20
    for f in feeds:
        assert f["section"] in ("economia", "politica", "tecnologia"), f
        assert f["continent"] in ("asia", "europa", "america", "global"), f
        assert f["url"].startswith("http"), f


def _sample_collected():
    return {
        "collected_at": "2026-01-01T05:00:00+00:00",
        "failed_sources": [{"name": "Roto", "error": "HTTP 404"}],
        "items": [
            {"id": f"id{i}", "source": "Test", "section": s, "continent": c, "lang": "en",
             "title": f"Noticia {i}", "url": f"https://e.com/{i}",
             "published": "2026-01-01T04:00:00+00:00", "excerpt": "x"}
            for i, (s, c) in enumerate(
                (s, c) for s in ("economia", "politica", "tecnologia")
                       for c in ("asia", "europa", "america", "global"))
        ],
    }


def test_fallback_schema():
    cells = summarize.run_fallback(_sample_collected())
    assert isinstance(cells, dict)
    for key, items in cells.items():
        section, continent = key.split("|")
        assert section in summarize.SECTIONS and continent in summarize.CONTINENTS
        for it in items:
            for field in ("headline", "summary", "source", "url", "original_title"):
                assert field in it
            for lang in ("es", "en", "zh"):
                assert lang in it["headline"] and lang in it["summary"]


def test_build_html(tmp_path, monkeypatch):
    # data/ temporal con un brief válido
    monkeypatch.setattr(build_mod, "DATA", tmp_path / "data")
    monkeypatch.setattr(build_mod, "SITE", tmp_path / "site")
    build_mod.DATA.mkdir()
    brief = {
        "date": "2026-01-01", "generated_at": "2026-01-01T05:30:00+00:00",
        "mode": "full", "cost_usd": 0.01, "market": None, "failed_sources": [],
        "cells": {"economia|asia": [{
            "headline": {"es": "Hola", "en": "Hello", "zh": "你好"},
            "summary": {"es": "R.", "en": "S.", "zh": "摘要。"},
            "original_title": "Orig", "source": "Test",
            "url": "https://e.com/1", "published": "2026-01-01T04:00:00+00:00"}]},
    }
    (build_mod.DATA / "2026-01-01.json").write_text(
        json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    index = build_mod.build("https://test.example/")
    html_text = index.read_text(encoding="utf-8")
    assert "__BRIEF_JSON__" not in html_text and "__DATES_JSON__" not in html_text
    assert "你好" in html_text  # el JSON va inyectado
    assert (build_mod.SITE / "data" / "2026-01-01.json").exists()
    assert (build_mod.SITE / "feed.xml").exists()
    assert (build_mod.SITE / "manifest.json").exists()
    assert (build_mod.SITE / "sw.js").exists()
    feed = (build_mod.SITE / "feed.xml").read_text(encoding="utf-8")
    assert "<rss" in feed and "https://e.com/1" in feed
