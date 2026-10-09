"""Classify ingredients as animal-derived, then label each recipe veg / non-veg / vegan.

This is derived, not given -- Wikibooks has no diet field -- so it is done in two layers
because neither alone is trustworthy:

1. CATEGORIES handle the obvious cases well: Cookbook:Chicken is in "Poultry",
   Cookbook:Paneer is in "Cheeses", Cookbook:Lentil is in "Pulses".

2. AN EXPLICIT TRAP LIST handles what categories miss, which is unfortunately the
   important part. Cookbook:Fish Sauce and Cookbook:Worcestershire Sauce are both filed
   only as "Condiments", and Cookbook:Gelatin as "Thickeners and stabilizers" -- nothing
   in their categories reveals they are animal-derived. A filter that calls a fish-sauce
   recipe vegetarian is worse than no filter at all.

Convention used is the Indian one, since that is the common meaning of "veg/non-veg":
egg counts as NON-veg, dairy does not. Western "vegetarian" usually includes egg, so the
distinction is surfaced in the data (`has_egg`, `has_dairy`) rather than baked into one
opaque label.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import requests

API = "https://en.wikibooks.org/w/api.php"
USER_AGENT = "recipe-explorer/0.1 (https://github.com/SyN-droMe; portfolio project)"
BATCH = 50

MEAT_CATEGORIES = {
    "poultry", "meat", "meats", "beef", "pork", "lamb", "game", "offal",
    "seafood", "fish", "shellfish", "cured meats", "sausages",
}
EGG_CATEGORIES = {"eggs", "egg"}
DAIRY_CATEGORIES = {"cheeses", "dairy", "dairy products", "milk products"}

# Animal-derived ingredients whose Wikibooks categories do NOT reveal it.
TRAP_NON_VEG = {
    "fish sauce", "oyster sauce", "worcestershire sauce", "anchovy", "anchovies",
    "gelatin", "gelatine", "lard", "suet", "tallow", "rennet", "isinglass",
    "bonito", "bonito flakes", "katsuobushi", "dashi", "shrimp paste", "belacan",
    "caviar", "roe", "fish stock", "chicken stock", "beef stock", "chicken broth",
    "beef broth", "bone broth", "carmine", "cochineal", "schmaltz", "duck fat",
    "bacon fat", "fish paste", "nam pla", "colatura", "garum", "surimi",
    "pepperoni", "chorizo", "prosciutto", "pancetta", "guanciale", "lardo",
}
TRAP_EGG = {"mayonnaise", "aioli", "meringue", "custard", "eggnog", "hollandaise"}

# Words that, in an ingredient NAME, are reliable enough on their own. Kept deliberately
# short -- this is a backstop for uncategorised pages, not the primary mechanism.
NAME_MEAT_WORDS = (
    "chicken", "beef", "pork", "lamb", "mutton", "veal", "venison", "duck", "goose",
    "turkey", "bacon", "ham", "sausage", "salami", "fish", "salmon", "tuna", "cod",
    "prawn", "shrimp", "crab", "lobster", "squid", "octopus", "clam", "mussel",
    "oyster", "scallop", "snail", "goat meat", "liver", "kidney", "tripe", "brisket",
    "sardine", "mackerel", "herring", "trout", "eel", "crayfish",
    # Generic terms. "Meat" itself was being classified as plant, because
    # Cookbook:Meat sits in no meat category and nothing matched the bare word.
    "meat", "poultry", "seafood", "shellfish", "gammon", "jerky", "pate", "broth",
)

# Dairy backstop by name. Wikibooks only files SPECIFIC cheeses under "Cheeses", so
# generic Butter / Cheese / Yogurt / Sour Cream / Buttermilk all fell through to
# "plant" -- which labelled 204 recipes vegan while they contained dairy, Alfredo
# Sauce among them.
NAME_DAIRY_WORDS = (
    "butter", "milk", "cream", "cheese", "yogurt", "yoghurt", "ghee", "curd",
    "paneer", "whey", "kefir", "mascarpone", "ricotta", "custard", "creme",
    "half-and-half", "quark", "labneh", "skyr",
)


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
        for page in (response.json().get("query") or {}).get("pages") or []:
            name = page.get("title", "").replace("Cookbook:", "")
            out[name] = [
                c["title"].replace("Category:", "").lower()
                for c in (page.get("categories") or [])
            ]
        print(f"  {min(start + BATCH, len(titles))}/{len(titles)}", flush=True)
        time.sleep(0.2)
    return out


def classify_ingredient(name: str, categories: list[str]) -> str:
    """-> 'meat' | 'egg' | 'dairy' | 'plant'"""
    low = name.lower()
    if low in TRAP_NON_VEG:
        return "meat"
    if low in TRAP_EGG:
        return "egg"
    cats = set(categories)
    if cats & MEAT_CATEGORIES:
        return "meat"
    if cats & EGG_CATEGORIES:
        return "egg"
    if cats & DAIRY_CATEGORIES:
        return "dairy"
    if any(word in low for word in NAME_MEAT_WORDS):
        return "meat"
    if low.startswith("egg") or low.endswith(" egg") or low.endswith(" eggs"):
        return "egg"
    # Checked after meat so that e.g. "buttermilk fried chicken" stays non-veg.
    if any(word in low for word in NAME_DAIRY_WORDS):
        return "dairy"
    return "plant"


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify ingredients and recipe diets")
    parser.add_argument("--recipes", type=Path, default=Path("data/recipes.json"))
    parser.add_argument("--out", type=Path, default=Path("data/diet.json"))
    args = parser.parse_args()

    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    vocabulary = sorted({i for r in recipes for i in r["ingredients"]})
    print(f"classifying {len(vocabulary)} ingredients")

    categories = fetch_categories(vocabulary)
    ingredient_class = {
        name: classify_ingredient(name, categories.get(name, [])) for name in vocabulary
    }

    diets = {}
    for recipe in recipes:
        classes = {ingredient_class.get(i, "plant") for i in recipe["ingredients"]}
        has_meat = "meat" in classes
        has_egg = "egg" in classes
        has_dairy = "dairy" in classes
        if has_meat or has_egg:
            diet = "non-veg"
        elif has_dairy:
            diet = "veg"
        else:
            diet = "vegan"
        diets[recipe["title"]] = {
            "diet": diet,
            "has_meat": has_meat,
            "has_egg": has_egg,
            "has_dairy": has_dairy,
        }

    args.out.write_text(
        json.dumps({"ingredients": ingredient_class, "recipes": diets}, indent=1),
        encoding="utf-8",
    )

    counts = Counter(v["diet"] for v in diets.values())
    classes = Counter(ingredient_class.values())
    print(f"\ningredient classes: {dict(classes)}")
    print(f"recipe diets: {dict(counts)}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
