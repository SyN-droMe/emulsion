/* Emulsion -- client-side recipe map.
 *
 * Everything runs in the browser: no backend, no API calls. Two 64-dim embedding
 * matrices ship as JSON and similarity is recomputed locally whenever the slider moves.
 *
 * Two decisions worth knowing about:
 *
 * 1. NEIGHBOURS ARE COMPUTED LAZILY, NOT PRECOMPUTED. A full 3226x3226 similarity
 *    matrix would be ~10M dot products on every slider change -- seconds of jank, and
 *    40MB+ if precomputed per slider position and shipped. Instead each node's
 *    neighbours are computed on demand (3226 x 64 x 2 multiply-adds, sub-millisecond)
 *    and memoised until alpha changes. Dijkstra only ever touches the nodes it visits.
 *
 * 2. NODE POSITIONS ARE FIXED. The layout comes from a blended space computed offline
 *    and never moves. The slider changes which EDGES exist, not where dishes sit --
 *    recomputing the layout live would make everything jump around mid-drag and destroy
 *    any sense of a stable place you can learn.
 */

const K = 12;              // neighbours per node in the routed graph
const state = {
  nodes: [], n: 0, dims: 0,
  ing: null, tec: null,    // flat Float32Arrays, row-major
  alpha: 0.5,
  neighbourCache: new Map(),
  hover: -1, start: -1, end: -1, path: [],
  filter: { diet: 'all', cuisine: 'all' },
  pantry: null,        // Set of lowercased ingredient names, or null when unused
  missing: null,       // per-node count of ingredients the pantry lacks
  view: { x: 0, y: 0, scale: 1 },
};

const canvas = document.getElementById('map');
// Palette lives in style.css; the canvas reads it rather than repeating hex codes.
let ACCENT = '#5bb8d4', BG = '#0f1317';
try {
  const css = getComputedStyle(document.documentElement);
  ACCENT = css.getPropertyValue('--accent').trim() || ACCENT;
  BG = css.getPropertyValue('--bg').trim() || BG;
} catch { /* no computed styles available; the literals above are the same values */ }
const ctx = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');

/* ---------- loading ---------- */

async function load() {
  const res = await fetch('data/graph.json');
  const g = await res.json();

  state.nodes = g.nodes;
  state.n = g.nodes.length;
  state.dims = g.meta.dims;

  const flatten = (rows) => {
    const out = new Float32Array(rows.length * state.dims);
    for (let i = 0; i < rows.length; i++) out.set(rows[i], i * state.dims);
    return out;
  };
  state.ing = flatten(g.ingredient_vecs);
  state.tec = flatten(g.technique_vecs);

  /* The headline count comes from the data, not the markup. It was hardcoded as 3,226
   * and drifted to a different number from the footer as the parser improved. */
  document.getElementById('count').textContent = state.n.toLocaleString();
  updateSearchLabel();
  renderPicks();

  const counts = {};
  for (const node of state.nodes) if (node.cuisine) counts[node.cuisine] = (counts[node.cuisine] || 0) + 1;
  const select = document.getElementById('cuisine');
  Object.entries(counts)
    .sort((a, b) => b[1] - a[1])          // most-represented cuisines first
    .forEach(([name, count]) => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = `${name} (${count})`;
      select.appendChild(option);
    });

  fitView();
  updateFilterRead();
  draw();
}

function updateFilterRead() {
  const { diet, cuisine } = state.filter;
  const el = document.getElementById('filterRead');
  if (!el) return;
  el.textContent = (diet === 'all' && cuisine === 'all')
    ? `${state.n} dishes shown`
    : `${allowedCount()} of ${state.n} dishes match`;
}

function applyFilterChange() {
  state.neighbourCache.clear();
  const pantryInput = document.getElementById('pantry');
  if (pantryInput && pantryInput.value.trim()) setPantry(pantryInput.value);   // neighbour sets depend on what is allowed
  if (state.start >= 0 && state.end >= 0) state.path = route(state.start, state.end);
  updateFilterRead();
  renderRoute();
  updateSearchLabel();
  renderPicks();
  draw();
}

