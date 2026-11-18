// Combine out/play.json + out/ios.json -> out/scored.csv, out/giants.csv, out/summary.md
import fs from 'node:fs';
const TERMS = JSON.parse(fs.readFileSync(new URL('./terms.json', import.meta.url)));
const load = (f) => (fs.existsSync(f) ? JSON.parse(fs.readFileSync(f)) : { apps: [], hits: [], stats: {} });
const P = load('out/play.json'), I = load('out/ios.json');
const DAY = 86400000, now = Date.now();
const VISUAL = /photo|image|video|avatar|face|cartoon|restore|colorize|eraser|background|wallpaper|sticker|collage|thumbnail|logo|caption|baby|pet|identifier|headshot|slideshow|ringtone|voice changer|scanner|presentation|resume|signature|ruler|measure|cast|remote|mirroring/i;
const clamp = (x) => Math.max(0, Math.min(1, x));
const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : null);
const age = (d) => (d ? now - new Date(d).getTime() : null);
const big = (a, key) => (key === 'play' ? (a.installs || 0) >= 100000 : (a.ratings || 0) >= 1000);

function termStore(S, term, key) {
  const byId = Object.fromEntries(S.apps.map((a) => [a.id, a]));
  const top = S.hits.filter((h) => h.term === term).sort((a, b) => a.rank - b.rank).map((h) => byId[h.id]).filter(Boolean);
  const t10 = top.slice(0, 10), t5 = top.slice(0, 5);
  const demand = key === 'play' ? t10.reduce((s, a) => s + (a.installs || 0), 0) : t10.reduce((s, a) => s + (a.ratings || 0), 0);
  const conc = top.filter((a) => (key === 'play' ? (a.installs || 0) >= 1e6 : (a.ratings || 0) >= 50000)).length;
  const rated = t5.map((a) => a.rating).filter((x) => x != null);
  const stale = t5.filter((a) => a.updated && age(a.updated) > 365 * DAY).length;
  const deep = top.filter((a) => a.deep && a.lowShare90 != null);
  const newer = (ms) => top.filter((a) => a.released && age(a.released) >= 0 && age(a.released) < ms);
  const n6 = newer(183 * DAY), n12 = newer(365 * DAY);
  const meaningful = n12.filter((a) => big(a, key));
  const growth = (a) => { const months = Math.max(1, age(a.released) / (30 * DAY)); return Math.round(((key === 'play' ? a.installs : a.ratings) || 0) / months); };
  const looking = top.filter((a) => a.lookN);
  const lookCount = looking.reduce((s, a) => s + a.lookCount, 0), lookN = looking.reduce((s, a) => s + a.lookN, 0);
  const lookQuotes = looking.flatMap((a) => (a.lookQuotes || []).map((q) => `${a.title}: "${q}"`)).slice(0, 2);
  const avgR = avg(rated), staleShare = t5.length ? stale / t5.length : 0;
  let cls = 'mixed';
  if (meaningful.length === 0 && (staleShare >= 0.4 || (avgR != null && avgR < 4.2))) cls = 'sleeping';
  else if (meaningful.length >= 3) cls = 'contested';
  return { n: top.length, demand, conc, avgRating5: avgR, staleShare, lowShare90: avg(deep.map((a) => a.lowShare90)),
    top, deep, n6: n6.length, n12: n12.length, meaningfulNew: meaningful.length,
    newApps: meaningful.sort((a, b) => growth(b) - growth(a)).slice(0, 3).map((a) => `${a.title} (rel ${String(a.released).slice(0, 10)}, ${key === 'play' ? fmtN(a.installs) + ' installs' : fmtN(a.ratings) + ' ratings'}, ${a.rating == null ? 'n/a' : Number(a.rating).toFixed(1)}*, ~${fmtN(growth(a))}/month)`),
    cls, lookCount, lookN, lookQuotes };
}
function fmtN(n) { n = n || 0; return n >= 1e9 ? (n / 1e9).toFixed(1) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(0) + 'k' : String(n); }
const fmt = fmtN;

