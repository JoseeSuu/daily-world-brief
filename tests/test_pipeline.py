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
            for field in ("title", "summary", "lang", "source", "url", "published"):
                assert field in it, (key, it)
            assert it["lang"] in ("es", "en", "zh")


def test_dedupe_cells():
    # Caso real: la misma noticia de inflación china desde dos agencias.
    cells = {
        "economia|asia": [
            {"title": "China’s monthly inflation cools as impact from Iran war eases",
             "summary": "", "lang": "en", "source": "South China Morning Post (Asia)",
             "url": "https://a", "published": None},
            {"title": "China’s Inflation Cools as Iran War Oil Shock Starts to Ease",
             "summary": "", "lang": "en", "source": "Bloomberg Economics",
             "url": "https://b", "published": None},
        ],
        "politica|america": [
            {"title": "Israel rejects Trump's 15-point plan for Gaza, Netanyahu says",
             "summary": "", "lang": "en", "source": "NPR World",
             "url": "https://c", "published": None},
        ],
        "politica|europa": [
            {"title": "Netanyahu rejects Trump's Gaza 15-point plan, Israel says",
             "summary": "", "lang": "en", "source": "BBC World",
             "url": "https://d", "published": None},
        ],
    }
    out, removed = summarize.dedupe_cells(cells)
    assert removed == 2
    assert sum(len(v) for v in out.values()) == 2
    # gana la fuente más autorizada (Bloomberg sobre SCMP, BBC sobre NPR)
    kept = {s["source"] for v in out.values() for s in v}
    assert kept == {"Bloomberg Economics", "BBC World"}
    # las celdas que quedan vacías desaparecen
    assert all(v for v in out.values())


def test_dedupe_keeps_distinct_stories():
    cells = {"tecnologia|america": [
        {"title": "Amazon data center could become the biggest climate polluter in Texas",
         "summary": "", "lang": "en", "source": "The Guardian World",
         "url": "https://a", "published": None},
        {"title": "DeepMind's hurricane breakthrough has surprised weather scientists",
         "summary": "", "lang": "en", "source": "Ars Technica",
         "url": "https://b", "published": None},
    ]}
    out, removed = summarize.dedupe_cells(cells)
    assert removed == 0 and len(out["tecnologia|america"]) == 2


def test_same_story_chinese():
    # sin espacios entre palabras: la comparación usa bigramas de caracteres
    assert summarize.same_story("中国七月通胀放缓至今年最低水平",
                                "中国通胀放缓 七月消费者价格涨幅最低")
    assert not summarize.same_story("中国七月通胀放缓至今年最低水平",
                                    "台湾2027年国防预算将增长16%")


def test_summary_language_check():
    ok = summarize.summary_language_ok
    # correctos
    assert ok("El Gobierno de España aprueba la reforma de las pensiones.", "es")
    assert ok("The government approved the pension reform on Monday.", "en")
    assert ok("中国政府批准了新的养老金改革方案。", "zh")
    assert ok("", "es")  # sin resumen (fallback) no se marca
    # el bug real detectado: artículo en inglés resumido en español
    assert not ok("Puerto Rico impone cortes de agua rotatorios de 48 horas "
                  "para los clientes de la isla.", "en")
    # chino donde no toca, y falta de chino donde sí toca
    assert not ok("中国政府批准了新的养老金改革方案。", "en")
    assert not ok("The government approved the reform.", "zh")
    # nombres propios en inglés dentro de un resumen español no dan falso positivo
    assert ok("Amazon Web Services anuncia un centro de datos en Aragón.", "es")


def test_detect_lang():
    # el idioma declarado en feeds.yaml manda…
    assert summarize.detect_lang({"title": "Hello world", "lang": "en"}) == "en"
    assert summarize.detect_lang({"title": "Hola mundo", "lang": "es"}) == "es"
    # …salvo que el titular sea claramente chino
    assert summarize.detect_lang({"title": "中国经济增长放缓", "lang": "en"}) == "zh"
    # idioma desconocido o ausente -> en
    assert summarize.detect_lang({"title": "Bonjour", "lang": "fr"}) == "en"
    assert summarize.detect_lang({"title": "No lang key"}) == "en"


def test_save_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    collected = _sample_collected()
    # simulamos que solo una de las candidatas acabó publicada
    published_url = collected["items"][0]["url"]
    cells = {"economia|asia": [{
        "title": "x", "summary": "y", "lang": "en", "source": "Test",
        "url": published_url, "published": None}]}

    out = summarize.save_candidates(collected, cells, "2026-08-10")
    saved = json.loads(out.read_text(encoding="utf-8"))

    assert saved["total"] == len(collected["items"])
    assert saved["selected"] == 1
    assert saved["seen_by_model"] == len(collected["items"])  # caben todas
    marked = [i for i in saved["items"] if i["selected"]]
    assert len(marked) == 1 and marked[0]["url"] == published_url
    # se archiva lo que ve el selector, sin el extracto
    assert "excerpt" not in saved["items"][0]
    for field in ("source", "section", "continent", "lang", "title", "url"):
        assert field in saved["items"][0]


def test_save_candidates_marks_unseen(tmp_path, monkeypatch):
    # con más candidatas que el tope, las sobrantes quedan marcadas como no vistas
    monkeypatch.setattr(summarize, "ROOT", tmp_path)
    monkeypatch.setattr(summarize, "MAX_INPUT_ITEMS", 5)
    collected = _sample_collected()
    assert len(collected["items"]) > 5
    saved = json.loads(
        summarize.save_candidates(collected, {}, "2026-08-10").read_text(encoding="utf-8"))
    assert saved["seen_by_model"] == 5
    assert sum(1 for i in saved["items"] if not i["seen_by_model"]) == saved["total"] - 5
    assert saved["selected"] == 0


def test_build_html(tmp_path, monkeypatch):
    # data/ temporal con un brief válido
    monkeypatch.setattr(build_mod, "DATA", tmp_path / "data")
    monkeypatch.setattr(build_mod, "SITE", tmp_path / "site")
    build_mod.DATA.mkdir()
    brief = {
        "date": "2026-01-01", "generated_at": "2026-01-01T05:30:00+00:00",
        "mode": "full", "cost_usd": 0.01, "market": None, "failed_sources": [],
        "cells": {"economia|asia": [{
            "title": "中国经济数据公布", "summary": "摘要。", "lang": "zh",
            "source": "FT中文网",
            "url": "https://e.com/1", "published": "2026-01-01T04:00:00+00:00"}]},
    }
    (build_mod.DATA / "2026-01-01.json").write_text(
        json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    index = build_mod.build("https://test.example/")
    html_text = index.read_text(encoding="utf-8")
    assert "__BRIEF_JSON__" not in html_text and "__DATES_JSON__" not in html_text
    assert "中国经济数据公布" in html_text  # el JSON va inyectado
    assert (build_mod.SITE / "data" / "2026-01-01.json").exists()
    assert (build_mod.SITE / "feed.xml").exists()
    assert (build_mod.SITE / "manifest.json").exists()
    assert (build_mod.SITE / "sw.js").exists()
    feed = (build_mod.SITE / "feed.xml").read_text(encoding="utf-8")
    assert "<rss" in feed and "https://e.com/1" in feed