document.getElementById('diet').addEventListener('click', (e) => {
  const button = e.target.closest('button[data-diet]');
  if (!button) return;
  state.filter.diet = button.dataset.diet;
  document.querySelectorAll('#diet button').forEach((b) => b.classList.toggle('on', b === button));
  applyFilterChange();
});

document.getElementById('cuisine').addEventListener('change', (e) => {
  state.filter.cuisine = e.target.value;
  applyFilterChange();
});

/* ---------- similarity + graph ---------- */

function similarityTo(i) {
  const { ing, tec, dims, n, alpha } = state;
  const sims = new Float32Array(n);
  const base = i * dims;
  const beta = 1 - alpha;
  for (let j = 0; j < n; j++) {
    const o = j * dims;
    let a = 0, b = 0;
    for (let d = 0; d < dims; d++) {
      a += ing[base + d] * ing[o + d];
      b += tec[base + d] * tec[o + d];
    }
    sims[j] = alpha * a + beta * b;
  }
  sims[i] = -Infinity;
  return sims;
}

/* Wikibooks page titles survive the pipeline unmodified, so the source URL is
 * derivable rather than needing to be stored per recipe. Verified against a random
 * sample of 25 titles, all of which resolved. */
function sourceUrl(node) {
  return 'https://en.wikibooks.org/wiki/Cookbook:' +
    encodeURIComponent(node.title.replace(/ /g, '_'));
}

/* 'have' / 'need' per ingredient, or null when no pantry is set. Staples count as
 * had, since the pantry logic assumes them anyway. */
function pantryStatus(ingredient) {
  if (!state.pantry) return null;
  const low = ingredient.toLowerCase();
  if (STAPLES.has(low)) return 'have';
  for (const owned of state.pantry) {
    if (low.includes(owned) || owned.includes(low)) return 'have';
  }
  return 'need';
}

function ingredientMarkup(node, limit) {
  /* Ingredients are alphabetical, so a flat truncation hid the very thing the pantry
   * matched: Paneer is 13th of 16 in Paneer Butter Masala, so "paneer" lit the dish
   * green while the tooltip showed ten ingredients, none of them paneer. What you
   * have goes first whenever a pantry is set. */
  let order = node.ingredients;
  if (state.pantry) {
    const have = order.filter((i) => pantryStatus(i) === 'have');
    order = have.concat(order.filter((i) => pantryStatus(i) !== 'have'));
  }
  const shown = limit ? order.slice(0, limit) : order;
  const parts = shown.map((ing) => {
    const status = pantryStatus(ing);
    return status ? `<span class="${status}">${ing}</span>` : ing;
  });
  const extra = order.length - shown.length;
  return parts.join(' · ') + (extra > 0 ? ` +${extra} more` : '');
}

/* An "open the recipe" affordance that does not require building a route first.
 * stopPropagation so clicking it does not also select the dish and start a route. */
function recipeLink(node) {
  return `<a class="ext" href="${sourceUrl(node)}" target="_blank" rel="noopener"` +
    ` title="Open the recipe on Wikibooks" onclick="event.stopPropagation()">recipe</a>`;
}

/* Filters constrain the ROUTABLE GRAPH, not just what is drawn. Excluded dishes are
 * dimmed and cannot be stepped through, so "vegetarian only" produces a genuinely
 * vegetarian path rather than a path that merely looks filtered. */
function allowed(i) {
  const node = state.nodes[i];
  const { diet, cuisine } = state.filter;
  if (cuisine !== 'all' && node.cuisine !== cuisine) return false;
  if (diet === 'all') return true;
  if (diet === 'veg') return node.diet === 'veg' || node.diet === 'vegan';  // vegan ⊂ veg
  return node.diet === diet;
}

function allowedCount() {
  let total = 0;
  for (let i = 0; i < state.n; i++) if (allowed(i)) total++;
  return total;
}