const WI = 0.6, WP = 0.4; // iOS is the priority (paying users)
const blend = (i, p) => (i != null && p != null ? WI * i + WP * p : i ?? p);
const rows = [];
for (const term of TERMS) {
  const p = termStore(P, term, 'play'), i = termStore(I, term, 'ios');
  const demand = (s, key) => clamp(Math.log10(1 + s) / (key === 'play' ? 9.5 : 7));
  const D = blend(i.n ? demand(i.demand, 'ios') : null, p.n ? demand(p.demand, 'play') : null) ?? 0;
  const weak = (s) => (s.n ? 0.6 * clamp((4.6 - (s.avgRating5 ?? 4.6)) / 0.9) + 0.4 * (s.lowShare90 == null ? 0.3 : clamp(s.lowShare90 / 0.5)) : null);
  const W = blend(weak(i), weak(p)) ?? 0;
  const S = blend(i.n ? i.staleShare : null, p.n ? p.staleShare : null) ?? 0;
  const clsVal = (c) => (c === 'sleeping' ? 1 : c === 'mixed' ? 0.5 : 0);
  const N = blend(i.n ? clsVal(i.cls) : null, p.n ? clsVal(p.cls) : null) ?? 0.5;
  const T = VISUAL.test(term) ? 1 : 0;
  const iTop = i.top.slice(0, 5);
  const moneyApps = iTop.filter((a) => a.priceModel === 'paid' || a.priceModel === 'free+IAP' || a.grossRank);
  const M = iTop.length ? moneyApps.length / iTop.length : 0;
  const gap = Math.round(100 * (0.30 * D + 0.25 * W + 0.10 * S + 0.15 * N + 0.10 * T + 0.10 * M));
  const overall = i.cls === p.cls ? i.cls : i.cls === 'contested' || p.cls === 'contested' ? 'mixed' : (i.cls === 'sleeping' || p.cls === 'sleeping') ? 'mixed' : 'mixed';
  const themes = [...p.deep, ...i.deep].filter((a) => a.themes).sort((a, b) => (b.lowShare90 || 0) - (a.lowShare90 || 0)).slice(0, 2)
    .map((a) => `${a.title}: ${a.themes.map((t) => `${t.theme} ("${String(t.quote).slice(0, 100)}")`).join('; ')}`);
  const money = iTop.map((a) => `${a.title} [${a.priceModel || '?'}${a.iapPrices && a.iapPrices.length ? ' ' + a.iapPrices.slice(0, 4).join('/') : ''}${a.grossRank ? ', grossing #' + a.grossRank + ' ' + a.grossCat : ''}]`);
  rows.push({ term, gap_score: gap, ios_class: i.cls, play_class: p.cls,
    play_installs_top10: p.demand, ios_ratings_top10: i.demand, play_giants_1M: p.conc, ios_giants_50k: i.conc,
    avg_rating_top5_ios: i.avgRating5 == null ? '' : i.avgRating5.toFixed(2), avg_rating_top5_play: p.avgRating5 == null ? '' : p.avgRating5.toFixed(2),
    lowstar_share_90d_ios: i.lowShare90 == null ? '' : i.lowShare90.toFixed(2), lowstar_share_90d_play: p.lowShare90 == null ? '' : p.lowShare90.toFixed(2),
    stale_share_ios: i.staleShare.toFixed(2), stale_share_play: p.staleShare.toFixed(2),
    new6_ios: i.n6, new12_ios: i.n12, new12_meaningful_ios: i.meaningfulNew, new6_play: p.n6, new12_play: p.n12, new12_meaningful_play: p.meaningfulNew,
    still_looking_ios: `${i.lookCount}/${i.lookN}`, still_looking_play: `${p.lookCount}/${p.lookN}`,
    tiktok_able: T ? 'yes' : 'no', ios_money: iTop.length ? `${moneyApps.length}/${iTop.length}` : '',
    leaders: [...i.top.slice(0, 3), ...p.top.slice(0, 2)].map((a) => a.title).join(' | '),
    new_entrants: [...i.newApps.map((x) => 'iOS ' + x), ...p.newApps.map((x) => 'Play ' + x)],
    still_looking_quotes: [...i.lookQuotes, ...p.lookQuotes], ios_money_detail: money, themes, _overall: overall });
}
rows.sort((a, b) => b.gap_score - a.gap_score);
const cols = Object.keys(rows[0]).filter((c) => c !== '_overall');
const esc = (v) => `"${String(Array.isArray(v) ? v.join(' || ') : v).replace(/"/g, '""')}"`;
fs.writeFileSync('out/scored.csv', [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n'));

const giants = [...P.apps, ...I.apps].filter((a) => (a.store === 'play' ? (a.installs || 0) >= 10e6 : (a.ratings || 0) >= 50000))
  .sort((a, b) => (b.installs || (b.ratings || 0) * 100) - (a.installs || (a.ratings || 0) * 100));
const gc = ['store', 'title', 'developer', 'installs', 'ratings', 'rating', 'updated', 'released', 'priceModel', 'iapPrices', 'grossRank', 'deep', 'lowShare90', 'n30', 'n90', 'lookCount', 'lookN'];
fs.writeFileSync('out/giants.csv', [gc.join(','), ...giants.map((a) => gc.map((c) => esc(a[c] ?? '')).join(','))].join('\n'));

const d10 = (s) => (s ? String(s).slice(0, 10) : 'n/a');
const nIosMoney = I.apps.filter((a) => a.iapPrices && a.iapPrices.length).length;
let md = `# App scan 2026-11-18 (Google Play + App Store, US/en)\n\nTerms ${TERMS.length}; Play apps ${P.apps.length}; App Store apps ${I.apps.length}; Play deep ${P.apps.filter((a) => a.deep).length}; iOS deep ${I.apps.filter((a) => a.deep).length}; Claude calls ${(P.stats.claudeCalls || 0) + (I.stats.claudeCalls || 0)}; errors play ${P.stats.errors} / ios ${I.stats.errors}; blocks ${P.stats.blocks} / ${I.stats.blocks}. iOS apps with IAP prices found on the app page: ${nIosMoney}; iOS apps seen in top-grossing lists: ${I.apps.filter((a) => a.grossRank).length}.\n\n`;
md += `**How the gap score works (0-100):** 30% demand + 25% leader weakness (rating, share of 1-2 star reviews in last 90 days) + 10% staleness + 15% "sleeping" market (no meaningful new app in 12 months) + 10% TikTok-demo-able (keyword rule) + 10% iOS money signal (share of top-5 iOS apps that are paid / free+IAP / top-grossing). **iOS is weighted 60%, Google Play 40%** in every blended part, because iOS is the paying audience. App Store has no install counts: ratings count is the demand proxy. "Meaningful new app" = released in last 12 months with Play >=100k installs or iOS >=1,000 ratings. New-app growth is estimated as total ratings (or installs) divided by months since release (no history available). Classes: sleeping = no meaningful new app and leaders stale or rated <4.2; contested = 3+ meaningful new apps; else mixed. "Still looking" = reviews of the top-3 apps per term (up to 150 recent each) containing phrases such as alternative, looking for an app, switched to, doesn't work anymore, please update (count / reviews checked).\n\n## Top 15 terms by gap score\n\n`;
rows.slice(0, 15).forEach((r, n) => {
  md += `${n + 1}. **${r.term}** - gap ${r.gap_score}; iOS ${r.ios_class} / Play ${r.play_class}. Demand: iOS top10 ${fmt(r.ios_ratings_top10)} ratings, Play top10 ${fmt(r.play_installs_top10)} installs. Leaders: ${r.leaders}. Avg rating top5 iOS ${r.avg_rating_top5_ios || 'n/a'} / Play ${r.avg_rating_top5_play || 'n/a'}; 1-2 star 90d iOS ${r.lowstar_share_90d_ios || 'n/a'} / Play ${r.lowstar_share_90d_play || 'n/a'}; stale top5 iOS ${r.stale_share_ios} / Play ${r.stale_share_play}. New apps (12m, meaningful) iOS ${r.new12_meaningful_ios} / Play ${r.new12_meaningful_play}. Still-looking reviews iOS ${r.still_looking_ios} / Play ${r.still_looking_play}. iOS money ${r.ios_money || 'n/a'}. TikTok-able ${r.tiktok_able}.\n`;
  if (r.new_entrants.length) md += `   - New entrants: ${r.new_entrants.join(' ; ')}\n`;
  if (r.ios_money_detail.length) md += `   - iOS pricing: ${r.ios_money_detail.join(' ; ')}\n`;
  if (r.still_looking_quotes.length) md += `   - Still looking: ${r.still_looking_quotes.join(' // ')}\n`;
  if (r.themes.length) md += `   - Complaints: ${r.themes.join(' // ')}\n`;
});
const watch = [...P.apps, ...I.apps].filter((a) => a.watch);
md += `\n## Watch-list apps (June leads, current numbers)\n\n| store | app | installs/ratings | rating | last updated | 1-2 star 90d | reviews/30d | themes |\n|---|---|---|---|---|---|---|---|\n` + watch.map((a) => `| ${a.store} | ${a.title} | ${a.store === 'play' ? fmt(a.installs || 0) : fmt(a.ratings || 0) + ' ratings'} | ${a.rating == null ? 'n/a' : Number(a.rating).toFixed(2)} | ${d10(a.updated)} | ${a.lowShare90 == null ? 'n/a' : a.lowShare90.toFixed(2)} | ${a.n30 ?? 'n/a'} | ${(a.themes || []).map((t) => t.theme).join('; ')} |`).join('\n') + '\n';
md += `\n## Giants (Play >=10M installs or iOS >=50k ratings): ${giants.length}. Full list with last-updated dates in giants.csv\n\n## All terms\n\n| term | gap | iOS class | Play class | iOS top10 ratings | Play top10 installs | new12 iOS/Play | still looking iOS/Play | TikTok |\n|---|---|---|---|---|---|---|---|---|\n` + rows.map((r) => `| ${r.term} | ${r.gap_score} | ${r.ios_class} | ${r.play_class} | ${fmt(r.ios_ratings_top10)} | ${fmt(r.play_installs_top10)} | ${r.new12_meaningful_ios}/${r.new12_meaningful_play} | ${r.still_looking_ios} / ${r.still_looking_play} | ${r.tiktok_able} |`).join('\n') + '\n';
fs.writeFileSync('out/summary.md', md);
console.log(`scored ${rows.length} terms, giants ${giants.length}`);
