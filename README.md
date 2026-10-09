# Emulsion

An interactive map of 3,222 recipes. Route between any two dishes either by what they
are **made of** or by how they are **made**, and watch the path change character as you
move between the two.

An emulsion is two things that don't naturally mix, held together anyway. That is more
or less what the slider does here. Ingredient similarity and technique similarity often
disagree completely about which dishes are related.

**[Live demo](https://syn-drome.github.io/emulsion/)** (no backend, it all runs in the page)

## What you can do

* **Search** by dish name or by ingredient. Five recipes use paneer; only two say so in
  the title.
* **Click two dishes** to route between them. You get the path plus the ingredients each
  step shares with the one before it.
* **Drag the slider** between pure technique and pure ingredients. With a route active it
  re-routes live, and watching the same A to B path change is the thing worth playing
  with.
* **Filter** by diet or by any of 71 cuisines. Filters constrain what the router can walk
  through, so "veg only" gives a genuinely vegetarian path rather than one that just
  looks filtered.
* **Type what is in your kitchen** into the pantry box. You get every dish that uses it,
  ranked by how much of it you already have, makeable ones first. Salt, water, pepper,
  oil and sugar are assumed.
* **Open the real recipe** from any search result, pantry match or route step. The map
  only encodes how dishes relate; the instructions live on Wikibooks.

## The two spaces

The point is that the two modalities are different *in kind*, not two text embeddings of
the same thing.

**Ingredients** use TF-IDF over canonical ingredient entities. Wikibooks links
ingredients to their own pages, so `[[Cookbook:Fish Sauce|fish sauce]]` gives a clean
entity rather than me parsing "2 tablespoons fish sauce" out of prose. TF-IDF rather
than raw overlap because salt and flour turn up in a quarter of all recipes, and
weighting by rarity is what makes "both use tamarind" count for more than "both use
salt".

**Technique** uses sentence embeddings (all-MiniLM-L6-v2) over the procedure text. Fuzzy
and semantic on purpose, so "braise" lands near "simmer gently" with no shared words.

Both reduce to 64 dimensions with PCA and ship as JSON. The browser recomputes cosine
similarity locally on every slider move, which is why there is no server.

## Why routes look the way they do

Positions come from UMAP on the blended space, computed once offline and frozen. The
slider changes **which edges exist**, never where dishes sit. I tried it the other way
first; recomputing the layout mid-drag makes every dot jump and destroys any sense of a
stable place you can learn.

The cost is that edges sometimes connect dots that look far apart, and routes zig-zag
instead of tracing a tidy line. The layout is a lossy squash of 128 dimensions into 2
while the routing still uses all 128, so two dishes can be genuine nearest neighbours
and still land in opposite corners.

## What I found

Smoothness here means the average ingredient overlap between consecutive steps of a
route. Across 200 random pairs:

| Strategy | Reachable | Median steps | Smoothness | Worst gap |
|---|---|---|---|---|
| ingredients only | 98% | 7 | 0.358 | 0.211 |
| 70/30 ingredients | 98% | 6 | 0.351 | 0.210 |
| balanced | 98% | 7 | 0.326 | 0.170 |
| 30/70 ingredients | 98% | 7 | 0.284 | 0.127 |
| technique only | 98% | 7 | 0.203 | 0.058 |

Smoothness and worst gap both fall monotonically toward technique, which is what the
design predicts. "Worst gap" is each route's single worst step, averaged, and it matters
more than the mean: one jarring jump ruins a path even when the average looks fine.
Technique-only sits at 0.058, so nearly every technique route contains a step with no
ingredient continuity at all.

Worth saying that a single route does not show this. Pad Thai to Chocolate Chip Cookies
comes out at 0.39 on ingredients and 0.40 on balanced, the wrong way round. Only
technique separates clearly, at 0.12, via Lo Mein, Tuna Casserole and Bread Pudding. On
one pair the gap between neighbouring slider positions is inside the noise, which is why
the table above exists.

**Position encodes real structure.** Comparing each dish's eight spatial neighbours
against chance:

| Label | Neighbours sharing it | Chance | Lift |
|---|---|---|---|
| cuisine | 19.0% | 4.5% | 4.2x |
| course | 28.7% | 5.4% | 5.3x |
| diet | 65.6% | 41.8% | 1.6x |

**Cuisines cluster in proportion to how distinctive their pantry is**, which is my
favourite thing to fall out of this. Indian is the tightest cluster of any real size at
47% off 116 recipes, Nigerian 42%, Italian 41%. At the other end American scores **3%**
and English 11%, completely scattered, because a dish filed under either is built from
the same butter, flour, sugar and onion as everything else in a Western-leaning corpus.
Nothing in ingredient or technique space marks a dish as American. An explainable
failure convinces me more than everything clustering neatly.

## Limitations

* **Diet labels are derived, not given.** Wikibooks has no diet field, so I classify
  ingredients and infer upward. Their categories handle the obvious cases but miss the
  ones that matter: `Fish Sauce` is filed only under "Condiments", `Gelatin` under
  "Thickeners". I keep an explicit list of those traps, but it is still a heuristic. If
  you have an actual dietary restriction, read the ingredient list, not my label.
* **"Veg" means the Indian convention**: egg is non-veg, dairy is not. `has_egg` and
  `has_dairy` stay as separate fields rather than getting baked into one opaque label.
* **Cuisine coverage is partial.** 964 of 3,222 recipes have a cuisine label, since that
  comes from page categories and not every page is categorised. A cuisine filter is
  therefore a filter on labelled dishes, not on the corpus.
* **UMAP distances are only locally meaningful.** You cannot read "Indian is twice as
  far from Italian as from Thai" off this map. Cluster sizes and the gaps between
  distant clusters do not mean anything either.
* **The pantry bands are absolute**, so a pantry of N items can only reach dishes with
  N+2 ingredients or fewer. One ingredient can never make anything "within two", which
  is why the panel lists everything that uses it instead.
* **The corpus has near-duplicates.** Guacamole I, II and III sit at 0.99 similarity, as
  do Chocolate Chip Cookies I through IV. I push coincident points apart just enough to
  hover them separately, since they really are different recipes.
* **Single-use ingredients are dropped** from the TF-IDF vocabulary (`min_df=2`), taking
  it from 1,052 names to 646. An ingredient used once can only make a cluster of one.

## Data

3,222 usable recipes out of 3,796 Wikibooks Cookbook pages, via the MediaWiki API.

Getting the data clean took more work than the modelling, and it is where every real bug
in this project has been. The pattern was always the same: nothing threw an error, the
map just quietly meant less than it claimed. A bullet-list parser was dropping a quarter
of the corpus because those recipes use wikitables. "Chopping" was the fourth most
common ingredient, since ingredient lines link to technique pages exactly like they link
to ingredients. `Eggplant` was classified as an egg product because the egg test was a
prefix check. And a rebuild once wrote an alias map with 5 entries instead of 388, which
undid every synonym merge and left one dish listing both Clarified Butter and Ghee.

The tests in `tests/test_pipeline.py` are one per bug that actually shipped, so they are
regression tests rather than decoration.

## Licensing

Code is MIT. The recipe data is CC BY-SA 4.0, inherited from Wikibooks, so derivatives
of the data have to stay CC BY-SA and credit Wikibooks. See [LICENSE](LICENSE) and
[LICENSE-DATA](LICENSE-DATA).

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -r requirements.txt

python src/crawl.py                 # fetch recipe pages (about 80 API requests)
python src/fetch_blocklist.py       # units and techniques to exclude
python src/parse.py                 # wikitext to structured recipes
python src/resolve_aliases.py       # merge ingredient synonyms
python src/parse.py                 # re-parse with the merges applied
python src/fetch_cuisines.py        # cuisine and course from page categories
python src/classify_diet.py         # derive veg / vegan / non-veg
python src/embed.py                 # embeddings, layout, shipped artifact

python -m http.server 8000 -d docs  # then open localhost:8000
```

Route from the command line, which is how I validated the idea before building any
interface:

```bash
python src/route.py "Pad Thai" "Chocolate Chip Cookies I"
python -m src.compare_spaces --pairs 200
python tests/test_pipeline.py
```

## Files

```
src/crawl.py            MediaWiki API crawl, batched 50 pages per request
src/parse.py            wikitext parsing, ingredient entity extraction
src/fetch_blocklist.py  pulls Wikibooks unit and technique categories
src/resolve_aliases.py  redirect resolution: synonym merging, technique filtering
src/fetch_cuisines.py   cuisine and course labels from page categories
src/classify_diet.py    ingredient classification and recipe diet labels
src/embed.py            both embedding spaces, UMAP layout, point separation
src/route.py            reference routing and the smoothness metric
src/compare_spaces.py   strategy comparison over many random routes
src/find_synonyms.py    looks for one food under two page names, for manual review
tests/test_pipeline.py  regression tests, one per bug that actually happened
docs/                   the site itself
```
