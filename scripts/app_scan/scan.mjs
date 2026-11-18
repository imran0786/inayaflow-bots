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

const LOOK = /alternative|looking for (an? )?(app|better|good|another)|switch(ed)? (to|from)|any other app|better app|used to be (good|great)|doesn'?t work anymore|no longer works?|stopped working|please update|not updated|abandoned/i;
function looking(rev) {
  const hit = rev.filter((r) => LOOK.test(String(r.text || '')));
  return { lookN: rev.length, lookCount: hit.length, lookQuotes: hit.slice(0, 2).map((r) => String(r.text).replace(/\s+/g, ' ').slice(0, 160)) };
}
let i = 0;
for (const a of cands) {
  const rev = await getReviews(a);
  Object.assign(a, { deep: true, watch: WATCH.test(a.title), reviewsFetched: rev.length, ...lowShare(rev), ...looking(rev) });
  const lows = rev.filter((r) => r.score <= 2 && r.t >= now - 120 * DAY).sort((x, y) => y.t - x.t);
  a.themes = await themes(a, lows.length >= 5 ? lows : rev.filter((r) => r.score <= 2));
  if (++i % 20 === 0) console.log(`${which}: deep ${i}/${cands.length}`);
}
// extra pass: top-3 apps of every term (not already deep) -> "still looking" phrases from recent reviews
const byId = Object.fromEntries(list.map((a) => [a.id, a]));
const extra = new Set();
for (const term of TERMS) {
  hits.filter((h) => h.term === term && h.rank <= 3).forEach((h) => { const a = byId[h.id]; if (a && !a.deep) extra.add(a.id); });
}
console.log(`${which}: extra review pass ${extra.size}`);
let ex = 0;
for (const id of extra) {
  const a = byId[id];
  const rev = (await getReviews(a)).slice(0, 150);
  Object.assign(a, { reviewsFetched: rev.length, ...looking(rev) });
  if (++ex % 50 === 0) console.log(`${which}: extra ${ex}/${extra.size}`);
}

// iOS money: price model, in-app purchase prices (best-effort from the public app page), top-grossing rank
if (which === 'ios') {
  const topIds = new Set();
  for (const term of TERMS) hits.filter((h) => h.term === term && h.rank <= 5).forEach((h) => topIds.add(h.id));
  let ok = 0, tried = 0;
  for (const id of topIds) {
    const a = byId[id]; if (!a) continue;
    tried++;
    try {
      await sleep(DELAY);
      const r = await fetch(`https://apps.apple.com/us/app/id${id}`, { headers: { 'user-agent': 'Mozilla/5.0 (research scan)' } });
      if (r.ok) {
        const html = await r.text();
        const m = html.match(/In-App Purchases[\s\S]{0,6000}/i);
        const prices = m ? [...m[0].matchAll(/\$\d+(?:\.\d{2})?/g)].map((x) => x[0]) : [];
        const names = m ? [...m[0].matchAll(/"title":"([^"]{2,60})","(?:subtitle|price)/g)].map((x) => x[1]) : [];
        a.iapPrices = [...new Set(prices)].slice(0, 12); a.iapNames = names.slice(0, 8);
        a.iap = a.iapPrices.length > 0 || /offers in-app purchases/i.test(html);
        ok++;
      } else blocks++;
    } catch (e) { errors++; }
    if (tried % 100 === 0) console.log(`ios: money pages ${tried}/${topIds.size}`);
  }
  console.log(`ios: money pages ok ${ok}/${tried}`);
  for (const a of list) a.priceModel = a.free === false || (a.price || 0) > 0 ? 'paid' : a.iap ? 'free+IAP' : 'free';
  const cats = [undefined, 'PHOTO_AND_VIDEO', 'PRODUCTIVITY', 'UTILITIES', 'HEALTH_AND_FITNESS', 'EDUCATION', 'FINANCE', 'LIFESTYLE', 'ENTERTAINMENT', 'MUSIC', 'REFERENCE', 'BUSINESS'];
  let gross = 0;
  for (const c of cats) {
    const res = await retry(() => store.list({ collection: store.collection.TOP_GROSSING_IOS, category: c ? store.category[c] : undefined, num: 200, country: 'us' }), 'grossing');
    (res || []).forEach((x, n) => { const a = byId[String(x.id)]; if (a) { a.grossRank = Math.min(a.grossRank || 999, n + 1); a.grossCat = c || 'ALL'; gross++; } });
  }
  console.log(`ios: top-grossing matches ${gross}`);
}
fs.mkdirSync('out', { recursive: true });
fs.writeFileSync(`out/${which}.json`, JSON.stringify({ store: which, scannedAt: new Date().toISOString(), apps: list, hits, stats: { errors, blocks, claudeCalls } }));
console.log(`${which}: DONE terms ${TERMS.length}, apps ${list.length}, deep ${cands.length}, errors ${errors}, blocks ${blocks}, claude ${claudeCalls}`);
