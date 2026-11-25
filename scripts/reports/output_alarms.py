"""OUTPUT ALARMS (25 Nov 2026). A robot that "ran green" but produced nothing is BROKEN. This module builds the table that goes at the very
top of the 3-hourly ops report: one row per robot, last run, output in the last 24 h in REAL units, status.

  0 output for 24 h = BROKEN (red words at the top).  0 for 48 h = BROKEN 48h+, also counted in the robot-health RED count.

Reads only the robots' own log files (private repo ops-* branches, the public data/ files). No secrets, nothing sent, nothing written
except the small snapshot file ops/output_snapshots.json on the report branch (used for the hunter, whose rows carry no date).
Public output rule: counts only (this module prints nothing itself).
Date-only log rows (platform / DEV / LIVE-link / candidate dates) count at 12:00 UTC of that day; rows with a time count at that time.
"""
import datetime as _dt
import json
import os
import re

import common as c

UTC = _dt.timezone.utc
H24, H48 = 24, 48
SNAP_FILE = "ops/output_snapshots.json"

# (row label, unit text, workflow file(s) whose last run is shown)
ROBOTS = [
    ("Outreach mails (backlink batch + outbox)", "mails queued or sent", ["backlink-batch.yml"]),
    ("Directory agent", "submissions + new LIVE links", ["directory-agent.yml"]),
    ("Platform poster", "articles published", ["platform-poster.yml"]),
    ("DEV poster", "DEV posts published", ["dev-poster.yml"]),
    ("List builder (Sonnet)", "new places added", ["list-builder.yml"]),
    ("List builder (DeepSeek)", "new places added", ["list-builder-deepseek.yml"]),
    ("Prospect hunter", "prospects added to the outreach list", ["hunter.yml"]),
]


def _d(s):
    """'2026-11-24' -> noon UTC of that day (date-only rows)."""
    try:
        return _dt.datetime.strptime(s.strip()[:10], "%Y-%m-%d").replace(hour=12, microsecond=1, tzinfo=UTC)   # microsecond=1 marks "date only"
    except Exception:
        return None