function neighbours(i) {
  const cached = state.neighbourCache.get(i);
  if (cached) return cached;

  const sims = similarityTo(i);
  // Partial selection: K passes over n beats sorting all 3226 entries.
  const picked = [];
  const taken = new Uint8Array(state.n);
  for (let slot = 0; slot < K; slot++) {
    let best = -1, bestVal = -Infinity;
    for (let j = 0; j < state.n; j++) {
      if (!taken[j] && sims[j] > bestVal && allowed(j)) { bestVal = sims[j]; best = j; }
    }
    if (best < 0) break;
    taken[best] = 1;
    picked.push({ j: best, sim: bestVal });
  }
  state.neighbourCache.set(i, picked);
  return picked;
}

function route(src, dst) {
  if (src < 0 || dst < 0 || src === dst) return [];
  if (!allowed(src) || !allowed(dst)) return [];
  const dist = new Float32Array(state.n).fill(Infinity);
  const prev = new Int32Array(state.n).fill(-1);
  const done = new Uint8Array(state.n);
  dist[src] = 0;
  const heap = [[0, src]];

  while (heap.length) {
    heap.sort((a, b) => a[0] - b[0]);       // small frontier; sort is fine here
    const [cost, node] = heap.shift();
    if (done[node]) continue;
    done[node] = 1;
    if (node === dst) break;
    for (const { j, sim } of neighbours(node)) {
      const w = Math.max(1 - sim, 1e-4);
      if (cost + w < dist[j]) { dist[j] = cost + w; prev[j] = node; heap.push([dist[j], j]); }
    }
  }

  if (dst !== src && prev[dst] < 0) return [];
  const path = [dst];
  while (path[path.length - 1] !== src) path.push(prev[path[path.length - 1]]);
  return path.reverse();
}

/* ---------- view ---------- */

function fitView() {
  canvas.width = window.innerWidth * devicePixelRatio;
  canvas.height = window.innerHeight * devicePixelRatio;
  canvas.style.width = window.innerWidth + 'px';
  canvas.style.height = window.innerHeight + 'px';
  const pad = 90 * devicePixelRatio;
  state.view.scale = Math.min(canvas.width - pad * 2, canvas.height - pad * 2) / 2;
  state.baseScale = state.baseScale || state.view.scale;
  state.view.x = canvas.width / 2;
  state.view.y = canvas.height / 2;
}

const toScreen = (node) => [
  node.x * state.view.scale + state.view.x,
  node.y * state.view.scale + state.view.y,
];

function hueFor(cuisine) {
  if (!cuisine) return null;
  let h = 0;
  for (let i = 0; i < cuisine.length; i++) h = (h * 31 + cuisine.charCodeAt(i)) % 360;
  return h;
}

/* ---------- pantry ---------- */

/* Assumed to be in every kitchen. Without this, almost nothing is ever "makeable":
 * the median recipe has 8 ingredients and salt/water/oil are three of them, so a
 * literal reading of a pantry list makes the feature useless. */
const STAPLES = new Set([
  'salt', 'water', 'pepper', 'black pepper', 'sugar', 'oil', 'vegetable oil',
  'olive oil', 'oil and fat', 'cooking oil',
]);

