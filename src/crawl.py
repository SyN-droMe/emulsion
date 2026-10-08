"""Fetch Wikibooks Cookbook recipes via the MediaWiki API.

Source: en.wikibooks.org Cookbook, CC BY-SA 4.0. Using the sanctioned API rather than
scraping HTML, batched 50 pages per request (the API's limit for content queries), so
the whole ~3,800-recipe corpus is about 80 requests.

Output is one JSON per page in data/raw/, which doubles as the checkpoint: re-running
skips what already exists.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

API = "https://en.wikibooks.org/w/api.php"
# Wikimedia asks for a descriptive User-Agent identifying the tool and contact.
USER_AGENT = "recipe-explorer/0.1 (https://github.com/SyN-droMe; portfolio project)"
BATCH = 50
DELAY = 0.3


def slugify(title: str) -> str:
    return title.replace("Cookbook:", "").replace("/", "__").replace(" ", "_")[:150]


def crawl(category: str, out_dir: Path, limit: int = 0) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    params = {
        "action": "query",
        "generator": "categorymembers",
        "gcmtitle": f"Category:{category}",
        "gcmlimit": str(BATCH),
        "gcmnamespace": "102",  # the Cookbook: namespace
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    }

    fetched = 0
    skipped = 0
    requests_made = 0

    while True:
        response = session.get(API, params=params, timeout=60)
        response.raise_for_status()
        body = response.json()
        requests_made += 1

        pages = (body.get("query") or {}).get("pages") or []
        for page in pages:
            title = page.get("title", "")
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            content = (revisions[0].get("slots", {}).get("main", {}) or {}).get("content")
            if not content:
                continue

            path = out_dir / f"{slugify(title)}.json"
            if path.exists():
                skipped += 1
                continue
            path.write_text(
                json.dumps({"title": title, "pageid": page.get("pageid"), "wikitext": content}),
                encoding="utf-8",
            )
            fetched += 1

        print(f"  request {requests_made}: {fetched} new, {skipped} already had", flush=True)

        if limit and fetched >= limit:
            break
        cont = body.get("continue")
        if not cont:
            break
        params.update(cont)
        time.sleep(DELAY)

    print(f"\ndone: {fetched} new pages, {skipped} skipped, {requests_made} API requests")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl Wikibooks Cookbook recipes")
    parser.add_argument("--category", default="Recipes")
    parser.add_argument("--out-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    crawl(args.category, args.out_dir, args.limit)


if __name__ == "__main__":
    main()
