/* Interaction tests for docs/app.js.
 *
 * The Python tests cover the data pipeline and caught real bugs there, but every
 * interface bug in this project shipped past them, because none of them load the page.
 * These run the real docs/app.js against a stub DOM and drive it the way a person
 * does. Like the Python ones, each case here is a bug that actually shipped.
 *
 * Run: node tests/test_ui.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const graphPath = path.join(root, 'docs', 'data', 'graph.json');

function makeEl(id) {
  const el = {
    id, style: {}, dataset: {}, children: [], textContent: '', value: '', hidden: false,
    width: 1200, height: 800,
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(type, fn) { (this.handlers || (this.handlers = {}))[type] = fn; },
    appendChild(child) { this.children.push(child); },
    querySelectorAll: () => [],
    // Elements built from markup have inner parts; hand back a stub per selector so
    // the pick slots (.who, .x) behave like the real nodes.
    querySelector(sel) {
      const parts = this.parts || (this.parts = {});
      return parts[sel] || (parts[sel] = makeEl(this.id + sel));
    },
    getContext: () => new Proxy({}, {
      get: (_t, k) => (k === 'measureText' ? () => ({ width: 80 }) : () => {}),
      set: () => true,
    }),
    getBoundingClientRect: () => ({ left: 900, top: 0, width: 296, height: 800 }),
  };
  let html = '';
  Object.defineProperty(el, 'innerHTML', {
    get: () => html,
    set(v) { html = v; if (v === '') el.children.length = 0; },
  });
  return el;
}

function loadApp() {
  const graph = JSON.parse(fs.readFileSync(graphPath, 'utf8'));
  const els = {};
  const document = {
    getElementById: (id) => els[id] || (els[id] = makeEl(id)),
    createElement: () => makeEl('created'),
    querySelectorAll: () => [],
    addEventListener() {},
    body: makeEl('body'),
  };
  const sandbox = {
    document, console, JSON, Math, Set, Map, Array, Object, Number, String,
    Int16Array, Float32Array, isNaN, parseInt, parseFloat, setTimeout, clearTimeout,
    devicePixelRatio: 1,
    requestAnimationFrame: (f) => f(),
    getComputedStyle: () => ({ getPropertyValue: () => '#5bb8d4' }),
    window: { innerWidth: 1200, innerHeight: 800, addEventListener() {}, devicePixelRatio: 1 },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(graph) }),
  };
  sandbox.globalThis = sandbox;
  const code = fs.readFileSync(path.join(root, 'docs', 'app.js'), 'utf8');
  vm.runInContext(
    code + '\nglobalThis.__api = { state, select, focusNode, setPantry, updateSearchLabel };',
    vm.createContext(sandbox),
  );
  return new Promise((resolve) => {
    setTimeout(() => resolve({ api: sandbox.__api, els }), 1200);
  });
}

let failures = 0;
function check(label, ok, detail) {
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${label}` + (!ok && detail ? `  ${detail}` : ''));
  if (!ok) failures++;
}

function indexOf(state, title) {
  return state.nodes.findIndex((n) => n.title === title);
}

async function main() {
  const { api, els } = await loadApp();
  const { state } = api;

  check('graph loads', state.n > 3000, `n=${state.n}`);

  /* Searching for a dish selects it, so clicking its dot afterwards selected it a
   * second time and set the destination to the start. The route was then a dish to
   * itself, and the next dish appeared to wipe the selection. */
  const a = indexOf(state, 'Chicken Tikka Masala');
  const b = indexOf(state, 'Caesar Salad');
  api.select(a);
  api.select(a);
  check('selecting the same dish twice leaves it as the start',
    state.start === a && state.end === -1,
    `start=${state.start} end=${state.end}`);

  api.select(b);
  check('a second, different dish completes a route',
    state.start === a && state.end === b && state.path.length > 1,
    `start=${state.start} end=${state.end} path=${state.path.length}`);

  // Re-clicking the destination used to discard the finished route and start again.
  const before = state.path.length;
  api.select(b);
  check('re-picking the destination keeps the route',
    state.start === a && state.end === b && state.path.length === before,
    `start=${state.start} end=${state.end} path=${state.path.length}`);

  const c = indexOf(state, 'Falafel');
  api.select(c);
  check('picking a third dish starts a new route',
    state.start === c && state.end === -1,
    `start=${state.start} end=${state.end}`);

  // Enter in the search box must pick the top hit; with no handler it did nothing,
  // and the next click looked like it replaced a selection that was never made.
  const search = els['search'];
  search.value = 'caesar salad';
  search.handlers.input({ target: { value: 'caesar salad' } });
  check('typing populates a top result',
    state.topResult >= 0 && state.nodes[state.topResult].title === 'Caesar Salad',
    `topResult=${state.topResult}`);
  search.handlers.keydown({ key: 'Enter', preventDefault() {} });
  check('Enter picks the top result', state.start === b || state.end === b,
    `start=${state.start} end=${state.end}`);
  check('picking from search clears the query', search.value === '',
    `value=${JSON.stringify(search.value)}`);

  // Search matched titles only, so dishes that merely use an ingredient were invisible.
  search.value = 'paneer';
  search.handlers.input({ target: { value: 'paneer' } });
  check('search matches ingredients, not just titles',
    els['results'].children.length > 2,
    `${els['results'].children.length} rows`);

  /* One ingredient can never put a dish "within two", so the pantry reported two
   * zeroes and an empty panel for a perfectly good query. */
  api.setPantry('paneer');
  const hits = state.pantryHits;
  const listed = hits.makeable.length + hits.nearly.length + hits.rest.length;
  check('a single pantry ingredient still lists the dishes that use it',
    listed >= 4 && els['pantryList'].children.length >= 4,
    `${listed} matched, ${els['pantryList'].children.length} rows`);

  // Staples are assumed, so a dish matching nothing you typed must not light up.
  const usesNothing = state.matched && [...state.matched].every((m, i) =>
    m > 0 || !(hits.makeable.includes(i) || hits.nearly.includes(i)));
  check('dishes matching only assumed staples are not highlighted', usesNothing);

  /* New users picked a dish, saw a 4px dot recolour somewhere in 3,222 of them, and
   * could not tell anything had happened or what to do next. */
  const h = indexOf(state, 'Hummus I');
  api.select(h >= 0 ? h : 0);
  const slotA = els['pickA'].querySelector('.who');
  const slotB = els['pickB'].querySelector('.who');
  const hint = els['pickHint'];
  check('picking a dish names it in slot A',
    slotA.textContent === state.nodes[state.start].title,
    'A=' + slotA.textContent);
  check('slot B asks for the next pick', /next/i.test(slotB.textContent),
    'B=' + slotB.textContent);
  check('the hint says what to do next', /pick b/i.test(hint.textContent),
    'hint=' + hint.textContent);

  const g = indexOf(state, 'Guacamole I');
  api.select(g >= 0 ? g : indexOf(state, 'Falafel'));
  check('both slots fill once a route exists',
    slotA.textContent === state.nodes[state.start].title
      && slotB.textContent === state.nodes[state.end].title,
    'A=' + slotA.textContent + ' B=' + slotB.textContent);
  check('the hint moves on to the slider', /slider/i.test(hint.textContent),
    'hint=' + hint.textContent);

  console.log(`\n${failures === 0 ? 'all' : failures} ${failures === 0 ? 'checks passed' : 'failed'}`);
  if (failures) process.exit(1);
}

main().catch((err) => { console.error(err); process.exit(1); });