function setPantry(text) {
  const items = text.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!items.length) {
    state.pantry = null;
    state.missing = null;
    state.matched = null;
    state.pantryHits = null;
    document.getElementById('pantryRead').textContent =
      'Staples (salt, water, pepper, oil, sugar) assumed.';
    renderPantryList();
    return;
  }

  state.pantry = new Set(items);
  state.missing = new Int16Array(state.n);
  state.matched = new Int16Array(state.n);
  for (let i = 0; i < state.n; i++) {
    let short = 0, hits = 0;
    for (const ingredient of state.nodes[i].ingredients) {
      const low = ingredient.toLowerCase();
      if (STAPLES.has(low)) continue;
      // Substring match both ways so "onion" covers "Red Onion" and vice versa.
      let have = false;
      for (const owned of state.pantry) {
        if (low.includes(owned) || owned.includes(low)) { have = true; break; }
      }
      if (have) hits++;
      else short++;
    }
    state.missing[i] = short;
    state.matched[i] = hits;
  }

  /* A dish only counts if it uses at least one thing you actually typed. Without this,
   * anything built purely from assumed staples (simple syrup, boiled water) shows up as
   * "makeable" no matter what you own, which is noise dressed up as a result. */
  const makeable = [], nearly = [], rest = [];
  for (let i = 0; i < state.n; i++) {
    if (!allowed(i) || state.matched[i] === 0) continue;
    if (state.missing[i] === 0) makeable.push(i);
    else if (state.missing[i] <= 2) nearly.push(i);
    else rest.push(i);
  }
  /* Ranking by coverage, not by absolute shortfall. "Missing 2" means something very
   * different for a 3-ingredient dish than for a 20-ingredient one, and the absolute
   * band has a hard ceiling: a pantry of P items can only ever reach dishes with P+2
   * ingredients or fewer, which puts 80% of the map out of range for a one-item
   * pantry no matter what that item is. That is why typing "paneer" alone reported
   * nothing: the smallest paneer dish has 4 ingredients, so its best possible
   * shortfall is 3, permanently past the cutoff. */
  const total = (i) => state.matched[i] + state.missing[i];
  const coverage = (i) => state.matched[i] / total(i);
  const byCoverage = (a, b) => coverage(b) - coverage(a);
  /* Everything makeable has coverage 1, so ranking those by coverage does nothing and
   * leaves them in index order, which offered "Garlic Salt" at the top of 13 dishes.
   * Substantial dishes first instead: a 9-ingredient curry you can make is a better
   * answer than a 1-ingredient garnish you can also make. */
  makeable.sort((a, b) => total(b) - total(a));
  nearly.sort(byCoverage);
  rest.sort(byCoverage);
  state.coverage = coverage;
  state.pantryHits = { makeable, nearly, rest };

  const using = makeable.length + nearly.length + rest.length;
  const read = document.getElementById('pantryRead');
  if (!using) {
    read.textContent = 'No dish uses any of those. Check the spelling?';
  } else {
    /* "What can I cook" and "what uses this" are different questions, and one
     * ingredient only ever answers the second. Both counts are shown so a single
     * ingredient gives a real answer instead of two zeroes. */
    read.textContent = `${using} dish${using > 1 ? 'es' : ''} use this · ` +
      `${makeable.length} makeable now · ${nearly.length} within 2`;
  }
  renderPantryList();
}

/* Routing by search only works if you can tell what the next pick will do. Without
 * this the panel still says "Find a dish" after you have chosen a start, so picking a
 * second dish looks like it replaced the first rather than completing a route. */
/* Shows which dishes are picked and what to do next, directly under the search box.
 * A selected dot is 4px wide on a map of 3,222 of them, so the map alone cannot tell
 * anyone that their click registered, let alone that a second pick is wanted. */
function renderPicks() {
  const slots = [
    { el: document.getElementById('pickA'), idx: state.start, fallback: 'nothing picked yet' },
    { el: document.getElementById('pickB'), idx: state.end, fallback: 'nothing picked yet' },
  ];
  const nextSlot = state.start < 0 ? 0 : (state.end < 0 ? 1 : -1);

  slots.forEach((slot, i) => {
    if (!slot.el) return;
    const who = slot.el.querySelector('.who');
    const clear = slot.el.querySelector('.x');
    const link = slot.el.querySelector('.ext');
    const picked = slot.idx >= 0;
    if (link) {
      link.hidden = !picked;
      if (picked) link.href = sourceUrl(state.nodes[slot.idx]);
    }
    who.textContent = picked ? state.nodes[slot.idx].title
      : (i === nextSlot ? 'pick this one next' : slot.fallback);
    slot.el.className = 'pick' + (picked ? ' set' : (i === nextSlot ? ' next' : ''));
    if (clear) clear.hidden = !picked;
  });

  const hint = document.getElementById('pickHint');
  if (!hint) return;
  if (state.start < 0) hint.textContent = 'Search a dish, or click any dot, to pick A.';
  else if (state.end < 0) hint.textContent = 'Now pick B the same way. The route appears below.';
  else hint.textContent = 'Drag the Similarity slider to re-route between the two.';
}

function updateSearchLabel() {
  const label = document.getElementById('searchLbl');
  if (!label) return;
  if (state.start >= 0 && state.end >= 0) label.textContent = 'Find a dish (starts a new route)';
  else if (state.start >= 0) label.textContent = 'Now find a destination';
  else label.textContent = 'Find a dish';
}

