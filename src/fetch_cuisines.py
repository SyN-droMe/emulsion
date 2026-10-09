"""Derive real cuisine labels from Wikibooks page categories.

The `{{recipesummary|category=...}}` field turned out to be COURSE, not cuisine -- its
most common values are "Recipes for dessert", "Soup recipes", "Pasta recipes". Useful,
but it does not tell you where a dish is from, so colouring a map by it says nothing
about geography and the "route between cuisines" framing had nothing to stand on.

Page categories do carry it: recipes sit in categories like "Indian recipes",
"Thai recipes", "Mexican recipes". This fetches those and separates the two ideas:

  cuisine  -> geography, from categories matched against a nationality list
  course   -> dish type, from categories and the summary field
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path

import requests

API = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "recipe-explorer/0.1 (https://github.com/SyN-droMe; portfolio project)"
BATCH = 50

# Categories are free-form, so cuisine is identified by matching the leading word(s)
# against nationalities/regions rather than by trusting any single naming convention.
CUISINES = [
    "Indian", "Thai", "Chinese", "Japanese", "Korean", "Vietnamese", "Filipino",
    "Indonesian", "Malaysian", "Pakistani", "Bangladeshi", "Sri Lankan", "Nepalese",
    "Italian", "French", "Spanish", "Portuguese", "German", "British", "English",
    "Irish", "Scottish", "Greek", "Turkish", "Russian", "Polish", "Hungarian",
    "Swedish", "Norwegian", "Danish", "Finnish", "Dutch", "Belgian", "Austrian",
    "Swiss", "Czech", "Romanian", "Bulgarian", "Serbian", "Croatian", "Albanian",
    "Ukrainian", "Georgian", "Mexican", "American", "Canadian", "Brazilian",
    "Peruvian", "Argentine", "Argentinian", "Chilean", "Colombian", "Cuban",
    "Jamaican", "Caribbean", "Puerto Rican", "Nigerian", "Ghanaian", "Kenyan",
    "Ethiopian", "Egyptian", "Moroccan", "Tunisian", "Algerian", "South African",
    "Lebanese", "Syrian", "Israeli", "Iranian", "Persian", "Iraqi", "Saudi",
    "Afghan", "Australian", "New Zealand", "Hawaiian", "Cajun", "Creole", "Tex-Mex",
    "Sindhi", "Bengali", "Punjabi", "Gujarati", "Tamil", "Kerala", "Goan",
]
CUISINE_RE = re.compile(r"\b(" + "|".join(sorted(CUISINES, key=len, reverse=True)) + r")\b", re.I)

COURSE_WORDS = [
    "dessert", "beverage", "drink", "soup", "bread", "rice", "pasta", "sauce", "meat",
    "breakfast", "cookie", "salad", "cake", "chicken", "seafood", "fish", "vegetarian",
    "vegan", "snack", "appetizer", "side dish", "stew", "curry", "pie", "candy",
]


def fetch_categories(titles: list[str]) -> dict[str, list[str]]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    out: dict[str, list[str]] = {}

    for start in range(0, len(titles), BATCH):
        chunk = titles[start : start + BATCH]
        params = {
            "action": "query",
            "titles": "|".join(f"Cookbook:{t}" for t in chunk),
            "prop": "categories",
            "cllimit": "max",
            "format": "json",
            "formatversion": "2",
        }
        response = session.get(API, params=params, timeout=60)
        response.raise_for_status()
        body = response.json().get("query") or {}
        for page in body.get("pages") or []:
            title = page.get("title", "").replace("Cookbook:", "")
            cats = [
                c["title"].replace("Category:", "")
                for c in (page.get("categories") or [])
            ]
            out[title] = cats
        print(f"  {min(start + BATCH, len(titles))}/{len(titles)}", flush=True)
        time.sleep(0.2)
    return out


def classify(categories: list[str]) -> tuple[str, str]:
    cuisine = ""
    course = ""
    for category in categories:
        if not cuisine:
            match = CUISINE_RE.search(category)
            if match:
                cuisine = match.group(1).title()
        if not course:
            low = category.lower()
            for word in COURSE_WORDS:
                if word in low:
                    course = word.title()
                    break
    return cuisine, course


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch cuisine/course labels")
    parser.add_argument("--recipes", type=Path, default=Path("data/recipes.json"))
    parser.add_argument("--out", type=Path, default=Path("data/labels.json"))
    args = parser.parse_args()

    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    titles = [r["title"] for r in recipes]
    print(f"fetching categories for {len(titles)} recipes")

    categories = fetch_categories(titles)
    labels = {}
    for title in titles:
        cuisine, course = classify(categories.get(title, []))
        labels[title] = {"cuisine": cuisine, "course": course}

    args.out.write_text(json.dumps(labels, indent=1), encoding="utf-8")

    cuisines = Counter(v["cuisine"] for v in labels.values() if v["cuisine"])
    courses = Counter(v["course"] for v in labels.values() if v["course"])
    print(f"\ncuisine labels: {sum(cuisines.values())}/{len(titles)} recipes, "
          f"{len(cuisines)} distinct")
    for name, count in cuisines.most_common(12):
        print(f"   {name:16s} {count}")
    print(f"\ncourse labels: {sum(courses.values())}/{len(titles)}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
