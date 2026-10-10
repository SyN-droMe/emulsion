# Emulsion

An interactive map of 3,222 recipes. Route between any two dishes either by what they
are **made of** or by how they are **made**, and watch the path change character as you
move between the two.

An emulsion is two things that don't naturally mix, held together anyway. That is more
or less what the slider does here. Ingredient similarity and technique similarity often
disagree completely about which dishes are related.

**[Live demo](https://emulsion.ayushkuriakose.workers.dev/)** (no backend, it all runs in the page)

![A route across the map from Pad Thai to Chocolate Chip Cookies I, passing through a
Zambian vegetable stir-fry, carrot dishes and a Sachertorte](media/preview.png)

*Pad Thai to Chocolate Chip Cookies I. The panel shows what each step shares with the
one before it, which is how you can tell the route is following something real rather
than wandering.*

## What you can do

* **Pick two dishes, A and B**, either by searching or by clicking dots on the map. The
  panel shows which one is picked and which it wants next, and the route appears once
  both are set, with the ingredients each step shares with the one before it.
* **Search** by dish name or by ingredient. Searching finds dishes that merely use an
  ingredient, not only ones with it in the title.
* **Drag the slider** between pure technique and pure ingredients. With a route active it
  re-routes live, and watching the same A to B path change is something you can play around with.
* **Filter** by diet or cuisine. The filter also limits what a route may pass through, so
  a veg route is actually veg.
* **Type what is in your kitchen** into the pantry box. You get every dish that uses it,
  ranked by how much of it you already have, makeable ones first. Salt, water, pepper,
  oil and sugar are assumed.
* **Open the real recipe** from either picked dish, any pantry match or any route step.
  The map only encodes how dishes relate; the instructions live on Wikibooks.

## Two ways to measure "similar"

Every dish gets measured against every other dish twice, and the two measures are
genuinely different kinds of thing rather than two versions of the same one. The slider
decides how much of each to use.

**By ingredients.** Each recipe becomes a list of what goes in it. Wikibooks links every
ingredient to its own page, so `[[Cookbook:Fish Sauce|fish sauce]]` arrives as a clean
name and I never have to pull "2 tablespoons fish sauce" apart myself.

Shared ingredients are not worth the same, though. Salt and flour appear in a quarter of
all recipes, so two dishes both using salt tells you nothing, while two dishes both using
tamarind tells you a lot. So rarer ingredients count for more. That weighting is TF-IDF.

**By technique.** The instructions go through a sentence model (all-MiniLM-L6-v2) that
turns text into numbers based on meaning rather than exact words. That way "braise" ends
up close to "simmer gently" even though they share no words at all.

Both measures start out much wider than 64 numbers, so each gets squeezed down to 64
with PCA, which keeps the variation that separates dishes and discards the rest. That is
what makes the whole thing small enough to ship inside the page. Moving the slider just reweights them and recompares, which is
why nothing talks to a server.

## How the map is laid out

The 64 numbers per dish are what the routing uses, but you cannot draw 64 dimensions.
UMAP is the step that turns them into an x and y for the screen: it looks for a 2D
arrangement where dishes that sat near each other in the full data still sit near each
other on screen.

That is a compression, and it has to throw something away. What UMAP preserves is local
neighbourhoods, so the dishes immediately around any dot really are its closest
relatives. What it does not preserve is the big picture. Distances between far-apart
clusters, and the sizes of the clusters themselves, are side effects of the layout rather
than facts about food.

The layout is computed once, offline, and then frozen. The slider changes **which dishes
connect to which**, never where any dish sits. I tried it the other way first, and
recomputing the layout mid-drag makes every dot jump around, which destroys any sense of
a stable place you can learn.

The visible cost is that routes sometimes zig-zag across the screen instead of tracing a
tidy line. Routing uses all 64 numbers per space while the picture only has two, so two
dishes can be genuine nearest neighbours and still get drawn in opposite corners.

## What I found

**Smoothness** is how much consecutive dishes in a route share ingredients, from 0 to 1.
A smooth route changes one thing at a time; a rough one jumps. Averaged over 200 random
pairs of dishes:

| Strategy | Reachable | Median steps | Smoothness | Worst gap |
|---|---|---|---|---|
| ingredients only | 98% | 7 | 0.358 | 0.211 |
| 70/30 ingredients | 98% | 6 | 0.351 | 0.210 |
| balanced | 98% | 7 | 0.326 | 0.170 |
| 30/70 ingredients | 98% | 7 | 0.284 | 0.127 |
| technique only | 98% | 7 | 0.203 | 0.058 |

Smoothness drops steadily as the slider moves from ingredients to technique, which is
exactly what the design is supposed to do. Weight ingredients and you get paths that
change ingredients gradually. Weight technique and you get paths through dishes that are
cooked the same way but made of completely different things.

**Worst gap** is the single most abrupt step in a route, averaged across routes. It
matters more than the average, because one jarring jump ruins a path even when the rest
of it is fine. Technique-only sits at 0.058, which means almost every technique route
contains one step where the two dishes share essentially no ingredients.

These are averages over 200 pairs rather than one example on purpose. On any single pair
of dishes, neighbouring slider positions land close enough together that the ordering can
come out backwards by chance.

**Position encodes real structure.** For each dish I checked how often its eight nearest
dots on screen share its label, against how often that would happen by chance:

| Label | Neighbours sharing it | Chance | Lift |
|---|---|---|---|
| cuisine | 19.0% | 4.5% | 4.2x |
| course | 28.7% | 5.4% | 5.3x |
| diet | 65.6% | 41.8% | 1.6x |

**Cuisines cluster in proportion to how distinctive their pantry is**, which is my
favourite thing to fall out of this. Indian is the tightest cluster of any real size at
47% off 116 recipes, Nigerian 42%, Italian 41%. At the other end American scores **3%**
and English 11%, completely scattered, because a dish filed under either is built from
the same butter, flour, sugar and onion as most other dishes in a Western-leaning set
of recipes.
Nothing in the ingredients or the method marks a dish as American. An explainable failure
convinces me more than everything clustering neatly.

## Limitations

* **Diet labels are derived, not given.** Wikibooks has no diet field, so I classify
  ingredients and work upward. Their categories handle the obvious cases but miss the
  ones that matter: `Fish Sauce` is filed only under "Condiments", `Gelatin` under
  "Thickeners". I keep an explicit list of those traps, but it is still a guess. If you
  have an actual dietary restriction, read the ingredient list, not my label.
* **"Veg" means the Indian convention**: egg is non-veg, dairy is not. `has_egg` and
  `has_dairy` stay as separate fields rather than getting baked into one opaque label.
* **Cuisine coverage is partial.** Only 964 of 3,222 recipes have a cuisine label, since
  that comes from page categories and not every page is categorised. Filtering by cuisine
  therefore filters labelled dishes, not the whole map.
* **You cannot read distances off the map.** Local neighbourhoods are meaningful; the
  gaps between distant clusters and the sizes of clusters are not.
* **The pantry bands are absolute**, so a pantry of N items can only reach dishes with
  N+2 ingredients or fewer. One ingredient can never make anything "within two", which is
  why the panel lists everything that uses it instead.
* **The data has near-duplicates.** Guacamole I, II and III are 99% identical, as are
  Chocolate Chip Cookies I through IV. I nudge overlapping dots apart just enough to hover
  them separately, since they really are different recipes.
* **Ingredients used in only one recipe are ignored.** An ingredient that appears once
  cannot tell you two dishes are alike, so it is dropped before any similarity is
  computed. That takes 1,052 ingredient names down to 646.

## Data

3,222 usable recipes out of 3,796 Wikibooks Cookbook pages, pulled through the MediaWiki
API.

Cleaning that data took longer than the modelling and is where every real bug in this
project has been. None of them threw an error. The map just quietly meant less than it
claimed, which is the part worth knowing: a parser that only read bullet lists was
silently dropping a quarter of all recipes because those use tables instead, and
"Chopping" was the fourth most common ingredient in the whole set, because ingredient
lines link to technique pages exactly like they link to ingredients.

The tests in `tests/test_pipeline.py` are one per bug that actually shipped, so they are
regression tests rather than decoration.

## Licensing

Code is MIT. The recipe data is CC BY-SA 4.0, inherited from Wikibooks, so derivatives of
the data have to stay CC BY-SA and credit Wikibooks. See [LICENSE](LICENSE) and
[LICENSE-DATA](LICENSE-DATA).

## Rebuilding it yourself

The live demo needs none of this. These steps are for cloning the repo and rebuilding the
data from scratch, or changing how similarity works and seeing what moves.

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
python src/embed.py                 # both measures, layout, shipped data file

python -m http.server 8000 -d docs  # then open localhost:8000
```

Routing also runs from the command line, which is how I checked the idea worked before
building any interface:

```bash
python src/route.py "Pad Thai" "Chocolate Chip Cookies I"
python -m src.compare_spaces --pairs 200
python tests/test_pipeline.py     # data pipeline
node tests/test_ui.js             # the page, driven the way a person drives it
```

## Files

```
src/crawl.py            MediaWiki API crawl, batched 50 pages per request
src/parse.py            wikitext parsing, ingredient extraction
src/fetch_blocklist.py  pulls Wikibooks unit and technique categories
src/resolve_aliases.py  redirect resolution: synonym merging, technique filtering
src/fetch_cuisines.py   cuisine and course labels from page categories
src/classify_diet.py    ingredient classification and recipe diet labels
src/embed.py            both similarity measures, UMAP layout, point separation
src/route.py            reference routing and the smoothness metric
src/compare_spaces.py   strategy comparison over many random routes
src/find_synonyms.py    looks for one food under two page names, for manual review
tests/test_pipeline.py  data regression tests, one per bug that actually happened
tests/test_ui.js        interaction tests, same idea, run against docs/app.js
docs/                   the site itself
```