function clearSearch() {
  const box = document.getElementById('search');
  if (box) box.value = '';
  const results = document.getElementById('results');
  if (results) results.innerHTML = '';
  state.topResult = -1;
}

function focusNode(i) {
  select(i);
  const [x, y] = toScreen(state.nodes[i]);
  state.view.x += canvas.width / 2 - x;
  state.view.y += canvas.height / 2 - y;
  draw();
}

function focusFromSearch(i) {
  focusNode(i);
  clearSearch();
}

function renderPantryList() {
  const box = document.getElementById('pantryList');
  if (!box) return;
  box.innerHTML = '';
  if (!state.pantryHits) return;

  const { makeable, nearly, rest } = state.pantryHits;
  const have = (i) => `${state.matched[i]} of ${state.matched[i] + state.missing[i]}`;
  // Capped only to keep the DOM small; the list scrolls.
  const rows = [
    ...makeable.map((i) => [i, 'all ' + (state.matched[i] + state.missing[i])]),
    ...nearly.map((i) => [i, have(i)]),
    ...(rest || []).map((i) => [i, have(i)]),
  ].slice(0, 80);
  for (const [i, badge] of rows) {
    const row = document.createElement('div');
    row.innerHTML = `<span>${state.nodes[i].title}</span>` +
      `<span class="badge">${badge}</span>` + recipeLink(state.nodes[i]);
    row.onclick = () => focusNode(i);
    box.appendChild(row);
  }
}

document.getElementById('pantry').addEventListener('input', (e) => {
  setPantry(e.target.value);
  draw();
});

/* ---------- drawing ---------- */

