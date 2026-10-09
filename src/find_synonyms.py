"""Find ingredient pairs that are the same food under two names.

resolve_aliases.py catches synonyms that Wikibooks itself records as a redirect
(Ghee -> Clarified Butter). It cannot catch the ones kept as two separate pages:
Cilantro and Coriander, Aubergine and Eggplant. Those stay as distinct vocabulary
entries and quietly weaken every similarity score, and when a recipe happens to link
both they inflate its ingredient count too, which is how "Clarified Butter and Ghee in
the same dish" got noticed.

Ingredient pages state their own alternative names in the first paragraph ("Clarified
butter, also called ghee, ...", "Corn or maize is a grain"). This reads those claims
and reports pairs where BOTH names are in our vocabulary, so the output is a candidate
list grounded in Wikibooks' own prose rather than in string similarity.

Writes data/synonym_candidates.json. Deliberately does not auto-merge: "pepper" meaning
bell pepper and "pepper" meaning black pepper is the kind of thing that needs a human.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests

API = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "recipe-explorer/0.1 (https://github.com/SyN-droMe; portfolio project)"
BATCH = 20

CLAIM_PATTERNS = [
    re.compile(r"also (?:called|known as|referred to as)\s+([^.;()]+)", re.I),
    re.compile(r"sometimes (?:ambiguously )?known (?:simply )?as\s+([^.;()]+)", re.I),
    re.compile(r"go by other names including\s+([^.;()]+)", re.I),
    re.compile(r"^\s*([A-Za-z][A-Za-z' -]+?)\s+or\s+([A-Za-z][A-Za-z' -]+?)\s+is\b", re.I),
]
# Phrases that signal the sentence is distinguishing two things, not equating them.
NEGATIVE = re.compile(r"\b(?:not to be confused|different from|unlike|whereas)\b", re.I)


def candidate_names(text: str) -> set[str]:
    names: set[str] = set()
    body = text.split("\n")[-1] if "\n" in text else text
    if NEGATIVE.search(body):
        return names
    for pattern in CLAIM_PATTERNS:
        for match in pattern.finditer(body):
            for group in match.groups():
                if not group:
                    continue
                for piece in re.split(r",| or | and ", group):
                    piece = piece.strip().strip('"\u201c\u201d\u2018\u2019').strip()
                    # Alternative names are short; a long span is a sentence fragment.
                    if 2 < len(piece) < 28 and piece.count(" ") <= 2:
                        names.add(piece.lower())
    return names


def fetch_intros(titles: list[str], session: requests.Session) -> dict[str, str]:
    out: dict[str, str] = {}
    for start in range(0, len(titles), BATCH):
        chunk = titles[start : start + BATCH]
        params = {
            "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
            "format": "json", "formatversion": 2, "redirects": 1,
            "titles": "|".join(f"Cookbook:{t}" for t in chunk),
        }
        data = session.get(API, params=params, timeout=40).json()
        for page in data.get("query", {}).get("pages", []):
            title = page.get("title", "").replace("Cookbook:", "")
            if page.get("extract"):
                out[title] = page["extract"]
        print(f"  {min(start + BATCH, len(titles))}/{len(titles)}", flush=True)
        time.sleep(0.2)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Find same-food-two-names pairs")
    parser.add_argument("--recipes", type=Path, default=Path("data/recipes.json"))
    parser.add_argument("--out", type=Path, default=Path("data/synonym_candidates.json"))
    args = parser.parse_args()

    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    vocabulary = sorted({i for r in recipes for i in r["ingredients"]})
    lower = {v.lower(): v for v in vocabulary}
    uses = {v: sum(1 for r in recipes if v in r["ingredients"]) for v in vocabulary}
    print(f"reading intros for {len(vocabulary)} ingredient pages")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    intros = fetch_intros(vocabulary, session)

    pairs: list[dict] = []
    for name, text in intros.items():
        if name not in uses:
            continue
        for alt in candidate_names(text):
            if alt == name.lower() or alt not in lower:
                continue
            other = lower[alt]
            if other == name:
                continue
            a, b = sorted([name, other], key=lambda n: -uses[n])
            pairs.append({
                "keep": a, "merge": b, "keep_uses": uses[a], "merge_uses": uses[b],
                "cooccur": sum(
                    1 for r in recipes
                    if a in r["ingredients"] and b in r["ingredients"]
                ),
                "claimed_by": name,
            })

    seen, unique = set(), []
    for p in sorted(pairs, key=lambda p: -(p["keep_uses"] + p["merge_uses"])):
        key = (p["keep"], p["merge"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)

    args.out.write_text(json.dumps(unique, indent=1), encoding="utf-8")
    print(f"\n{len(unique)} candidate pairs\n")
    header = f"{'keep':22s} {'merge':22s} {'uses':>10s} {'both':>5s}"
    print(header)
    print("-" * len(header))
    for p in unique[:40]:
        print(f"{p['keep'][:21]:22s} {p['merge'][:21]:22s} "
              f"{p['keep_uses']:4d}+{p['merge_uses']:<5d} {p['cooccur']:5d}")
    total = sum(p["cooccur"] for p in unique)
    print(f"\nrecipes listing both names of a pair: {total}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
