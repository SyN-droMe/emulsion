# Emulsion

An interactive map of 3,226 recipes where you can route between any two dishes, either
by what they are **made of** or by how they are **made**.

An emulsion is two things that do not naturally mix held together anyway, which is
roughly what the slider in this thing does: it blends ingredient similarity with
technique similarity, and those two measures often disagree completely about which
dishes are related.

**[Live demo](https://syn-drome.github.io/emulsion/)** (no backend, loads straight from
the page)

## What you can do with it

* **Hover** a dish to see its ingredients and light up its twelve nearest neighbours at
  the current slider setting.
* **Click two dishes** to route between them. You get the full path, plus which
  ingredients each step shares with the one before it.
* **Drag the slider** between pure technique and pure ingredients. With a route active
  it re-routes live, and watching the same A to B path change character is the thing
  worth playing with.
* **Filter** by diet (veg, vegan, non-veg) or by any of 71 cuisines. Filters constrain
  what the router can walk through, so "veg only" gives you an actually vegetarian path
  rather than a path that just looks filtered.
* **Type what is in your kitchen** into the pantry box. Dishes you can make right now
  go green, dishes within two ingredients go amber, everything else fades out. Salt,
  water, pepper, oil and sugar are assumed, because the median recipe has eight
  ingredients and three of them are usually staples, which makes a literal reading of a
  pantry list useless.

## The two similarity spaces

The interesting part is that the two modalities are different *in kind*, not just two
text embeddings of the same thing:

**Ingredients** use TF-IDF over canonical ingredient entities. Wikibooks links
ingredients to their own pages, so `[[Cookbook:Fish Sauce|fish sauce]]` gives me a clean
entity instead of me having to parse "2 tablespoons fish sauce" out of free text. TF-IDF
rather than raw overlap because salt, sugar and flour turn up in a quarter of all
recipes, and weighting by rarity is what makes "both of these use tamarind" count for
more than "both of these use salt".

**Technique** uses sentence embeddings (all-MiniLM-L6-v2) over the procedure text. This
one is fuzzy and semantic on purpose, so "braise" and "simmer gently" land near each
other even with no shared words.

Both get reduced to 64 dimensions with PCA and shipped as JSON. The browser recomputes
cosine similarity locally every time you move the slider, which is why there is no
server involved.

## Why routes look the way they do

Positions come from UMAP on the blended space, computed once offline and then frozen.
The slider changes **which edges exist**, never where dishes sit. I tried it the other
way first and recomputing the layout mid-drag makes every dot jump around, which
destroys any sense of a stable place you can learn.

The side effect is that edges sometimes connect dots that look far apart, and routes
sometimes zig-zag across the screen instead of tracing a tidy line. That is not a bug.
The layout is a lossy squash of 128 dimensions into 2, while the routing still uses all
128. Two dishes can be genuine nearest neighbours in the full space and land in
different corners of the projection.

## What I found

**The slider does what it claims.** Routing Pad Thai to Chocolate Chip Cookies produces
three different paths, and the ingredient-overlap smoothness of each path orders exactly
the way the design predicts:

| Slider | Path | Smoothness |
|---|---|---|
| ingredients | via Thai Stir-Fried Pumpkin, Filipino Spring Rolls, Albanian Meat with Walnuts, Holland Tea Cakes | 0.30 |
| balanced | via Thai Shrimp, Shrimp Curry, Sindhi Chickpea Confection, Pralin, Marly Sponge | 0.26 |
| technique | via Lo Mein, Tuna Casserole, Bulgarian Casserole, Bread Pudding, Very Simple Cookies | 0.11 |

The ingredient route bridges savoury to sweet through nuts. The technique route goes
through casseroles and baked dishes, connecting things that share a *method* and almost
no ingredients. Smoothness measures ingredient continuity, so ingredient-weighted
routing scoring highest on it is the expected result rather than a lucky one.

One route is an anecdote though, so `src/compare_spaces.py` runs the same comparison
over 200 random recipe pairs:

| Strategy | Reachable | Median steps | Smoothness | Worst gap |
|---|---|---|---|---|
| ingredients only | 96% | 7 | 0.327 | 0.178 |
| 70/30 ingredients | 98% | 7 | 0.304 | 0.166 |
| balanced | 100% | 7 | 0.285 | 0.145 |
| 30/70 ingredients | 99% | 6 | 0.246 | 0.102 |
| technique only | 100% | 7 | 0.167 | 0.036 |

Smoothness falls monotonically as the slider moves toward technique, so the single route
above was not a fluke. "Worst gap" is the average of each route's single worst
consecutive-step overlap, which is what actually makes a path feel jarring: one bad jump
ruins a route even when its average looks fine. Technique-only routing sits at 0.036
there, meaning nearly every technique route contains a step with essentially no
ingredient continuity at all.

The thing I did not expect is the **reachability tradeoff**. Ingredient-only routing has
the *worst* connectivity at 96%, while technique-only and balanced both reach 100%.
Ingredient space fragments, because a recipe with an unusual ingredient set has no close
neighbours and strands itself, whereas every dish shares cooking methods with something.
So balanced is the best default on evidence rather than on taste, which is why the
slider starts there.

**Position encodes real structure.** Comparing each dish's eight spatial neighbours
against chance:

| Label | Neighbours sharing it | Chance | Lift |
|---|---|---|---|
| cuisine | 23.9% | 4.5% | 5.3x |
| course | 27.1% | 5.4% | 5.0x |
| diet | 64.3% | 41.6% | 1.5x |

