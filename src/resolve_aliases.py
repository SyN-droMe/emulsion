"""Resolve Cookbook redirects across the ingredient vocabulary.

Does two jobs at once, which is why it is worth the ~40 API requests:

1. CATCHES TECHNIQUES. `Cookbook:Mince` and `Cookbook:Dice` are redirects to
   `Cookbook:Knife Skills`, so they are techniques wearing ingredient-shaped names.
   Hand-listing every such page is a losing game; resolving the redirect and checking
   the target is not.

2. MERGES SYNONYMS. If `Cookbook:Scallion` redirects to `Cookbook:Green onion`, then
   recipes linking the two names are using the SAME ingredient. Left unresolved they
   become separate vocabulary entries, which quietly weakens every ingredient
   similarity in the graph.

Outputs an alias map (name -> canonical name) and an extended blocklist.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests

API = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "recipe-explorer/0.1 (https://github.com/SyN-droMe; portfolio project)"
BATCH = 50

# Redirect targets that mean "this was never an ingredient".
TECHNIQUE_TARGETS = {
    "knife skills",
    "cooking techniques",
    "cooking terms",
    "weights and measures",
    "units of measurement",
    "cookbook units",
}


def resolve(names: list[str]) -> tuple[dict[str, str], set[str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    aliases: dict[str, str] = {}
    blocked: set[str] = set()

    for start in range(0, len(names), BATCH):
        chunk = names[start : start + BATCH]
        params = {
            "action": "query",
            "titles": "|".join(f"Cookbook:{n}" for n in chunk),
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
        }
        response = session.get(API, params=params, timeout=60)
        response.raise_for_status()
        body = response.json().get("query") or {}

        for entry in body.get("redirects") or []:
            source = entry["from"].replace("Cookbook:", "").strip()
            target = entry["to"].replace("Cookbook:", "").split("#")[0].strip()
            if target.lower() in TECHNIQUE_TARGETS:
                blocked.add(source.lower())
            else:
                aliases[source] = target

        print(
            f"  {min(start + BATCH, len(names))}/{len(names)} resolved "
            f"({len(aliases)} aliases, {len(blocked)} blocked)",
            flush=True,
        )
        time.sleep(0.2)

    return aliases, blocked


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve ingredient redirects")
    parser.add_argument("--vocab", type=Path, default=Path("data/vocab_raw.json"))
    parser.add_argument("--aliases-out", type=Path, default=Path("data/aliases.json"))
    parser.add_argument("--blocklist", type=Path, default=Path("data/blocklist.json"))
    args = parser.parse_args()

    # Raw, pre-alias names from parse.py. Reading data/recipes.json here instead would
    # feed this already-canonicalised names, so no redirect would be detected and the
    # map it writes would be empty.
    vocabulary = sorted(json.loads(args.vocab.read_text(encoding="utf-8")))
    print(f"resolving {len(vocabulary)} ingredient names")

    aliases, blocked = resolve(vocabulary)

    args.aliases_out.write_text(json.dumps(aliases, indent=1), encoding="utf-8")

    existing = set()
    if args.blocklist.exists():
        existing = {n for n in json.loads(args.blocklist.read_text(encoding="utf-8"))}
    merged = sorted(existing | {b.title() for b in blocked})
    args.blocklist.write_text(json.dumps(merged, indent=1), encoding="utf-8")

    print(f"\n{len(aliases)} synonym redirects -> {args.aliases_out}")
    print(f"{len(blocked)} technique redirects added to blocklist (now {len(merged)})")
    if aliases:
        sample = list(aliases.items())[:8]
        print("\nsample synonym merges:")
        for source, target in sample:
            print(f"  {source}  ->  {target}")


if __name__ == "__main__":
    main()