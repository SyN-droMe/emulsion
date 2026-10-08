"""Fetch the set of Cookbook pages that are techniques, not ingredients.

Ingredient lines link to technique pages as freely as to ingredients
(`[[Cookbook:Chopping|chopped]]`), and those links were showing up as the 4th most
common "ingredient" in the corpus. Rather than hand-maintaining a blocklist, pull
Wikibooks' own `Category:Cooking techniques` and let the source of truth maintain it.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

API = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "recipe-explorer/0.1 (https://github.com/SyN-droMe; portfolio project)"


def fetch_category(session: requests.Session, category: str) -> list[str]:
    titles: list[str] = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": "500",
        "cmnamespace": "102",
        "format": "json",
        "formatversion": "2",
    }
    while True:
        response = session.get(API, params=params, timeout=60)
        response.raise_for_status()
        body = response.json()
        for member in (body.get("query") or {}).get("categorymembers") or []:
            titles.append(member["title"].replace("Cookbook:", "").strip())
        cont = body.get("continue")
        if not cont:
            return titles
        params.update(cont)
        time.sleep(0.3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch technique blocklist")
    parser.add_argument("--out", type=Path, default=Path("data/blocklist.json"))
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    names: set[str] = set()
    # "Cookbook units" is the authoritative unit list (it contains oddities like "Each"
    # that a hand-written guess would miss). "Cooking techniques" covers cooking methods.
    # Neither covers knife work -- Cookbook:Chopping and Cookbook:Slicing are
    # uncategorised on Wikibooks, so those stay hardcoded in parse.py.
    for category in ("Cookbook units", "Cooking techniques"):
        try:
            found = fetch_category(session, category)
            print(f"  Category:{category}: {len(found)} pages")
            names.update(found)
        except requests.HTTPError as exc:
            print(f"  Category:{category}: skipped ({exc})")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(sorted(names), indent=1), encoding="utf-8")
    print(f"\nWrote {len(names)} technique pages to {args.out}")


if __name__ == "__main__":
    main()
