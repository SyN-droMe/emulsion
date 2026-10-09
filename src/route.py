"""k-NN graph construction and routing, plus the path-quality metric.

This is the reference implementation. The frontend reimplements the same logic in JS so
routing stays client-side (no backend), and this module is what validates that the idea
actually works before any of it gets ported.

`path_smoothness` is the honest check on the headline feature: a route that technically
connects two recipes but jumps between unrelated dishes at every step is a bad route,
and "it returned a path" is not evidence of quality. Measuring mean ingredient overlap
between consecutive steps turns the claim into a number -- and lets the ingredient and
technique spaces be compared on it rather than argued about.
"""

from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path

import numpy as np

K_NEIGHBOURS = 12


def load(path: Path = Path("docs/data/graph.json")) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def similarity(graph: dict, alpha: float) -> np.ndarray:
    """alpha = 1.0 is pure ingredients, 0.0 is pure technique."""
    ingredient = np.array(graph["ingredient_vecs"], dtype=np.float32)
    technique = np.array(graph["technique_vecs"], dtype=np.float32)
    return alpha * (ingredient @ ingredient.T) + (1 - alpha) * (technique @ technique.T)


def knn_graph(sim: np.ndarray, k: int = K_NEIGHBOURS) -> list[list[tuple[int, float]]]:
    """Adjacency with edge cost = 1 - similarity, so Dijkstra prefers similar hops."""
    np.fill_diagonal(sim, -np.inf)
    neighbours = np.argpartition(-sim, k, axis=1)[:, :k]
    adjacency: list[list[tuple[int, float]]] = []
    for node, row in enumerate(neighbours):
        adjacency.append([(int(j), float(max(1.0 - sim[node, j], 1e-4))) for j in row])
    return adjacency


def shortest_path(adjacency: list[list[tuple[int, float]]], src: int, dst: int) -> list[int]:
    distances = {src: 0.0}
    previous: dict[int, int] = {}
    queue = [(0.0, src)]
    visited: set[int] = set()

    while queue:
        cost, node = heapq.heappop(queue)
        if node in visited:
            continue
        visited.add(node)
        if node == dst:
            break
        for neighbour, weight in adjacency[node]:
            new_cost = cost + weight
            if new_cost < distances.get(neighbour, float("inf")):
                distances[neighbour] = new_cost
                previous[neighbour] = node
                heapq.heappush(queue, (new_cost, neighbour))

    if dst not in previous and dst != src:
        return []
    path = [dst]
    while path[-1] != src:
        path.append(previous[path[-1]])
    return path[::-1]


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def path_smoothness(graph: dict, path: list[int]) -> float:
    """Mean ingredient Jaccard between consecutive steps. Higher = less jarring."""
    if len(path) < 2:
        return 0.0
    sets = [set(graph["nodes"][i]["ingredients"]) for i in path]
    return float(np.mean([jaccard(sets[i], sets[i + 1]) for i in range(len(sets) - 1)]))


def find(graph: dict, query: str) -> int:
    titles = [n["title"] for n in graph["nodes"]]
    for index, title in enumerate(titles):
        if title.lower() == query.lower():
            return index
    for index, title in enumerate(titles):
        if query.lower() in title.lower():
            return index
    raise SystemExit(f"no recipe matching {query!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Route between two recipes")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--graph", type=Path, default=Path("docs/data/graph.json"))
    args = parser.parse_args()

    graph = load(args.graph)
    src, dst = find(graph, args.start), find(graph, args.end)
    titles = [n["title"] for n in graph["nodes"]]
    print(f"{titles[src]}  ->  {titles[dst]}\n")

    for alpha, label in ((1.0, "ingredients only"), (0.5, "balanced"), (0.0, "technique only")):
        sim = similarity(graph, alpha)
        path = shortest_path(knn_graph(sim), src, dst)
        if not path:
            print(f"{label:18s} no path (graph disconnected at k={K_NEIGHBOURS})")
            continue
        smooth = path_smoothness(graph, path)
        print(f"{label:18s} {len(path)} steps, ingredient smoothness {smooth:.2f}")
        for step, node in enumerate(path):
            print(f"    {step}. {titles[node]}")
        print()


if __name__ == "__main__":
    main()
