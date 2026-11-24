"""Backlink targets table for the daily report (22 Nov 2026, Imran: every page gets backlinks every day).

Target: 1 NEW live backlink per day for each of the 5 pages; per week 7 in total, of which at least MIN_FOLLOWED followed
(homepage 5, the others 4). Numbers come only from files the robots already write; where a number cannot be computed it says
"not tracked yet" (never a guess).

  - live links: ops/live_links.md (link checker). Column 7 "our page" says which of our pages the link points to
    (written since 22 Nov 2026; older rows have no page, so they are counted under 'page unknown', never guessed).
  - outreach mails queued today: ops/backlink_batch_state.json (mails are not links; shown as a lead indicator only).
  - directory submissions today: ops/directory_agent_state.json (SUBMITTED is not live; shown as a lead indicator only).
"""
import datetime as dt
import json
from urllib.parse import urlparse

import common as c

PAGES = [  # (label, path on inayaflow.ai, listing id in data/listings.json, minimum FOLLOWED per week)
    ("Homepage", "/", "homepage", 5),
    ("Hashtag strategy guide", "/blog/tiktok-hashtag-strategy", None, 4),
    ("Follower count checker", "/tools/tiktok-follower-count-checker", "follower-count-checker", 4),
    ("Money calculator", "/tools/tiktok-money-calculator", "money-calculator", 4),
    ("Shadowban checker", "/tools/tiktok-shadowban-checker", "shadowban-checker", 4),
    ("Hashtag generator", "/tools/tiktok-hashtag-generator", "hashtag-generator", 4),
    ("Hashtag counter", "/tools/tiktok-hashtag-counter", "hashtag-counter", 4),
    ("Engagement rate calculator", "/tools/tiktok-engagement-rate-calculator", "engagement-rate-calculator", 4),
    ("Best time to post", "/tools/best-time-to-post-on-tiktok-today", "best-time-to-post", 4),
    ("Coins to USD", "/tools/tiktok-coins-to-usd", "coins-to-usd", 4),
    ("Username generator", "/tools/tiktok-username-generator", "username-generator", 4),
    ("No views guide", "/blog/why-is-my-tiktok-getting-no-views", None, 4),
    ("Shadowban guide", "/blog/tiktok-shadowban", None, 4),
    ("Account analyzer", "/analyze", None, 4),
    ("How many views is good guide", "/blog/how-many-views-is-good-on-tiktok", None, 4),
    ("Hooks guide", "/blog/tiktok-hooks-that-stop-the-scroll", None, 4),
    ("Captions guide", "/blog/tiktok-captions", None, 4),
]

# QUICK-WIN pages: close to Google page 1-2 now (Search Console 28 days), so they get TWICE the slots of other pages in the hunter rotation, the
# DEV poster and the outreach rotation, and are shown first in the daily report. ONE list, imported by hunter.py, poster.py and backlink_batch.py.
# Director updates weekly from Search Console; last 24 Nov 2026 (impressions, average position: hashtag-strategy 858 / 11.8, no-views 141 / 7.0,
# shadowban guide 65 / 8.4, /analyze 96 / 17.0, how-many-views 93 / 18.2, hooks 75 / 17.4, captions 68 / 16.1).
QUICK_WIN_PATHS = (
    "/blog/tiktok-hashtag-strategy",
    "/blog/why-is-my-tiktok-getting-no-views",
    "/blog/tiktok-shadowban",
    "/analyze",
    "/blog/how-many-views-is-good-on-tiktok",
    "/blog/tiktok-hooks-that-stop-the-scroll",
    "/blog/tiktok-captions",
)
QUICK_WIN_WEIGHT = 2        # a quick-win page gets twice the slots (tools and the homepage keep theirs)


def is_quick_win(path_or_url):
    """True for a quick-win page given its path ("/analyze") or full URL; query and trailing slash are ignored."""
    p = urlparse(str(path_or_url)).path or "/"
    p = "/" if p.strip("/") == "" else "/" + p.strip("/")
    return p in QUICK_WIN_PATHS
DAY_TARGET = 1
WEEK_TARGET = 7


def _date(s):
    try:
        return dt.date.fromisoformat(str(s).strip()[:10])
    except Exception:
        return None


