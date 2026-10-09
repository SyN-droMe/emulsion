"""Build the two embedding spaces, the 2D layout, and the shippable artifact.

Two modalities, deliberately different in kind -- that contrast is the whole point of
the ingredients/technique slider:

  ingredients -> TF-IDF over canonical ingredient entities. Crisp and interpretable.
                 TF-IDF rather than raw set overlap because salt/sugar/flour appear in
                 a quarter of all recipes; weighting by rarity is what makes "both use
                 tamarind" count for more than "both use salt".

  technique   -> sentence embeddings over the procedure text. Fuzzy and semantic, so
                 "braise" and "simmer gently" land near each other even with no shared
                 vocabulary.

Everything is reduced with PCA before shipping so the browser can recompute similarity
at any slider position locally -- no backend, no round trip. That is the architectural
difference from wave.fm, which needs a running FastAPI server to answer queries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

TEXT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
PCA_DIMS = 64


def build_ingredient_space(recipes: list[dict]) -> tuple[np.ndarray, list[str]]:
    """TF-IDF over ingredient entities, treating each recipe as a bag of entities."""
    vectorizer = TfidfVectorizer(
        analyzer=lambda recipe_ingredients: recipe_ingredients,  # already tokenised
        min_df=2,  # an ingredient used once can only create a spurious private cluster
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform([r["ingredients"] for r in recipes])
    return normalize(matrix.toarray().astype(np.float32)), list(vectorizer.get_feature_names_out())


def build_technique_space(recipes: list[dict]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(TEXT_MODEL)
    texts = [r["procedure_text"][:2000] for r in recipes]
    vectors = model.encode(texts, batch_size=64, show_progress_bar=True, convert_to_numpy=True)
    return normalize(vectors.astype(np.float32))


def reduce_space(matrix: np.ndarray, dims: int) -> np.ndarray:
    dims = min(dims, matrix.shape[1], matrix.shape[0])
    reduced = PCA(n_components=dims, random_state=0).fit_transform(matrix)
    return normalize(reduced.astype(np.float32))


def separate(coords: np.ndarray, min_sep: float = 0.014, iterations: int = 12) -> np.ndarray:
    """Push apart points that land on top of each other.

    UMAP happily stacks near-identical recipes at the same coordinate -- this corpus has
    clusters like Guacamole I/II/III and Chocolate Chip Cookies I-IV with ~0.99
    ingredient similarity, so they collapse into one unclickable dot. A few relaxation
    passes separate them enough to hover individually while leaving the global structure
    intact (displacement is capped well below cluster spacing).

    Grid-bucketed so it stays O(n) per pass rather than comparing all 3226^2 pairs.
    """
    coords = coords.copy()
    for _ in range(iterations):
        buckets: dict[tuple[int, int], list[int]] = {}
        for index, (x, y) in enumerate(coords):
            buckets.setdefault((int(x / min_sep), int(y / min_sep)), []).append(index)

        shift = np.zeros_like(coords)
        for (cell_x, cell_y), members in buckets.items():
            nearby = [
                other
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                for other in buckets.get((cell_x + dx, cell_y + dy), ())
            ]
            for a in members:
                for b in nearby:
                    if b <= a:
                        continue
                    delta = coords[a] - coords[b]
                    distance = float(np.hypot(*delta))
                    if distance >= min_sep:
                        continue
                    if distance < 1e-9:
                        # Exactly coincident: pick a deterministic direction by index so
                        # repeated builds produce the same layout.
                        angle = (a * 2.399963) % (2 * np.pi)
                        delta = np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)
                        distance = 1e-6
                    push = (delta / distance) * (min_sep - distance) * 0.5
                    shift[a] += push
                    shift[b] -= push
        coords += shift
    return coords


def build_layout(blended: np.ndarray, seed: int = 0) -> np.ndarray:
    """2D coordinates for the map.

    The layout is computed ONCE on the blended space and then held fixed. Recomputing
    it per slider position would make nodes jump around as you drag, which destroys any
    sense of a stable place -- the slider changes which edges exist, not where things
    live.
    """
    try:
        import umap

        coords = umap.UMAP(
            n_neighbors=15, min_dist=0.25, metric="cosine", random_state=seed
        ).fit_transform(blended)
    except Exception as exc:  # noqa: BLE001 - UMAP is optional, fall back gracefully
        print(f"  UMAP unavailable ({exc}); falling back to PCA layout")
        coords = PCA(n_components=2, random_state=seed).fit_transform(blended)

    coords = np.asarray(coords, dtype=np.float32)
    # Normalise to roughly [-1, 1] so the frontend does not need to know the scale.
    centre = coords.mean(axis=0)
    coords -= centre
    scale = np.abs(coords).max()
    coords = coords / scale if scale else coords
    return separate(coords)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build embeddings and layout")
    parser.add_argument("--recipes", type=Path, default=Path("data/recipes.json"))
    parser.add_argument("--out", type=Path, default=Path("docs/data/graph.json"))
    parser.add_argument("--dims", type=int, default=PCA_DIMS)
    args = parser.parse_args()

    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    print(f"{len(recipes)} recipes")

    # Real cuisine (geography) and course (dish type) from page categories; the
    # recipesummary "category" field is course, not cuisine -- see fetch_cuisines.py.
    labels_path = Path("data/labels.json")
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    labelled = sum(1 for r in recipes if labels.get(r["title"], {}).get("cuisine"))
    print(f"  cuisine labels available for {labelled} recipes")

    # Diet is derived, not given -- see classify_diet.py for how and its limits.
    diet_path = Path("data/diet.json")
    diets = (
        json.loads(diet_path.read_text(encoding="utf-8"))["recipes"]
        if diet_path.exists()
        else {}
    )

    print("building ingredient space (TF-IDF over entities)...")
    ingredient_full, vocabulary = build_ingredient_space(recipes)
    print(f"  vocabulary: {len(vocabulary)} ingredients")

    print("building technique space (sentence embeddings over procedure)...")
    technique_full = build_technique_space(recipes)

    ingredient = reduce_space(ingredient_full, args.dims)
    technique = reduce_space(technique_full, args.dims)

    blended = normalize(np.hstack([ingredient, technique]))
    print("computing 2D layout...")
    coords = build_layout(blended)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "count": len(recipes),
            "dims": int(ingredient.shape[1]),
            "text_model": TEXT_MODEL,
            "source": "Wikibooks Cookbook (CC BY-SA 4.0)",
        },
        "nodes": [
            {
                "i": index,
                "title": recipe["title"],
                "cuisine": labels.get(recipe["title"], {}).get("cuisine", ""),
                "course": labels.get(recipe["title"], {}).get("course", "")
                or recipe.get("cuisine", ""),
                "diet": diets.get(recipe["title"], {}).get("diet", ""),
                "time": recipe.get("time", ""),
                "difficulty": recipe.get("difficulty", ""),
                "ingredients": recipe["ingredients"],
                "steps": len(recipe["steps"]),
                "x": round(float(coords[index][0]), 4),
                "y": round(float(coords[index][1]), 4),
            }
            for index, recipe in enumerate(recipes)
        ],
        # Rounded to 3dp: keeps the payload small, and cosine similarity is not
        # meaningfully affected at this precision.
        "ingredient_vecs": [[round(float(v), 3) for v in row] for row in ingredient],
        "technique_vecs": [[round(float(v), 3) for v in row] for row in technique],
    }
    args.out.write_text(json.dumps(payload), encoding="utf-8")
    size_mb = args.out.stat().st_size / 1e6
    print(f"\nWrote {args.out} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
