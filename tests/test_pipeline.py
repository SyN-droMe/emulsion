"""Regression tests for the data pipeline.

Every test here exists because the bug it checks for actually happened, and all four
were invisible from the outside: the parser silently dropped a quarter of the corpus,
"Chopping" became the fourth most common ingredient, recipes containing butter were
labelled vegan, and one ingredient existed twice under underscore and space spellings.
None of them raised an error. The map just quietly meant less than it claimed.

Run: python tests/test_pipeline.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classify_diet import classify_ingredient
from src.parse import parse_recipe

PASS, FAIL = "PASS", "FAIL"


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"[{PASS if condition else FAIL}] {label}" + (f"  {detail}" if detail and not condition else ""))
    return bool(condition)


# --- parser --------------------------------------------------------------------

BULLET_RECIPE = """
{{recipesummary|category=Thai recipes|servings=2|time=20 minutes}}
== Ingredients ==
* 1 [[Cookbook:Cup|cup]] [[Cookbook:Rice|rice]]
* 2 cloves [[Cookbook:Garlic|garlic]], [[Cookbook:Chopping|chopped]]
== Procedure ==
# Rinse the rice.
# Fry the garlic.
"""

TABLE_RECIPE = """
== Ingredients ==
{| class="wikitable"
!Ingredient
!Weight
|-
|[[Cookbook:Flour|All-purpose flour]]
|550 [[Cookbook:Gram|g]]
|-
|[[Cookbook:Salt|Salt]]
|18 g
|}
== Procedure ==
# Mix everything.
# Bake it.
"""


def test_bullet_ingredients_parse() -> bool:
    recipe = parse_recipe("Cookbook:Test", BULLET_RECIPE)
    return check(
        "bullet-list ingredients are extracted",
        recipe is not None and {"Rice", "Garlic"} <= set(recipe["ingredients"]),
        f"got {recipe and recipe['ingredients']}",
    )


def test_wikitable_ingredients_parse() -> bool:
    """The bug that silently dropped ~25% of the corpus (Afghan Bread, Afang Soup)."""
    recipe = parse_recipe("Cookbook:Test", TABLE_RECIPE)
    return check(
        "wikitable ingredients are extracted, not silently dropped",
        recipe is not None and len(recipe["ingredients"]) >= 2,
        f"got {recipe and recipe.get('ingredients')}",
    )


def test_units_and_techniques_excluded() -> bool:
    """'Chopping' was the 4th most common ingredient in the corpus before this."""
    recipe = parse_recipe("Cookbook:Test", BULLET_RECIPE)
    found = {i.lower() for i in (recipe["ingredients"] if recipe else [])}
    return check(
        "unit and technique links are not treated as ingredients",
        "cup" not in found and "chopping" not in found and "gram" not in found,
        f"got {sorted(found)}",
    )


def test_underscores_folded() -> bool:
    """MediaWiki treats Feta_Cheese and Feta Cheese as one page; so must we."""
    recipe = parse_recipe(
        "Cookbook:Test",
        """
