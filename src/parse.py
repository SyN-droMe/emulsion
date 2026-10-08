"""Parse Wikibooks Cookbook wikitext into structured recipes.

The payoff of this corpus is that ingredients arrive pre-linked to canonical pages --
`[[Cookbook:Fish Sauce|fish sauce]]` -- so we get normalised ingredient entities without
NLP-parsing "2 cloves garlic, chopped". Units are linked the same way, though, so the
measurement pages have to be filtered out or every recipe shares "cup" and "tablespoon"
and the ingredient similarity collapses.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

# Prep-technique pages that are linked inside ingredient lines but are not ingredients.
# These are hardcoded because they are UNCATEGORISED on Wikibooks -- Cookbook:Chopping
# and Cookbook:Slicing belong to no category at all, so the dynamic blocklist in
# fetch_blocklist.py cannot reach them. Everything that *is* categorised comes from
# there instead of being listed here.
PREP_PAGES = {
    "chopping", "slicing", "dicing", "mincing", "grating", "shredding", "peeling",
    "julienne", "grinding", "crushing", "zesting", "mashing", "sifting", "whisking",
    "kneading", "marinating", "seasoning", "garnish", "garnishing", "to taste",
}

# Measurement/unit pages that are linked like ingredients but are not ingredients.
# Kept as a fallback for when data/blocklist.json has not been fetched yet.
UNIT_PAGES = {
    "pound", "pounds", "gram", "grams", "kilogram", "kilograms", "ounce", "ounces",
    "cup", "cups", "milliliter", "millilitre", "liter", "litre", "tablespoon",
    "tablespoons", "teaspoon", "teaspoons", "quart", "quarts", "pint", "pints",
    "gallon", "gallons", "fluid ounce", "milligram", "inch", "inches", "centimeter",
    "centimetre", "celsius", "fahrenheit", "dash", "pinch", "stick", "sticks",
    "weights and measures", "metric", "us customary units",
}

# Section headings whose list items are ingredients.
INGREDIENT_HEADINGS = {"ingredients", "ingredient", "for the sauce", "for the dough"}
PROCEDURE_HEADINGS = {"procedure", "directions", "method", "preparation", "instructions"}

_ALIAS_CACHE: dict[str, str] | None = None


def _aliases(path: Path = Path("data/aliases.json")) -> dict[str, str]:
    """Synonym map from resolve_aliases.py, e.g. All-purpose flour -> Wheat Flour.

    Applied at parse time so the ingredient vocabulary is canonical before any
    similarity is computed -- otherwise two names for one ingredient look like two
    unrelated ingredients.
    """
    global _ALIAS_CACHE
    if _ALIAS_CACHE is None:
        _ALIAS_CACHE = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )
    return _ALIAS_CACHE


_BLOCKLIST_CACHE: set[str] | None = None


def _blocklist(path: Path = Path("data/blocklist.json")) -> set[str]:
    """Technique pages fetched from Wikibooks' own category (see fetch_blocklist.py)."""
    global _BLOCKLIST_CACHE
    if _BLOCKLIST_CACHE is None:
        if path.exists():
            _BLOCKLIST_CACHE = {n.lower() for n in json.loads(path.read_text(encoding="utf-8"))}
        else:
            _BLOCKLIST_CACHE = set()
    return _BLOCKLIST_CACHE


LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
TEMPLATE_FIELD = re.compile(r"\|\s*([A-Za-z _]+?)\s*=\s*([^|}\n]+)")
SECTION = re.compile(r"^={2,4}\s*(.+?)\s*={2,4}\s*$", re.M)


def strip_markup(text: str) -> str:
    """Flatten links to their display text and drop residual markup."""
    text = LINK.sub(lambda m: (m.group(2) or m.group(1)).replace("Cookbook:", ""), text)
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"'''?", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[\[File:[^\]]+\]\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_summary(wikitext: str) -> dict:
    match = re.search(r"\{\{recipesummary(.*?)\}\}", wikitext, re.S | re.I)
    if not match:
        return {}
    fields = {}
    for key, value in TEMPLATE_FIELD.findall(match.group(1)):
        key = key.strip().lower()
        if key in {"category", "servings", "time", "difficulty"}:
            fields[key] = strip_markup(value)
    return fields