def live_by_page(live_rows, today):
    """({path: {"today": [followed, nofollow], "week": [followed, nofollow]}}, n_unknown_today, n_unknown_week, tracked)
    'week' = Monday to today. tracked = at least one LIVE row carries a page."""
    monday = today - dt.timedelta(days=today.weekday())
    out = {p[1]: {"today": [0, 0], "week": [0, 0]} for p in PAGES}
    unk_today = unk_week = 0
    tracked = False
    for r in live_rows:
        if len(r) < 5 or r[1].strip().upper() != "LIVE" or r[2].strip().lower() not in ("followed", "nofollow", "ugc", "sponsored"):
            continue
        page = r[6].strip() if len(r) > 6 else ""
        if page:
            tracked = True
        first = _date(r[4])
        if first is None or first < monday or first > today:
            continue
        i = 0 if r[2].strip().lower() == "followed" else 1
        if page in out:
            out[page]["week"][i] += 1
            if first == today:
                out[page]["today"][i] += 1
        elif not page:
            unk_week += 1
            unk_today += first == today
    return out, unk_today, unk_week, tracked


def outreach_today(batch_state, today_s):
    """{path: first mails queued today} from the batch state's recorded match key."""
    try:
        import backlink_batch as bb
    except Exception:
        return {}
    out = {}
    for u, n in bb.sent_today_by_page(batch_state or {}, today_s).items():
        path = urlparse(u).path or "/"
        path = "/" if path.strip("/") == "" else "/" + path.strip("/")
        out[path] = out.get(path, 0) + n
    return out


def directory_today(dir_state, today_s):
    """{listing id: submissions today} from the directory agent state (homepage = the site entries)."""
    out, seen = {}, set()
    for k, e in (dir_state or {}).items():
        if not isinstance(e, dict) or e.get("name") in seen:
            continue
        seen.add(e.get("name"))
        if e.get("status") == "SUBMITTED" and e.get("date") == today_s:
            out["homepage"] = out.get("homepage", 0) + 1
        for pid, pr in (e.get("products") or {}).items():
            if pr.get("status") == "SUBMITTED" and pr.get("date") == today_s:
                out[pid] = out.get(pid, 0) + 1
    return out


def build_targets(live_rows, batch_state, dir_state, today=None):
    """Markdown section 'Backlink targets today'."""
    today = today or c.now().date()
    today_s = today.isoformat()
    dayno = today.weekday() + 1          # Monday = 1 ... Sunday = 7
    live, unk_today, unk_week, tracked = live_by_page(live_rows, today)
    mails = outreach_today(batch_state, today_s)
    dirs = directory_today(dir_state, today_s)
    L = ["## Backlink targets today", "",
         "Target: %d new LIVE backlink per page per day; %d a week per page, at least 5 followed for the homepage and 4 followed for each other page. "
         "RED = behind that pace. LIVE is counted when the link checker first sees the link on the site (rel read from the page code). "
         "Mails and directory submissions are lead indicators, not links." % (DAY_TARGET, WEEK_TARGET), "",
         "Quick wins first: pages already close to Google page 1-2 (Search Console, Director updates weekly, last 24 Nov 2026) get twice the slots in every robot.", ""]
    head = ["| page | target/day | new LIVE today (followed / nofollow) | this week (followed / nofollow) vs %d | min followed (week) | mails queued today | directory submissions today | status |" % WEEK_TARGET,
            "|---|---|---|---|---|---|---|---|"]
    ordered = [p for p in PAGES if p[1] in QUICK_WIN_PATHS] + [p for p in PAGES if p[1] not in QUICK_WIN_PATHS]
    for label, path, pid, minf in ordered:
        if path == "/blog/tiktok-hashtag-strategy":
            L += ["### Quick wins (page 1-2 now)", ""] + head
        elif path == "/":
            L += ["", "### Other targets (homepage and tools)", ""] + head
        t, w = live[path]["today"], live[path]["week"]
        mail_n = mails.get(path, 0)
        dir_n = dirs.get(pid, 0) if pid else "n/a (a guide, not listed)"
        if tracked:
            tot_t, tot_w = sum(t), sum(w)
            behind = tot_w < dayno * DAY_TARGET or w[0] < minf * dayno / 7.0
            status = "RED: behind (%d of %d so far this week, %d followed)" % (tot_w, dayno, w[0]) if behind else "on pace"
            if tot_t == 0 and not behind:
                status = "on pace (none new today yet)"
            cells = ["%d / %d" % tuple(t), "%d / %d, total %d" % (w[0], w[1], tot_w)]
        else:
            status = "not tracked yet (link checker has not yet recorded which page each link points to)"
            cells = ["not tracked yet", "not tracked yet"]
        L.append("| %s (%s) | %d | %s | %s | %d | %d | %s | %s |" % (label, path, DAY_TARGET, cells[0], cells[1], minf, mail_n, dir_n, status))
    L += ["", "Live links first seen without a recorded page (older checker rows): today %d, this week %d. They are not counted for any page." % (unk_today, unk_week), ""]
    return "\n".join(L) + "\n"


def parse_json(text):
    try:
        return json.loads(text) if text else {}
    except Exception:
        return {}