**Cuisines cluster in proportion to how distinctive their pantry is**, which I think is
the most interesting thing to fall out of this. Ethiopian is the tightest cluster in the
whole map at 69% (teff, berbere, injera: nothing else in the corpus uses them). Indian
is at 51% off 116 recipes. But English recipes score **0%**, completely scattered,
because puddings and pies and roasts are built from the same butter, flour, sugar and
onion as everything else in a Western-leaning corpus. There is nothing in ingredient or
technique space that marks a dish as English.

So when the map fails to cluster something, the failure is explainable, which I find
more convincing than if everything had clustered neatly.

## Limitations

I would rather state these than have someone find them.

* **Diet labels are derived, not given.** Wikibooks has no diet field, so I classify
  ingredients and infer from there. Wikibooks' own ingredient categories handle obvious
  cases (`Chicken` is in Poultry, `Paneer` is in Cheeses) but miss the ones that
  matter: `Fish Sauce` and `Worcestershire Sauce` are filed only as "Condiments", and
  `Gelatin` as "Thickeners and stabilizers". Nothing in their categories reveals those
  are animal derived, so I keep an explicit list of those traps. It is still a
  heuristic. If you have an actual dietary restriction, read the ingredient list in the
  tooltip rather than trusting my label.
* **"Veg" here means the Indian convention**: egg counts as non-veg, dairy does not.
  Western vegetarian usually includes egg, so `has_egg` and `has_dairy` are kept as
  separate fields in the data rather than baked into one opaque label.
* **Cuisine coverage is partial.** 964 of 3,226 recipes have a cuisine label, because
  that comes from page categories and not every page is categorised. Unlabelled dishes
  show grey and are excluded when you filter by cuisine.
* **UMAP distances are only locally meaningful.** You cannot read "Indian is twice as
  far from Italian as from Thai" off this map. Cluster sizes and the gaps between
  distant clusters do not mean anything either. Local neighbourhoods are the only thing
  the layout promises to get right.
* **The corpus has near-duplicates.** Guacamole I, II and III sit at 0.99 ingredient
  similarity, as do Chocolate Chip Cookies I through IV. They form tight knots. I push
  coincident points apart just enough to hover them individually rather than merging
  them, since they really are separate recipes.
* **Ingredients that appear only once are dropped** from the TF-IDF vocabulary
  (`min_df=2`), taking it from 1,758 distinct ingredients down to 972. A single-use
  ingredient can only create a cluster of one.

## Data

Recipes come from the [Wikibooks Cookbook](https://en.wikibooks.org/wiki/Cookbook:Recipes),
3,226 usable recipes out of 3,796 pages, fetched through the MediaWiki API.

Getting clean data took more work than the modelling did. Four things were silently
wrong before I checked the output properly:

1. A bullet-list parser was dropping about a quarter of all recipes, because recipes
   like Afghan Bread and Afang Soup put their ingredients in a wikitable instead.
2. "Chopping" was the fourth most common "ingredient" in the corpus. Ingredient lines
   link to technique pages exactly like they link to ingredients.
3. `Cookbook:Mince` and `Cookbook:Dice` are redirects to `Cookbook:Knife Skills`, so
   they are techniques wearing ingredient-shaped names. Resolving redirects catches
   these, and the same pass merges 373 synonyms like "All-purpose flour" into "Wheat
   Flour".
4. MediaWiki treats `Feta_Cheese` and `Feta Cheese` as the same page, but I was counting
   them as two ingredients, which fragmented the vocabulary and weakened every
   similarity score with no visible symptom.

## Licensing

Code is MIT. The recipe data is CC BY-SA 4.0, inherited from Wikibooks, which means
derivatives of the data have to stay CC BY-SA and credit Wikibooks. See
[LICENSE](LICENSE) and [LICENSE-DATA](LICENSE-DATA).

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -r requirements.txt

python src/crawl.py                 # fetch recipe pages (about 80 API requests)
python src/fetch_blocklist.py       # units and techniques to exclude
python src/parse.py                 # wikitext to structured recipes
python src/resolve_aliases.py       # merge ingredient synonyms
python src/fetch_cuisines.py        # cuisine and course from page categories
python src/classify_diet.py         # derive veg / vegan / non-veg
python src/embed.py                 # embeddings, layout, and the shipped artifact

python -m http.server 8000 -d docs  # then open localhost:8000
```

Route between two dishes from the command line, which is how I validated the idea before
building any of the interface:

```bash
python src/route.py "Pad Thai" "Chocolate Chip Cookies I"
```

Tests cover the parser and the diet classifier. Every case in there is a bug that
actually shipped at some point, so they are regression tests rather than decoration:

```bash
python tests/test_pipeline.py
python -m src.compare_spaces --pairs 200
```

## Files

```
src/crawl.py            MediaWiki API crawl, batched 50 pages per request
src/parse.py            wikitext parsing, ingredient entity extraction
src/fetch_blocklist.py  pulls Wikibooks' unit and technique categories
src/resolve_aliases.py  redirect resolution: synonym merging and technique filtering
src/fetch_cuisines.py   cuisine and course labels from page categories
src/classify_diet.py    ingredient classification and recipe diet labels
src/embed.py            both embedding spaces, UMAP layout, point separation
src/route.py            reference routing and the path smoothness metric
src/compare_spaces.py   embedding strategy comparison over many random routes
tests/test_pipeline.py  regression tests, one per bug that actually happened
docs/                   the site itself, served by GitHub Pages
```
