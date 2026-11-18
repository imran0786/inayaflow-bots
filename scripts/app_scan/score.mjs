// Combine out/play.json + out/ios.json -> out/scored.csv, out/giants.csv, out/summary.md
import fs from 'node:fs';
const TERMS = JSON.parse(fs.readFileSync(new URL('./terms.json', import.meta.url)));
const load = (f) => (fs.existsSync(f) ? JSON.parse(fs.readFileSync(f)) : { apps: [], hits: [], stats: {} });
const P = load('out/play.json'), I = load('out/ios.json');
const DAY = 86400000, now = Date.now();
const VISUAL = /photo|image|video|avatar|face|cartoon|restore|colorize|eraser|background|wallpaper|sticker|collage|thumbnail|logo|caption|baby|pet|identifier|headshot|slideshow|ringtone|voice changer|scanner|presentation|resume|signature|ruler|measure|cast|remote|mirroring/i;
const clamp = (x) => Math.max(0, Math.min(1, x));
const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : null);
function termStore(S, term, key) {
  const byId = Object.fromEntries(S.apps.map((a) => [a.id, a]));
  const top = S.hits.filter((h) => h.term === term).sort((a, b) => a.rank - b.rank).map((h) => byId[h.id]).filter(Boolean);
  const t10 = top.slice(0, 10), t5 = top.slice(0, 5);
  const demand = key === 'play' ? t10.reduce((s, a) => s + (a.installs || 0), 0) : t10.reduce((s, a) => s + (a.ratings || 0), 0);
  const conc = top.filter((a) => (key === 'play' ? (a.installs || 0) >= 1e6 : (a.ratings || 0) >= 50000)).length;
  const rated = t5.map((a) => a.rating).filter((x) => x != null);
  const stale = t5.filter((a) => a.updated && now - new Date(a.updated).getTime() > 365 * DAY).length;
  const deep = top.filter((a) => a.deep && a.lowShare90 != null);
  const fresh = top.some((a) => a.released && now - new Date(a.released).getTime() < 365 * DAY && (key === 'play' ? (a.installs || 0) >= 100000 : (a.ratings || 0) >= 2000));
  return { n: top.length, demand, conc, avgRating5: avg(rated), staleShare: t5.length ? stale / t5.length : 0,
    lowShare90: avg(deep.map((a) => a.lowShare90)), fresh, top, deep };
}
const rows = [];
for (const term of TERMS) {
  const p = termStore(P, term, 'play'), i = termStore(I, term, 'ios');
  const D = clamp(Math.log10(1 + p.demand + i.demand * 20) / 9.5);
  const rating5 = avg([p.avgRating5, i.avgRating5].filter((x) => x != null));
  const ls = [p.lowShare90, i.lowShare90].filter((x) => x != null);
  const low = ls.length ? avg(ls) : null;
  const W = clamp(0.6 * clamp((4.6 - (rating5 ?? 4.6)) / 0.9) + 0.4 * (low == null ? 0.3 : clamp(low / 0.5)));
  const S = avg([p.staleShare, i.staleShare]);
  const F = p.fresh || i.fresh ? 1 : 0;
  const T = VISUAL.test(term) ? 1 : 0;
  const gap = Math.round(100 * (0.35 * D + 0.30 * W + 0.15 * S + 0.10 * (1 - F) + 0.10 * T));
  const themes = [...p.deep, ...i.deep].filter((a) => a.themes).sort((a, b) => (b.lowShare90 || 0) - (a.lowShare90 || 0)).slice(0, 2)
    .map((a) => `${a.title}: ${a.themes.map((t) => `${t.theme} ("${String(t.quote).slice(0, 100)}")`).join('; ')}`);
  rows.push({ term, play_installs_top10: p.demand, ios_ratings_top10: i.demand, play_giants_1M: p.conc, ios_giants_50k: i.conc,
    avg_rating_top5: rating5 == null ? '' : rating5.toFixed(2), lowstar_share_90d: low == null ? '' : low.toFixed(2),
    stale_share: S.toFixed(2), fresh_competitor: F, tiktok_able: T ? 'yes' : 'no', gap_score: gap,
    leaders: [...p.top.slice(0, 3), ...i.top.slice(0, 2)].map((a) => a.title).join(' | '), themes });
}
rows.sort((a, b) => b.gap_score - a.gap_score);
const cols = Object.keys(rows[0]);
const esc = (v) => `"${String(Array.isArray(v) ? v.join(' || ') : v).replace(/"/g, '""')}"`;
fs.writeFileSync('out/scored.csv', [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n'));

// giants list with current last-updated dates
const giants = [...P.apps, ...I.apps].filter((a) => a.store === 'play' ? (a.installs || 0) >= 10e6 : (a.ratings || 0) >= 50000)
  .sort((a, b) => (b.installs || b.ratings * 100 || 0) - (a.installs || a.ratings * 100 || 0));
const gc = ['store', 'title', 'developer', 'installs', 'ratings', 'rating', 'updated', 'released', 'deep', 'lowShare90', 'n30', 'n90'];
fs.writeFileSync('out/giants.csv', [gc.join(','), ...giants.map((a) => gc.map((c) => esc(a[c] ?? '')).join(','))].join('\n'));

const fmt = (n) => (n >= 1e9 ? (n / 1e9).toFixed(1) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? (n / 1e3).toFixed(0) + 'k' : String(n));
const d10 = (s) => (s ? String(s).slice(0, 10) : 'n/a');
let md = `# App scan 2026-11-18 (Google Play + App Store, US/en)\n\nTerms ${TERMS.length}; Play apps ${P.apps.length}; App Store apps ${I.apps.length}; Play deep ${P.apps.filter((a) => a.deep).length}; iOS deep ${I.apps.filter((a) => a.deep).length}; Claude calls ${(P.stats.claudeCalls || 0) + (I.stats.claudeCalls || 0)}; errors play ${P.stats.errors} / ios ${I.stats.errors}; blocks ${P.stats.blocks} / ${I.stats.blocks}.\n\nGap score 0-100 = 35% demand + 30% leader weakness (rating, 1-2 star share last 90d) + 15% staleness + 10% no fresh competitor + 10% TikTok-demo-able (keyword rule). App Store has no install counts: ratings used as proxy.\n\n## Top 15 terms by gap score\n\n`;
rows.slice(0, 15).forEach((r, n) => {
  md += `${n + 1}. **${r.term}** - gap ${r.gap_score}. Demand: Play top10 ${fmt(r.play_installs_top10)} installs, iOS top10 ${fmt(r.ios_ratings_top10)} ratings. Leaders: ${r.leaders}. Avg rating top5 ${r.avg_rating_top5 || 'n/a'}, 1-2 star share 90d ${r.lowstar_share_90d || 'n/a'}, stale ${r.stale_share}, fresh competitor ${r.fresh_competitor ? 'yes' : 'no'}, TikTok-able ${r.tiktok_able}, giants (Play>=1M ${r.play_giants_1M}, iOS>=50k ratings ${r.ios_giants_50k}).\n`;
  if (r.themes.length) md += `   - Complaints: ${r.themes.join(' // ')}\n`;
});
const watch = [...P.apps, ...I.apps].filter((a) => a.watch);
md += `\n## Watch-list apps (June leads, current numbers)\n\n| store | app | installs/ratings | rating | last updated | 1-2 star 90d | reviews/30d | themes |\n|---|---|---|---|---|---|---|---|\n` + watch.map((a) => `| ${a.store} | ${a.title} | ${a.store === 'play' ? fmt(a.installs || 0) : fmt(a.ratings || 0) + ' ratings'} | ${a.rating == null ? 'n/a' : Number(a.rating).toFixed(2)} | ${d10(a.updated)} | ${a.lowShare90 == null ? 'n/a' : a.lowShare90.toFixed(2)} | ${a.n30 ?? 'n/a'} | ${(a.themes || []).map((t) => t.theme).join('; ')} |`).join('\n') + '\n';
md += `\n## Giants (Play >=10M installs or iOS >=50k ratings): ${giants.length}. Full list with last-updated dates in giants.csv\n\n## All terms\n\n| term | gap | Play top10 installs | iOS top10 ratings | avg rating top5 | 1-2 star 90d | TikTok |\n|---|---|---|---|---|---|---|\n` + rows.map((r) => `| ${r.term} | ${r.gap_score} | ${fmt(r.play_installs_top10)} | ${fmt(r.ios_ratings_top10)} | ${r.avg_rating_top5} | ${r.lowstar_share_90d} | ${r.tiktok_able} |`).join('\n') + '\n';
fs.writeFileSync('out/summary.md', md);
console.log(`scored ${rows.length} terms, giants ${giants.length}`);