def _dt_min(s):
    try:
        return _dt.datetime.strptime(s.strip()[:16], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
    except Exception:
        return None


def _sent_at(s):
    try:
        return _dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except Exception:
        return None


def within(events, now, hours):
    lim = now - _dt.timedelta(hours=hours)
    tot = 0
    for t, n in events:
        if t is None:
            continue
        if t.microsecond == 1 and t > now and t.date() == now.date():
            t = now                                    # a date-only row of TODAY counts at once (it cannot be older than now)
        if lim <= t <= now + _dt.timedelta(minutes=5):
            tot += n
    return tot


# ---------- event extractors: lists of (time, count) ----------

def outreach_events(batch_log, sent_files):
    ev = []
    for m in re.finditer(r"^## Backlink batch (\d{4}-\d\d-\d\d \d\d:\d\d) UTC \(LIVE\)(.*?)(?=^## |\Z)", batch_log or "", re.M | re.S):
        q = re.search(r"(\d+) e-mail\(s\) queued", m.group(2))
        if q and int(q.group(1)):
            ev.append((_dt_min(m.group(1)), int(q.group(1))))
    for name, text in (sent_files or {}).items():
        base = name.rsplit("/", 1)[-1]
        if not re.search(r"outreach|swap", base, re.I):
            continue                                   # watchdog / refund / test mails are not link outreach
        try:
            ev.append((_sent_at(json.loads(text).get("sent_at", "")), 1))
        except Exception:
            continue
    return ev


def directory_events(agent_summary, live_links):
    """Submissions = the agent's own daily summary line ("successes today N", the real count of accepted submissions; the per-site log also
    carries many SUBMITTED rows that were not new submissions), taken as the highest value of each date. New LIVE = listings first seen live."""
    ev = []
    best = {}
    for line in (agent_summary or "").splitlines():
        m = re.match(r"^\|\s*(\d{4}-\d\d-\d\d) \|.*?successes today (\d+)", line)
        if m:
            best[m.group(1)] = max(best.get(m.group(1), 0), int(m.group(2)))
    for d, n in best.items():
        if n:
            ev.append((_d(d), n))
    for r in c.md_rows(live_links):
        if len(r) >= 5 and r[1].upper() == "LIVE" and _d(r[4]):
            ev.append((_d(r[4]), 1))
    return ev


def published_events(log):
    """Platform / DEV poster logs: a row counts only with a real article URL (the proof)."""
    ev = []
    for r in c.md_rows(log):
        url = next((x for x in r if x.startswith("http")), "")
        if url and not any(x.upper().startswith("FAIL") for x in r):
            ev.append((_d(r[0]), 1))
    return ev


def candidate_events(cand_text, only_found_by=None, exclude_found_by=None):
    try:
        rows = (json.loads(cand_text) or {}).get("sites", [])
    except Exception:
        return None
    ev = []
    for r in rows:
        fb = str(r.get("found_by", "")).lower()
        if only_found_by and fb != only_found_by:
            continue
        if exclude_found_by and fb == exclude_found_by:
            continue
        if r.get("added"):
            ev.append((_d(str(r["added"])), 1))
    return ev


def prospects_count(text):
    try:
        d = json.loads(text)
        return len(d) if isinstance(d, list) else None
    except Exception:
        return None


# ---------- snapshots (hunter) ----------

def snapshot_baseline(snaps, now, min_hours):
    """Newest snapshot that is at least `min_hours` old, or None."""
    ok = []
    for s in snaps:
        try:
            t = _dt.datetime.strptime(s["t"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
        except Exception:
            continue
        if now - t >= _dt.timedelta(hours=min_hours):
            ok.append((t, s))
    return max(ok, key=lambda x: x[0])[1] if ok else None


def update_snapshots(snaps, now, prospects):
    out = [s for s in snaps if isinstance(s, dict) and "t" in s]
    if prospects is not None:
        stamp = now.strftime("%Y-%m-%d %H:%M")
        if not out or out[-1]["t"][:13] != stamp[:13]:           # one snapshot per hour is plenty
            out.append({"t": stamp, "prospects": prospects})
    cut = (now - _dt.timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
    return [s for s in out if s["t"] >= cut][-200:]


# ---------- the table ----------

def _status(o24, o48):
    if o24 is None:
        return "UNKNOWN (log not readable)"
    if o24 > 0:
        return "ok"
    if o48 == 0:
        return "BROKEN 48h+ (0 output for 48 hours)"
    return "BROKEN (0 output for 24 hours)"


def build(inputs, now, ages=None, snaps=None):
    """-> (markdown, summary dict, new snapshots list). `ages` = {workflow file: minutes since newest run or None}."""
    ages = ages or {}
    snaps = snaps or []
    sent = inputs.get("outbox_sent") or {}
    ev = {
        0: outreach_events(inputs.get("batch_log", ""), sent) if (inputs.get("batch_log") or sent) else None,
        1: directory_events(inputs.get("agent_summary", ""), inputs.get("live_links", "")) if inputs.get("agent_summary") else None,
        2: published_events(inputs.get("platform_log", "")) if inputs.get("platform_log") else None,
        3: published_events(inputs.get("dev_log", "")) if inputs.get("dev_log") else None,
        4: candidate_events(inputs.get("cand_sonnet", ""), exclude_found_by="deepseek") if inputs.get("cand_sonnet") else None,
        5: candidate_events(inputs.get("cand_deepseek", "")) if inputs.get("cand_deepseek") else [],   # file absent = the twin never wrote one = 0
    }
    pnow = prospects_count(inputs.get("prospects", ""))
    new_snaps = update_snapshots(snaps, now, pnow)
    rows, broken24, broken48, unknown = [], [], [], []
    for i, (label, unit, wfs) in enumerate(ROBOTS):
        o24 = o48 = None
        note = ""
        if i == 6:
            b24, b48 = snapshot_baseline(snaps, now, 20), snapshot_baseline(snaps, now, 44)
            if pnow is not None and b24:
                o24 = max(0, pnow - b24["prospects"])
                o48 = max(0, pnow - b48["prospects"]) if b48 else o24
                if not b48 and o24 == 0:
                    o48 = 1                                           # only 24 h of history: cannot call it 48 h yet
            note = "outreach list holds %s rows" % ("?" if pnow is None else pnow)
            if pnow is not None and not b24:
                rows.append((label, ages, wfs, "not yet (first snapshot taken; the 24 h count starts now)", unit + "; " + note, "NO BASELINE YET"))
                continue
        elif ev[i] is not None:
            o24, o48 = within(ev[i], now, H24), within(ev[i], now, H48)
        st = _status(o24, o48)
        if st.startswith("BROKEN 48h"):
            broken48.append(label)
        if st.startswith("BROKEN"):
            broken24.append(label)
        if st.startswith("UNKNOWN"):
            unknown.append(label)
        rows.append((label, ages, wfs, "UNKNOWN" if o24 is None else str(o24), unit + (" (48 h: %d)" % o48 if o48 is not None else "") + ("; " + note if note else ""), st))
    L = []
    if broken24:
        L.append("## ROBOT OUTPUT ALARM: %d robot(s) BROKEN (0 output in 24 h): %s" % (len(broken24), "; ".join(broken24)))
    else:
        L.append("## ROBOT OUTPUT: every robot produced something in the last 24 h" + (" (not readable: %s)" % "; ".join(unknown) if unknown else ""))
    if broken48:
        L += ["", "ROBOT HEALTH RED: %d robot(s) with 0 output for 48 hours: %s" % (len(broken48), "; ".join(broken48))]
    L += ["", "Judged by OUTPUT, not by a green run. 'Last 24 h' = real units below. Date-only logs count at 12:00 UTC.", "",
          "| Robot | Last run | Output, last 24 h | What is counted | Status |", "|---|---|---|---|---|"]
    for label, a, wfs, out, unit, st in rows:
        m = a.get(wfs[0])
        last = "unknown" if m is None else ("never" if m == "never" else "%s ago" % _fmt(m))
        L.append("| %s | %s | %s | %s | %s |" % (label, last, out, unit, st if st == "ok" else "**%s**" % st))
    L.append("")
    return "\n".join(L), {"broken24": broken24, "broken48": broken48, "unknown": unknown}, new_snaps


def _fmt(m):
    return "%d min" % m if m < 120 else "%.1f h" % (m / 60.0)


# ---------- reading the real files ----------

def read_inputs(work=None):
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

    def local(rel):
        try:
            return open(os.path.join(root, rel), encoding="utf-8").read()
        except OSError:
            return ""
    return {
        "batch_log": c.fetch_file("ops-reports", "ops/backlink_batch_log.md", work),
        "prospects": c.fetch_file("ops-reports", "ops/outreach_prospects.json", work),
        "outbox_sent": c.read_tree("ops-outbox", ["outbox/sent"], work),
        "agent_summary": c.fetch_file("ops-directory-logs", "ops/directory_agent_summary.md", work),
        "live_links": c.fetch_file("ops-directory-logs", "ops/live_links.md", work),
        "platform_log": c.fetch_file("ops-platform-poster", "ops/platform_poster_log.md", work),
        "dev_log": c.fetch_file("ops-dev-poster", "ops/dev_poster_log.md", work),
        "cand_sonnet": local("data/directories_candidates.json"),
        "cand_deepseek": local("data/directories_candidates_deepseek.json"),
    }


def run_ages(token, now, fetch=None):
    """{workflow: minutes since the newest run that really started (None unknown, 'never')} via keepalive.py's reader."""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "keepalive"))
    import keepalive as ka
    fetch = fetch or (lambda wf: ka.fetch_runs(token, wf))
    out = {}
    for _, _, wfs in ROBOTS:
        for wf in wfs:
            try:
                a = ka.age_minutes(ka.newest_run(fetch(wf)), now)
                out[wf] = "never" if a is None else a
            except Exception:
                out[wf] = None
    return out
