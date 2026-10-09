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

  document.getElementById('stat').textContent =
    `${state.n} dishes · ${g.meta.dims}d × 2 spaces · ${g.nodes.filter(n=>n.cuisine).length} cuisine-labelled · all client-side`;

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
    document.getElementById('pantryRead').textContent =
      'Staples (salt, water, pepper, oil, sugar) assumed.';
    return;
  }

  state.pantry = new Set(items);
  state.missing = new Int16Array(state.n);

  for (let i = 0; i < state.n; i++) {
    let short = 0;
    for (const ingredient of state.nodes[i].ingredients) {
      const low = ingredient.toLowerCase();
      if (STAPLES.has(low)) continue;
      // Substring match both ways so "onion" covers "Red Onion" and vice versa.
      let have = false;
      for (const owned of state.pantry) {
        if (low.includes(owned) || owned.includes(low)) { have = true; break; }
      }
      if (!have) short++;
    }
    state.missing[i] = short;
  }

  let now = 0, close = 0;
  for (let i = 0; i < state.n; i++) {
    if (!allowed(i)) continue;
    if (state.missing[i] === 0) now++;
    else if (state.missing[i] <= 2) close++;
  }
  document.getElementById('pantryRead').textContent =
    `${now} makeable now · ${close} within 2 ingredients`;
}

document.getElementById('pantry').addEventListener('input', (e) => {
  setPantry(e.target.value);
  draw();
});

/* ---------- drawing ---------- */

function draw() {
  ctx.fillStyle = '#12100e';
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
    ctx.strokeStyle = '#e2733a';
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
    if (special) fill = '#e2733a';
    else if (!ok) fill = 'rgba(120,114,104,.16)';
    else if (state.missing) {
      const short = state.missing[i];
      if (short === 0) { fill = 'rgba(127,195,120,.95)'; radius = 3.4; }
      else if (short <= 2) { fill = 'rgba(214,183,96,.72)'; radius = 2.6; }
      else fill = 'rgba(120,114,104,.14)';
    }
    else if (hue !== null) fill = `hsla(${hue},42%,62%,.82)`;
    else fill = 'rgba(148,141,128,.5)';

    ctx.beginPath();
    ctx.arc(x, y, radius * dpr, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
  }

  // Label only the path and the hovered node: labelling 3226 points is noise.
  ctx.font = `${12 * dpr}px Inter, sans-serif`;
  ctx.fillStyle = '#f2efe8';
  const labelled = state.path.length ? state.path : (state.hover >= 0 ? [state.hover] : []);
  for (const idx of labelled) {
    const [x, y] = toScreen(state.nodes[idx]);
    ctx.fillText(state.nodes[idx].title, x + 8 * dpr, y - 8 * dpr);
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
      `<div class="i">${node.ingredients.slice(0, 10).join(' · ')}` +
      (node.ingredients.length > 10 ? ` +${node.ingredients.length - 10} more` : '') + `</div>`;
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
const ZOOM_MIN = 0.55, ZOOM_MAX = 14;

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
  if (state.start < 0 || (state.start >= 0 && state.end >= 0)) {
    state.start = i; state.end = -1; state.path = [];
  } else {
    state.end = i;
    state.path = route(state.start, state.end);
  }
  renderRoute();
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
    stateEl.textContent = 'Click a dish on the map to set the start.';
    clear.hidden = true;
    return;
  }
  clear.hidden = false;

  if (state.end < 0) {
    stateEl.innerHTML = `From <strong>${state.nodes[state.start].title}</strong>. Now click a destination.`;
    return;
  }
  if (!state.path.length) {
    stateEl.textContent = (!allowed(state.start) || !allowed(state.end))
      ? 'One endpoint is excluded by the current filter.'
      : 'No path within this filter -- loosen it or move the slider.';
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
    row.innerHTML =
      `<span class="n">${step}</span><span>${state.nodes[idx].title}` +
      (link.length ? `<div class="shared">shares ${link.join(', ')}</div>` : '') + `</span>`;
    list.appendChild(row);
  });
}

document.getElementById('clear').addEventListener('click', () => {
  state.start = state.end = -1; state.path = [];
  renderRoute(); draw();
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

document.getElementById('search').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  const box = document.getElementById('results');
  box.innerHTML = '';
  if (!q || !state.n) return;   // respond from the first character
  state.nodes
    .filter((n) => n.title.toLowerCase().includes(q) && allowed(n.i))
    .slice(0, 12)
    .forEach((n) => {
      const row = document.createElement('div');
      row.textContent = n.title;
      row.onclick = () => {
        select(n.i);
        const [x, y] = toScreen(n);
        state.view.x += canvas.width / 2 - x;
        state.view.y += canvas.height / 2 - y;
        draw();
      };
      box.appendChild(row);
    });
});

load();