def split_sections(wikitext: str) -> list[tuple[str, str]]:
    """[(heading_lowercase, body)] with a leading '' section for the intro."""
    parts: list[tuple[str, str]] = []
    matches = list(SECTION.finditer(wikitext))
    if not matches:
        return [("", wikitext)]
    parts.append(("", wikitext[: matches[0].start()]))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(wikitext)
        parts.append((match.group(1).strip().lower(), wikitext[match.end() : end]))
    return parts


def parse_recipe(title: str, wikitext: str) -> dict | None:
    sections = split_sections(wikitext)

    ingredients: list[str] = []
    ingredient_lines: list[str] = []
    steps: list[str] = []

    for heading, body in sections:
        is_ingredients = any(h in heading for h in INGREDIENT_HEADINGS)
        is_procedure = any(h in heading for h in PROCEDURE_HEADINGS)

        if is_ingredients:
            # Format-agnostic on purpose. Roughly a quarter of recipes put ingredients
            # in a wikitable rather than a bullet list, and a bullet-only parser
            # silently discarded all of them ("Afghan Bread", "Afang Soup", ...). Every
            # format still links ingredients the same way, so harvest links from the
            # whole section and let the blocklist do the filtering.
            for target, _ in LINK.findall(body):
                name = target.replace("Cookbook:", "").strip()
                if not name or name.startswith(("File:", "Image:", "Category:")):
                    continue
                name = _aliases().get(name, name)  # canonicalise synonyms first
                low = name.lower()
                if low in UNIT_PAGES or low in PREP_PAGES or low in _blocklist():
                    continue
                ingredients.append(name)
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("*"):
                    ingredient_lines.append(strip_markup(line.lstrip("*").strip()))

        if is_procedure:
            for line in body.splitlines():
                line = line.strip()
                # Numbered steps are the norm; a few recipes use bullets instead.
                if (line.startswith("#") and not line.startswith("#*")) or line.startswith("*"):
                    text = strip_markup(line.lstrip("#*").strip())
                    if text and len(text) > 3:
                        steps.append(text)

    # A recipe with no ingredients or no method is not usable as a graph node.
    if len(ingredients) < 2 or not steps:
        return None

    summary = parse_summary(wikitext)
    return {
        "title": title.replace("Cookbook:", ""),
        "ingredients": sorted(set(ingredients)),
        "ingredient_lines": ingredient_lines,
        "steps": steps,
        "procedure_text": " ".join(steps),
        "cuisine": summary.get("category", ""),
        "servings": summary.get("servings", ""),
        "time": summary.get("time", ""),
        "difficulty": summary.get("difficulty", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse crawled wikitext into recipes")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--out", type=Path, default=Path("data/recipes.json"))
    args = parser.parse_args()

    recipes = []
    rejected = 0
    for path in sorted(args.raw_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        parsed = parse_recipe(payload["title"], payload["wikitext"])
        if parsed is None:
            rejected += 1
            continue
        recipes.append(parsed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(recipes, indent=1), encoding="utf-8")

    counts = Counter(i for r in recipes for i in r["ingredients"])
    with_cuisine = sum(1 for r in recipes if r["cuisine"])
    print(f"parsed {len(recipes)} recipes ({rejected} rejected as unusable)")
    print(f"  distinct ingredients: {len(counts)}")
    print(f"  median ingredients/recipe: "
          f"{sorted(len(r['ingredients']) for r in recipes)[len(recipes)//2]}")
    print(f"  median steps/recipe: {sorted(len(r['steps']) for r in recipes)[len(recipes)//2]}")
    print(f"  have cuisine label: {with_cuisine}/{len(recipes)}")
    print(f"\n  top ingredients: {', '.join(n for n, _ in counts.most_common(15))}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