function draw() {
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const dpr = devicePixelRatio;
  const onPath = new Set(state.path);

  // hovered node's neighbourhood, drawn only on demand -- rendering all ~39k edges
  // would be an unreadable hairball and slow.
  if (state.hover >= 0) {
    const [hx, hy] = toScreen(state.nodes[state.hover]);
    ctx.strokeStyle = 'rgba(127,163,122,.45)';
    ctx.lineWidth = 1 * dpr;
    for (const { j } of neighbours(state.hover)) {
      const [x, y] = toScreen(state.nodes[j]);
      ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(x, y); ctx.stroke();
    }
  }

  if (state.path.length > 1) {
    ctx.strokeStyle = ACCENT;
    ctx.lineWidth = 2 * dpr;
    ctx.beginPath();
    state.path.forEach((idx, step) => {
      const [x, y] = toScreen(state.nodes[idx]);
      step ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }

  for (let i = 0; i < state.n; i++) {
    const node = state.nodes[i];
    const [x, y] = toScreen(node);
    const special = onPath.has(i) || i === state.hover || i === state.start || i === state.end;
    const hue = hueFor(node.cuisine);
    const ok = allowed(i);

    // In pantry mode, proximity to "I can cook this" replaces cuisine colour, since
    // two colour scales at once is unreadable.
    let radius = special ? 4.6 : ok ? 2.1 : 1.1;
    let fill;
    if (special) fill = ACCENT;
    else if (!ok) fill = 'rgba(120,114,104,.16)';
    else if (state.missing) {
      const short = state.missing[i];
      const uses = state.matched && state.matched[i] > 0;
      if (uses && short === 0) { fill = 'rgba(127,195,120,.95)'; radius = 3.4; }
      else if (uses && short <= 2) { fill = 'rgba(214,183,96,.72)'; radius = 2.6; }
      // Uses something you typed but needs more than two others. Dim, but findable:
      // with one ingredient this is the only band that ever has members, and fading
      // it out left the map looking empty.
      else if (uses) { fill = 'rgba(170,150,215,.52)'; radius = 2.2; }
      else fill = 'rgba(120,114,104,.12)';
    }
    else if (hue !== null) fill = `hsla(${hue},42%,62%,.82)`;
    else fill = 'rgba(148,141,128,.5)';

    ctx.beginPath();
    ctx.arc(x, y, radius * dpr, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
  }

  /* Label only the path and the hovered node: labelling every point is noise.
   * Routes often pass through a tight cluster, and drawing a label per step there
   * stacks four titles on top of each other into an unreadable smear, so a label is
   * skipped when its box would overlap one already drawn. Endpoints are drawn first
   * so they win the space, and long titles near the right edge get pulled back inside
   * instead of running off screen. */
  /* A ring around each picked dish. The dot itself only grows from 2px to 4.6px, which
   * is not a visible change on a map this dense, so people could not tell their click
   * had done anything. */
  for (const idx of [state.start, state.end]) {
    if (idx < 0) continue;
    const [x, y] = toScreen(state.nodes[idx]);
    ctx.beginPath();
    ctx.arc(x, y, 8 * dpr, 0, Math.PI * 2);
    ctx.strokeStyle = ACCENT;
    ctx.lineWidth = 1.4 * dpr;
    ctx.stroke();
  }

  ctx.font = `${12 * dpr}px Inter, sans-serif`;
  ctx.textBaseline = 'alphabetic';
  // Picked dishes stay labelled whether or not a route exists yet, so picking one
  // puts its name on the map instead of silently recolouring a dot.
  const picked = state.path.length
    ? state.path
    : [state.start, state.end].filter((i) => i >= 0);
  const labelled = state.hover >= 0 && !picked.includes(state.hover)
    ? [...picked, state.hover] : picked;
  const order = state.path.length
    ? [state.path[0], state.path[state.path.length - 1], ...state.path.slice(1, -1)]
    : labelled;

  const placed = [];
  const pad = 3 * dpr, lineH = 15 * dpr;
  // The usable width ends at the side panel, not at the window edge, or endpoint
  // labels slide underneath it and get cut in half.
  const panel = document.getElementById('panel');
  const rect = panel ? panel.getBoundingClientRect() : null;
  // offsetParent is always null for a position:fixed element, so visibility has to be
  // judged from the rect. On narrow screens the panel moves to the bottom, where it
  // does not take horizontal space, so it only constrains the width when it is right.
  const onRight = rect && rect.width > 0 && rect.left > window.innerWidth * 0.5;
  const panelLeft = onRight ? rect.left * dpr : canvas.width;
  const rightEdge = Math.min(canvas.width, panelLeft) - 10 * dpr;
  for (const idx of order) {
    const title = state.nodes[idx].title;
    const [x, y] = toScreen(state.nodes[idx]);
    const w = ctx.measureText(title).width;
    let lx = x + 8 * dpr, ly = y - 8 * dpr;
    if (lx + w > rightEdge) lx = Math.max(6 * dpr, x - 8 * dpr - w);
    const box = { l: lx - pad, r: lx + w + pad, t: ly - lineH, b: ly + pad };
    if (placed.some((p) => box.l < p.r && box.r > p.l && box.t < p.b && box.b > p.t)) {
      continue;
    }
    placed.push(box);
    // A dark backing keeps a title readable where it crosses a dense patch of dots.
    ctx.fillStyle = BG;
    ctx.globalAlpha = 0.72;
    ctx.fillRect(box.l, box.t, box.r - box.l, box.b - box.t);
    ctx.globalAlpha = 1;
    ctx.fillStyle = '#f2efe8';
    ctx.fillText(title, lx, ly);
  }
}

/* ---------- interaction ---------- */

function pick(clientX, clientY) {
  const mx = clientX * devicePixelRatio, my = clientY * devicePixelRatio;
  let best = -1, bestDist = 14 * devicePixelRatio;
  for (let i = 0; i < state.n; i++) {
    const [x, y] = toScreen(state.nodes[i]);
    const d = Math.hypot(x - mx, y - my);
    if (d < bestDist) { bestDist = d; best = i; }
  }
  return best;
}

canvas.addEventListener('mousemove', (e) => {
  if (dragging) {
    state.view.x += (e.clientX - last.x) * devicePixelRatio;
    state.view.y += (e.clientY - last.y) * devicePixelRatio;
    last = { x: e.clientX, y: e.clientY };
    draw();
    return;
  }
  const hit = pick(e.clientX, e.clientY);
  if (hit !== state.hover) { state.hover = hit; draw(); }
  if (hit >= 0) {
    const node = state.nodes[hit];
    tooltip.hidden = false;
    tooltip.style.left = Math.min(e.clientX + 14, window.innerWidth - 300) + 'px';
    tooltip.style.top = (e.clientY + 14) + 'px';
    const tags = [node.cuisine, node.course, node.diet].filter(Boolean).join(' · ');
    const shortfall = state.missing
      ? (state.missing[hit] === 0
          ? '<div class="c">you can make this now</div>'
          : `<div class="c">missing ${state.missing[hit]} ingredient${state.missing[hit] > 1 ? 's' : ''}</div>`)
      : '';
    tooltip.innerHTML =
      `<div class="t">${node.title}</div>` +
      (tags ? `<div class="c">${tags}</div>` : '') + shortfall +
      `<div class="i">${ingredientMarkup(node, 10)}</div>` +
      `<div class="src">click to route · "recipe" opens the full instructions</div>`;
  } else {
    tooltip.hidden = true;
  }
});

let dragging = false, last = { x: 0, y: 0 }, moved = false;
canvas.addEventListener('mousedown', (e) => {
  dragging = true; moved = false; last = { x: e.clientX, y: e.clientY };
  canvas.classList.add('dragging');
});
window.addEventListener('mouseup', (e) => {
  canvas.classList.remove('dragging');
  const wasDragging = dragging;
  dragging = false;
  if (!wasDragging || moved) return;
  const hit = pick(e.clientX, e.clientY);
  if (hit >= 0) select(hit);
});
canvas.addEventListener('mousemove', () => { if (dragging) moved = true; }, { capture: true });

/* Zoom is clamped relative to the initial fitted scale. Unbounded zoom lets you
 * scroll until the whole map is a single pixel or one dot fills the screen, and in
 * both cases there is no obvious way back. */
const ZOOM_MIN = 0.9, ZOOM_MAX = 8;

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const base = state.baseScale || state.view.scale;
  const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
  const wanted = state.view.scale * factor;
  const clamped = Math.min(Math.max(wanted, base * ZOOM_MIN), base * ZOOM_MAX);
  if (clamped === state.view.scale) return;          // already at a limit

  const applied = clamped / state.view.scale;         // zoom about the cursor
  const mx = e.clientX * devicePixelRatio, my = e.clientY * devicePixelRatio;
  state.view.x = mx - (mx - state.view.x) * applied;
  state.view.y = my - (my - state.view.y) * applied;
  state.view.scale = clamped;
  draw();
}, { passive: false });

window.addEventListener('resize', () => { fitView(); draw(); });

/* ---------- route panel ---------- */

function select(i) {
  /* Picking the same dish twice is the normal way to use this: you search for it to
   * find out where it is, which selects it, and then you click its dot because that
   * is what selecting a thing looks like. Counting that as two picks set the
   * destination to the start, producing a route from a dish to itself and a "no path"
   * message, and the next dish then looked like it wiped the selection. */
  if (i === state.start && state.end < 0) return;
  // Same reasoning for the destination: clicking the dot of the dish you just routed
  // to would otherwise throw the finished route away and start a new one from it.
  if (i === state.end) return;
  if (state.start < 0 || (state.start >= 0 && state.end >= 0)) {
    state.start = i; state.end = -1; state.path = [];
  } else {
    state.end = i;
    state.path = route(state.start, state.end);
  }
  renderRoute();
  updateSearchLabel();
  renderPicks();
  draw();
}

function shared(a, b) {
  const setB = new Set(state.nodes[b].ingredients);
  return state.nodes[a].ingredients.filter((x) => setB.has(x));
}

function renderRoute() {
  const stateEl = document.getElementById('routeState');
  const list = document.getElementById('routeList');
  const clear = document.getElementById('clear');
  list.innerHTML = '';

  if (state.start < 0) {
    stateEl.textContent = 'Click a dish on the map, or search for one, to set the start.';
    clear.hidden = true;
    return;
  }
  clear.hidden = false;

  if (state.end < 0) {
    stateEl.textContent = 'Pick B above to see the route.';
    return;
  }
  if (!state.path.length) {
    stateEl.textContent = (!allowed(state.start) || !allowed(state.end))
      ? 'One endpoint is excluded by the current filter.'
      : 'No path at this slider position. Try loosening the filter or moving the slider.';
    return;
  }

  // Mean ingredient overlap between consecutive steps: the same path-smoothness measure
  // the Python reference implementation reports, so the UI and the analysis agree.
  let total = 0;
  for (let s = 0; s < state.path.length - 1; s++) {
    const a = new Set(state.nodes[state.path[s]].ingredients);
    const b = new Set(state.nodes[state.path[s + 1]].ingredients);
    const inter = [...a].filter((x) => b.has(x)).length;
    total += inter / (new Set([...a, ...b]).size || 1);
  }
  const smooth = (total / (state.path.length - 1)).toFixed(2);
  stateEl.innerHTML = `${state.path.length} steps · ingredient smoothness <span class="mono">${smooth}</span>`;

  state.path.forEach((idx, step) => {
    const row = document.createElement('div');
    row.className = 'step';
    const link = step > 0 ? shared(state.path[step - 1], idx).slice(0, 3) : [];
    const node = state.nodes[idx];
    row.innerHTML =
      `<span class="n">${step}</span><span><a class="dish" href="${sourceUrl(node)}" ` +
      `target="_blank" rel="noopener" title="Open the recipe on Wikibooks">${node.title}</a>` +
      (link.length ? `<div class="shared">shares ${link.join(', ')}</div>` : '') + `</span>`;
    list.appendChild(row);
  });
}

document.getElementById('pickA').querySelector('.x').addEventListener('click', () => {
  // Clearing the start with a destination still set would leave a dangling end, so
  // the destination is promoted into the empty start slot instead of vanishing.
  state.start = state.end; state.end = -1; state.path = [];
  renderRoute(); updateSearchLabel(); renderPicks(); draw();
});
document.getElementById('pickB').querySelector('.x').addEventListener('click', () => {
  state.end = -1; state.path = [];
  renderRoute(); updateSearchLabel(); renderPicks(); draw();
});

document.getElementById('clear').addEventListener('click', () => {
  state.start = state.end = -1; state.path = [];
  renderRoute(); updateSearchLabel(); renderPicks(); draw();
});

/* ---------- slider + search ---------- */

document.getElementById('alpha').addEventListener('input', (e) => {
  state.alpha = e.target.value / 100;
  state.neighbourCache.clear();   // neighbours are alpha-dependent
  const pct = Math.round(state.alpha * 100);
  document.getElementById('alphaRead').textContent =
    `${pct}% ingredients / ${100 - pct}% technique`;
  if (state.start >= 0 && state.end >= 0) state.path = route(state.start, state.end);
  renderRoute();
  draw();
});

/* Enter picks the top hit. A search box where Enter does nothing reads as broken:
 * you type a dish, press Enter, nothing is selected, and the next dish you click
 * looks like it replaced a selection that was never made. */
document.getElementById('search').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  if (state.topResult >= 0) focusFromSearch(state.topResult);
});

document.getElementById('search').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  const box = document.getElementById('results');
  box.innerHTML = '';
  if (!q || !state.n) return;   // respond from the first character
  /* Searching ingredients as well as titles: five recipes USE paneer but only two say
   * so in the name, and a title-only search made the rest invisible. */
  const scored = [];
  for (const n of state.nodes) {
    if (!allowed(n.i)) continue;
    const inTitle = n.title.toLowerCase().includes(q);
    const ingredient = inTitle ? null : n.ingredients.find((x) => x.toLowerCase().includes(q));
    if (!inTitle && !ingredient) continue;
    scored.push([n, inTitle ? 0 : 1, ingredient]);   // title matches rank first
  }
  scored.sort((a, b) => a[1] - b[1]);
  state.topResult = scored.length ? scored[0][0].i : -1;
  scored
    .slice(0, 14)
    .forEach(([n, , ingredient]) => {
      const row = document.createElement('div');
      row.innerHTML = `<span>${n.title}</span>` +
        (ingredient ? `<span class="badge">${ingredient}</span>` : '') + recipeLink(n);
      row.onclick = () => focusFromSearch(n.i);
      box.appendChild(row);
    });
});

load();