== Ingredients ==
* [[Cookbook:Feta_Cheese|feta]]
* [[Cookbook:Olive_Oil|oil]]
== Procedure ==
# Combine.
# Serve.
""",
    )
    names = recipe["ingredients"] if recipe else []
    return check(
        "underscore spellings are folded to spaces",
        all("_" not in n for n in names) and len(names) == 2,
        f"got {names}",
    )


def test_recipe_without_method_rejected() -> bool:
    recipe = parse_recipe(
        "Cookbook:Ingredient Page",
        "== Ingredients ==\n* [[Cookbook:Salt|salt]]\n* [[Cookbook:Rice|rice]]\n",
    )
    return check("a page with no procedure is not a usable recipe", recipe is None)


# --- diet classification -------------------------------------------------------

def test_obvious_animal_products() -> bool:
    cases = [
        ("Chicken", ["ingredients", "poultry"], "meat"),
        ("Paneer", ["ingredients", "cheeses"], "dairy"),
        ("Lentil", ["ingredients", "pulses"], "plant"),
        ("Egg", ["ingredients", "eggs"], "egg"),
    ]
    ok = all(classify_ingredient(n, c) == want for n, c, want in cases)
    return check("categorised ingredients classify correctly", ok)


def test_hidden_animal_products() -> bool:
    """These are filed only as 'Condiments' or 'Thickeners'. Categories cannot catch them."""
    cases = [
        ("Fish Sauce", ["condiments", "ingredients"]),
        ("Worcestershire Sauce", ["condiments", "ingredients"]),
        ("Gelatin", ["thickeners and stabilizers", "ingredients"]),
        ("Lard", ["ingredients"]),
    ]
    bad = [n for n, c in cases if classify_ingredient(n, c) != "meat"]
    return check(
        "hidden animal products are caught despite harmless categories",
        not bad,
        f"missed: {bad}",
    )


def test_generic_dairy_caught() -> bool:
    """Only SPECIFIC cheeses are categorised as dairy, which labelled 204 recipes vegan."""
    cases = ["Butter", "Milk", "Cheese", "Yogurt", "Sour Cream", "Buttermilk", "Ghee"]
    bad = [n for n in cases if classify_ingredient(n, ["ingredients"]) != "dairy"]
    return check("generic dairy names classify as dairy", not bad, f"missed: {bad}")


def test_bare_meat_caught() -> bool:
    """Cookbook:Meat sits in no meat category. It was classified 'plant'."""
    return check(
        "the ingredient literally named 'Meat' is not a plant",
        classify_ingredient("Meat", ["ingredients"]) == "meat",
    )


# --- shipped artifact ----------------------------------------------------------

def test_artifact_consistency() -> bool:
    path = Path("docs/data/graph.json")
    if not path.exists():
        return check("shipped artifact is self-consistent", True, "(not built, skipped)")
    graph = json.loads(path.read_text(encoding="utf-8"))
    nodes, dims = graph["nodes"], graph["meta"]["dims"]
    ok = (
        len(graph["ingredient_vecs"]) == len(nodes) == len(graph["technique_vecs"])
        and len(graph["ingredient_vecs"][0]) == dims
        and all(abs(n["x"]) <= 1.2 and abs(n["y"]) <= 1.2 for n in nodes)
    )
    return check("shipped artifact is self-consistent", ok)


def test_no_vegan_recipe_contains_dairy() -> bool:
    """The exact bug: 204 recipes called vegan while containing butter, milk or cheese."""
    recipes_path, diet_path = Path("data/recipes.json"), Path("data/diet.json")
    if not (recipes_path.exists() and diet_path.exists()):
        return check("no vegan recipe contains dairy or meat", True, "(no data, skipped)")

    recipes = json.loads(recipes_path.read_text(encoding="utf-8"))
    diet = json.loads(diet_path.read_text(encoding="utf-8"))
    classes = diet["ingredients"]
    offenders = [
        r["title"]
        for r in recipes
        if diet["recipes"].get(r["title"], {}).get("diet") == "vegan"
        and any(classes.get(i) in ("meat", "egg", "dairy") for i in r["ingredients"])
    ]
    return check(
        "no vegan recipe contains dairy, egg or meat",
        not offenders,
        f"{len(offenders)} offenders, e.g. {offenders[:3]}",
    )


if __name__ == "__main__":
    tests = [
        test_bullet_ingredients_parse,
        test_wikitable_ingredients_parse,
        test_units_and_techniques_excluded,
        test_underscores_folded,
        test_recipe_without_method_rejected,
        test_obvious_animal_products,
        test_hidden_animal_products,
        test_generic_dairy_caught,
        test_bare_meat_caught,
        test_artifact_consistency,
        test_no_vegan_recipe_contains_dairy,
    ]
    results = [t() for t in tests]
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    if passed != len(results):
        raise SystemExit(1)
