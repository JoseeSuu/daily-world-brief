"""Collect public AI discovery signals without treating popularity as verification."""
import concurrent.futures as cf
import datetime as dt
import html
import re
from urllib.parse import urlsplit

import requests

HN = "https://hacker-news.firebaseio.com/v0"
AI_TERMS = re.compile(r"\b(ai|llms?|gpt|claude|rag|ocr|agents?|machine learning|artificial intelligence)\b", re.I)


def get_json(url, **kwargs):
    response = requests.get(url, timeout=15,
                            headers={"User-Agent": "DailyWorldBrief/1.0"}, **kwargs)
    response.raise_for_status()
    return response.json()


def web_url(url):
    try:
        parts = urlsplit(url or "")
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def github_items(now):
    since = (now - dt.timedelta(days=30)).date().isoformat()
    result = get_json("https://api.github.com/search/repositories", params={
        "q": f"topic:llm created:>={since} archived:false fork:false stars:>=10",
        "sort": "stars", "order": "desc", "per_page": 15,
    })
    if result.get("incomplete_results"):
        raise ValueError("Incomplete GitHub search")
    return [{
        "id": f"gh-{r['id']}", "source": "GitHub", "kind": "project",
        "title": r["full_name"], "url": r["html_url"], "discussion_url": "",
        "published": r["created_at"],
        "lang": "zh" if re.search(r"[一-鿿]{2}", r.get("description") or "") else "en",
        "excerpt": (r.get("description") or "")[:600],
        "signal": {"stars_total": r["stargazers_count"]},
    } for r in result["items"] if web_url(r.get("html_url"))]


def hn_items(now):
    ids = get_json(f"{HN}/topstories.json")[:60]

    def fetch(iid):
        try:
            return get_json(f"{HN}/item/{iid}.json"), None
        except (requests.RequestException, ValueError) as ex:
            return None, type(ex).__name__

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(fetch, ids))
    items = []
    for row, error in responses:
        if not row or row.get("dead") or row.get("deleted") or row.get("type") != "story":
            continue
        published = dt.datetime.fromtimestamp(row["time"], dt.timezone.utc)
        title = html.unescape(row.get("title", ""))
        if not dt.timedelta(0) <= now - published <= dt.timedelta(hours=48):
            continue
        if not AI_TERMS.search(title):
            continue
        discussion = f"https://news.ycombinator.com/item?id={row['id']}"
        url = row.get("url") or discussion
        if web_url(url):
            items.append({
                "id": f"hn-{row['id']}", "source": "Hacker News", "kind": "discussion",
                "title": title, "url": url, "discussion_url": discussion,
                "published": published.isoformat(), "lang": "en",
                "excerpt": html.unescape(re.sub(r"<[^>]+>", " ", row.get("text", "")))[:600],
                "signal": {"points": row.get("score", 0), "comments": row.get("descendants", 0)},
            })
    failed = sum(error is not None for _, error in responses)
    return items[:15], failed


def collect_radar():
    now = dt.datetime.now(dt.timezone.utc)
    items, failed = [], []
    for name, fetch in (("GitHub", github_items), ("Hacker News", hn_items)):
        try:
            result = fetch(now)
            if name == "Hacker News":
                result, errors = result
                if errors:
                    failed.append({"name": name, "error": f"{errors} items unavailable"})
            items.extend(result)
        except (requests.RequestException, ValueError, KeyError, TypeError) as ex:
            failed.append({"name": name, "error": type(ex).__name__})
    return {"collected_at": now.isoformat(), "items": items, "failed_sources": failed}
