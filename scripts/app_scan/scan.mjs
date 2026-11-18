// Usage: node scan.mjs play|ios   -> out/<store>.json   (public log: counts only)
import fs from 'node:fs';
import * as gp from 'google-play-scraper';
import ast from 'app-store-scraper';
const gplay = gp.default ?? gp;
const store = ast.default ?? ast;
const which = process.argv[2];
const TERMS = JSON.parse(fs.readFileSync(new URL('./terms.json', import.meta.url)));
const WATCH = /photoscan|photo ?scan|mi remote|cantook|apowermirror|ar meter|smartruler|smart ruler|mobills|itranslate/i;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DELAY = 600;
const DAY = 86400000;
let errors = 0, blocks = 0, claudeCalls = 0;
const CLAUDE_CAP = 60;

async function retry(fn, label) {
  for (let i = 0; i < 4; i++) {
    try { await sleep(DELAY); return await fn(); }
    catch (e) {
      const msg = String((e && e.message) || e);
      if (/429|403|blocked|captcha|too many/i.test(msg)) blocks++;
      if (i === 3) { errors++; console.log(`fail ${label}`); return null; }
      await sleep(2000 * 2 ** i);
    }
  }
}
const ts = (d) => (d ? new Date(d).getTime() : null);

function normPlay(a) {
  return { store: 'play', id: a.appId, title: a.title, developer: a.developer,
    installs: a.minInstalls ?? null, maxInstalls: a.maxInstalls ?? null, rating: a.score ?? null,
    ratings: a.ratings ?? null, reviews: a.reviews ?? null, histogram: a.histogram ?? null,
    updated: a.updated ? new Date(a.updated).toISOString() : null, released: a.released ?? null,
    free: a.free, price: a.price, iap: !!a.offersIAP, ads: !!a.adSupported, genre: a.genre };
}
function normIos(a) {
  return { store: 'ios', id: String(a.id), title: a.title, developer: a.developer,
    installs: null, rating: a.score ?? null, ratings: a.reviews ?? null, histogram: null,
    updated: a.updated ?? null, released: a.released ?? null, free: a.free, price: a.price,
    iap: null, ads: null, genre: a.primaryGenre };
}

async function runSearch() {
  const apps = {}; const hits = [];
  let done = 0;
  for (const term of TERMS) {
    if (which === 'play') {
      const res = await retry(() => gplay.search({ term, num: 30, country: 'us', lang: 'en' }), 'search');
      (res || []).forEach((a, i) => hits.push({ term, rank: i + 1, id: a.appId }));
    } else {
      const res = await retry(() => store.search({ term, num: 30, country: 'us', lang: 'en' }), 'search');
      (res || []).forEach((a, i) => { const n = normIos(a); apps[n.id] = apps[n.id] || n; hits.push({ term, rank: i + 1, id: n.id }); });
    }
    done++;
    if (done % 10 === 0) console.log(`${which}: terms ${done}/${TERMS.length}`);
  }
  if (which === 'play') {
    const ids = [...new Set(hits.map((h) => h.id))];
    let n = 0;
    for (const id of ids) {
      const d = await retry(() => gplay.app({ appId: id, country: 'us', lang: 'en' }), 'detail');
      if (d) apps[id] = normPlay(d);
      if (++n % 100 === 0) console.log(`play: details ${n}/${ids.length}`);
    }
  }
  return { apps, hits };
}

function lowShare(reviews) {
  const cut = Date.now() - 90 * DAY;
  const r90 = reviews.filter((r) => r.t >= cut);
  const low = r90.filter((r) => r.score <= 2);
  return { n90: r90.length, low90: low.length, lowShare90: r90.length ? low.length / r90.length : null,
    n30: reviews.filter((r) => r.t >= Date.now() - 30 * DAY).length };
}

