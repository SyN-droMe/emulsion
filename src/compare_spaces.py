"""Compare embedding strategies on route quality, over many random pairs.

The single Pad Thai to Cookies example is an anecdote. This runs the same comparison
across hundreds of random recipe pairs so the claim "ingredient routing produces
ingredient-smooth paths" rests on a distribution rather than one lucky route.

Reported per strategy:
  reachable   fraction of pairs connected at k=12 (a sparser graph fragments)
  steps       median path length
  smoothness  mean ingredient Jaccard between consecutive steps, averaged over routes
  worst gap   mean of each route's single worst consecutive-step overlap, which is
              what actually makes a path feel jarring: one bad jump ruins it even
              when the average looks fine
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np

from src.route import jaccard, knn_graph, load, shortest_path

STRATEGIES = {
    "ingredients only": 1.0,
    "70/30 ingredients": 0.7,
    "balanced": 0.5,
    "30/70 ingredients": 0.3,
    "technique only": 0.0,
}


def blended_similarity(graph: dict, alpha: float) -> np.ndarray:
    ingredient = np.array(graph["ingredient_vecs"], dtype=np.float32)
    technique = np.array(graph["technique_vecs"], dtype=np.float32)
    return alpha * (ingredient @ ingredient.T) + (1 - alpha) * (technique @ technique.T)


def evaluate(graph: dict, sim: np.ndarray, pairs: list[tuple[int, int]], k: int) -> dict:
    adjacency = knn_graph(sim.copy(), k=k)
    sets = [set(n["ingredients"]) for n in graph["nodes"]]

    reachable = 0
    lengths: list[int] = []
    means: list[float] = []
    worsts: list[float] = []

    for src, dst in pairs:
        path = shortest_path(adjacency, src, dst)
        if not path or len(path) < 2:
            continue
        reachable += 1
        lengths.append(len(path))
        overlaps = [jaccard(sets[path[i]], sets[path[i + 1]]) for i in range(len(path) - 1)]
        means.append(float(np.mean(overlaps)))
        worsts.append(float(np.min(overlaps)))

    if not reachable:
        return {"reachable": 0.0, "steps": 0, "smoothness": 0.0, "worst": 0.0}
    return {
        "reachable": reachable / len(pairs),
        "steps": int(np.median(lengths)),
        "smoothness": float(np.mean(means)),
        "worst": float(np.mean(worsts)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare embedding strategies")
    parser.add_argument("--graph", type=Path, default=Path("docs/data/graph.json"))
    parser.add_argument("--pairs", type=int, default=300)
    parser.add_argument("--k", type=int, default=12)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    graph = load(args.graph)
    count = len(graph["nodes"])
    rng = random.Random(args.seed)
    pairs = [
        (rng.randrange(count), rng.randrange(count)) for _ in range(args.pairs)
    ]
    pairs = [(a, b) for a, b in pairs if a != b]
    print(f"{count} recipes, {len(pairs)} random pairs, k={args.k}\n")

    header = f"{'strategy':20s} {'reachable':>10s} {'steps':>7s} {'smoothness':>12s} {'worst gap':>11s}"
    print(header)
    print("-" * len(header))
    for label, alpha in STRATEGIES.items():
        result = evaluate(graph, blended_similarity(graph, alpha), pairs, args.k)
        print(
            f"{label:20s} {result['reachable']:9.0%} {result['steps']:7d} "
            f"{result['smoothness']:12.3f} {result['worst']:11.3f}"
        )


if __name__ == "__main__":
    main()