async function getReviews(app) {
  let raw = [];
  if (which === 'play') {
    const r = await retry(() => gplay.reviews({ appId: app.id, sort: gplay.sort.NEWEST, num: 150, country: 'us', lang: 'en' }), 'reviews');
    raw = ((r && r.data) || []).map((x) => ({ t: ts(x.date), score: x.score, text: x.text }));
  } else {
    for (let p = 1; p <= 3; p++) {
      const r = await retry(() => store.reviews({ id: Number(app.id), country: 'us', page: p, sort: store.sort.RECENT }), 'reviews');
      if (!r || !r.length) break;
      raw.push(...r.map((x) => ({ t: ts(x.updated), score: x.score, text: x.text })));
    }
  }
  return raw.filter((r) => r.t);
}

async function themes(app, lows) {
  if (claudeCalls >= CLAUDE_CAP || lows.length < 5 || !process.env.ANTHROPIC_API_KEY) return null;
  claudeCalls++;
  const texts = lows.slice(0, 60).map((r, i) => `${i + 1}. ${String(r.text || '').replace(/\s+/g, ' ').slice(0, 220)}`).join('\n');
  const prompt = `These are recent 1-2 star reviews of the app "${app.title}". Return ONLY JSON: {"themes":[{"theme":"short label","quote":"one short real quote under 140 chars"}]} with the top 3 complaint themes.\n\n${texts}`;
  try {
    await sleep(500);
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: { 'x-api-key': process.env.ANTHROPIC_API_KEY, 'anthropic-version': '2023-06-01', 'content-type': 'application/json' },
      body: JSON.stringify({ model: 'claude-haiku-5-5', max_tokens: 3000, output_config: { effort: 'low' }, messages: [{ role: 'user', content: prompt }] }),
    });
    if (!res.ok) { errors++; console.log(`claude http ${res.status}`); return null; }
    const j = await res.json();
    const txt = (j.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('');
    const m = txt.match(/\{[\s\S]*\}/);
    return m ? JSON.parse(m[0]).themes : null;
  } catch (e) { errors++; console.log('claude parse/err'); return null; }
}

const { apps, hits } = await runSearch();
const list = Object.values(apps);
console.log(`${which}: apps ${list.length}, hits ${hits.length}`);

const now = Date.now();
const size = (a) => (which === 'play' ? a.installs || 0 : a.ratings || 0);
const isGiant = (a) => (which === 'play' ? (a.installs || 0) >= 10e6 : (a.ratings || 0) >= 50000);
const watch = list.filter((a) => WATCH.test(a.title) && size(a) > 0);
const qual = list.filter((a) => {
  const stale = a.updated && now - ts(a.updated) > 365 * DAY;
  return isGiant(a) && ((a.rating != null && a.rating < 4.2) || stale);
});
const cands = [...new Set([...watch, ...qual.sort((a, b) => size(b) - size(a))])].slice(0, 150);
console.log(`${which}: deep candidates ${cands.length} (watch ${watch.length})`);

let i = 0;
for (const a of cands) {
  const rev = await getReviews(a);
  Object.assign(a, { deep: true, watch: WATCH.test(a.title), reviewsFetched: rev.length, ...lowShare(rev) });
  const lows = rev.filter((r) => r.score <= 2 && r.t >= now - 120 * DAY).sort((x, y) => y.t - x.t);
  a.themes = await themes(a, lows.length >= 5 ? lows : rev.filter((r) => r.score <= 2));
  if (++i % 20 === 0) console.log(`${which}: deep ${i}/${cands.length}`);
}
fs.mkdirSync('out', { recursive: true });
fs.writeFileSync(`out/${which}.json`, JSON.stringify({ store: which, scannedAt: new Date().toISOString(), apps: list, hits, stats: { errors, blocks, claudeCalls } }));
console.log(`${which}: DONE terms ${TERMS.length}, apps ${list.length}, deep ${cands.length}, errors ${errors}, blocks ${blocks}, claude ${claudeCalls}`);
