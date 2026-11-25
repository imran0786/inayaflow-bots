#!/usr/bin/env python3
"""KEEP-ALIVE (23 Nov 2026): GitHub's cron is best effort and drops or delays runs (23 Nov: backlink-batch, hunter and
directory-agent did not fire for ~5 h; inbox digest skipped 7 h; report robot 4.5 h). This check looks at the newest run of
every scheduled robot and starts (workflow_dispatch) any robot that missed its time.
25 Nov 2026 (clock fix): robots that run a few times a day (every 3 h and slower) are judged by their real SLOTS (the UTC times in their
cron), not by 1.5 x interval (a daily robot could be 36 h late): a slot that passed more than SLOT_GRACE_MIN minutes ago with no run
started since it = dispatch. Fast robots (every 5-60 min) keep the interval rule.

Started by keepalive-check.yml (after ANY robot run, plus its own 20-minute cron). Uses the built-in GITHUB_TOKEN (actions: write).
GitHub docs (triggering-a-workflow): events triggered by GITHUB_TOKEN "will not create a new workflow run", except
"workflow_dispatch and repository_dispatch events always create workflow runs", so a dispatch from here does start a run.

Loop and double-run guards: never dispatches a robot that is queued / in progress / waiting; at most ONE dispatch per robot per
hour (any workflow_dispatch run created in the last 60 min counts, manual ones too); keepalive-check is not in its own trigger list.
Public output: robot file names, ages in minutes, counts only.
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "imran0786/inayaflow-bots")
API = "https://api.github.com"
FACTOR = 1.5              # dispatch when the newest run is older than FACTOR x interval
RED_FACTOR = 2.0          # report shows RED when older than RED_FACTOR x interval
MAX_DISPATCH_PER_HOUR = 1
BUSY_MAX_MIN = 180          # a run queued / in progress for longer than this is stuck: it no longer blocks a dispatch and the report shows RED
BUSY_MAX_OVERRIDE = {"heartbeat.yml": 400}   # the heartbeat job legitimately runs ~5 h 40 min (timeout 350)
SLOT_GRACE_MIN = 20         # a slot is "missed" only when it passed more than this many minutes ago (GitHub's own cron is often a few minutes late)
BUSY = ("queued", "in_progress", "waiting", "pending", "requested")

# (workflow file, cron interval in minutes, inputs sent when started by hand-dispatch). Spend robots get dry_run=false as the heartbeat does.
ROBOTS = [
    ("email-replier.yml", 5, {}),
    ("mail-router.yml", 10, {}),
    ("outbox.yml", 10, {}),
    ("reader.yml", 10, {}),
    ("job-runner.yml", 10, {}),
    ("directory-verify.yml", 30, {}),
    ("inbox-digest.yml", 60, {}),
    ("backlink-batch.yml", 60, {"dry_run": "false"}),
    ("hunter.yml", 180, {}),
    ("list-builder.yml", 720, {"dry_run": "false"}),   # its manual default is dry_run=true: a bare dispatch would write nothing
    ("list-builder-deepseek.yml", 720, {"dry_run": "false"}),   # same (its scheduled runs are live; without the DEEPSEEK_API_KEY secret the job prints "skipped" and exits 0)
    ("directory-agent.yml", 180, {"dry_run": "false"}),
    ("ops-report.yml", 180, {}),
    ("heartbeat.yml", 180, {}),
    ("funnel-daily.yml", 1440, {}),
    ("link-checker.yml", 1440, {}),
    ("platform-poster.yml", 1440, {"dry_run": "false"}),   # publishes only when the repo variable PLATFORM_POSTER_LIVE is "true", so dry_run=false is safe
    ("dev-poster.yml", 1440, {"dry_run": "false"}),       # same: its workflow turns dry_run=false into a dry run unless the repo variable DEV_POSTER_LIVE is "true"
]


# Real SLOTS (UTC "HH:MM", copied from each workflow's cron; a test checks they match). Robots listed here are judged by slots.
SLOTS = {
    "hunter.yml": ["%02d:17" % h for h in range(0, 24, 3)],
    "list-builder.yml": ["04:23", "16:23"],
    "list-builder-deepseek.yml": ["04:53", "16:53"],
    "directory-agent.yml": ["%02d:37" % h for h in range(0, 24, 3)],
    "ops-report.yml": ["%02d:23" % h for h in range(0, 24, 3)],
    "heartbeat.yml": ["%02d:11" % h for h in range(0, 24, 3)],
    "funnel-daily.yml": ["07:30"],
    "link-checker.yml": ["07:53"],
    "platform-poster.yml": ["09:41"],
    "dev-poster.yml": ["13:23"],
}


def parse_time(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


def newest_run(runs):
    """Newest run that really started (a cancelled run before it began does not count as the robot running)."""
    ok = [r for r in runs if r.get("conclusion") not in ("skipped", "cancelled")]
    return max(ok, key=lambda r: r["created_at"]) if ok else None


def last_success(runs):
    ok = [r for r in runs if r.get("conclusion") == "success"]
    return max(ok, key=lambda r: r["created_at"]) if ok else None


def age_minutes(run, now):
    return None if run is None else int((now - parse_time(run["created_at"])).total_seconds() // 60)


def stuck_busy(wf, runs, now):
    """True when a run has been queued / in progress for longer than its limit (stuck: waiting for approval, runner lost ...)."""
    lim = BUSY_MAX_OVERRIDE.get(wf, BUSY_MAX_MIN)
    return any(r.get("status") in BUSY and (now - parse_time(r["created_at"])).total_seconds() > lim * 60 for r in runs)


def last_slot(slots, now):
    """Newest slot time (UTC datetime) that is not in the future, today or yesterday."""
    best = None
    for day in (now.date() - datetime.timedelta(days=1), now.date()):
        for hm in slots:
            h, m = hm.split(":")
            t = datetime.datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=datetime.timezone.utc)
            if t <= now and (best is None or t > best):
                best = t
    return best


def decide(interval, runs, now, wf=""):
    """-> (action, reason). action is 'dispatch' or 'skip'. A busy run older than its limit is ignored (stuck); a fresh one blocks.
    Robots with SLOTS: dispatch when the newest slot passed more than SLOT_GRACE_MIN minutes ago and no run started since it."""
    lim = BUSY_MAX_OVERRIDE.get(wf, BUSY_MAX_MIN)
    for r in runs:
        if r.get("status") in BUSY and (now - parse_time(r["created_at"])).total_seconds() <= lim * 60:
            return "skip", "already queued or running"
    recent = 0
    for r in runs:
        if r.get("event") == "workflow_dispatch" and (now - parse_time(r["created_at"])).total_seconds() < 3600:
            recent += 1
    if recent >= MAX_DISPATCH_PER_HOUR:
        return "skip", "dispatched less than 1 h ago"
    nr = newest_run(runs)
    if wf in SLOTS:
        slot = last_slot(SLOTS[wf], now)
        if slot is None or (now - slot).total_seconds() <= SLOT_GRACE_MIN * 60:
            return "skip", "no slot is overdue"
        if nr is None or parse_time(nr["created_at"]) < slot:
            return "dispatch", "slot %s UTC passed %d min ago, no run since" % (slot.strftime("%H:%M"), int((now - slot).total_seconds() // 60))
        return "skip", "slot %s UTC was served" % slot.strftime("%H:%M")
    age = age_minutes(nr, now)
    if age is None:
        return "dispatch", "never ran"
    if age > FACTOR * interval:
        return "dispatch", "last run %d min ago (limit %d)" % (age, FACTOR * interval)
    return "skip", "on time (%d min)" % age


def flag(interval, runs, now, wf=""):
    nr = newest_run(runs)
    age = age_minutes(nr, now)
    if stuck_busy(wf, runs, now):
        return "RED (stuck)"
    if wf in SLOTS:
        if any(r.get("status") in BUSY for r in runs):      # a fresh busy run (the heartbeat legitimately runs 5h40m) is a run in progress: ok (a stuck one returned RED above)
            return "ok"
        slot = last_slot(SLOTS[wf], now)
        late = slot is not None and (now - slot).total_seconds() > 2 * SLOT_GRACE_MIN * 60 and (nr is None or parse_time(nr["created_at"]) < slot)
        return "RED (missed slot)" if late else "ok"
    if age is None or age > RED_FACTOR * interval:
        return "RED"
    return "ok"


def api(method, path, token, body=None, tries=3, sleep=time.sleep):
    """One GitHub API call; a transient 5xx / network error is retried (the heartbeat died on a single HTTP 500 dispatch on 23, 24 and 25 Nov)."""
    data = json.dumps(body).encode() if body is not None else None
    for i in range(tries):
        req = urllib.request.Request(API + path, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "inayaflow-keepalive", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code < 500 or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
        sleep(3 * (i + 1))


def fetch_runs(token, wf, repo=REPO):
    return api("GET", "/repos/%s/actions/workflows/%s/runs?per_page=30" % (repo, wf), token).get("workflow_runs", [])


def dispatch(token, wf, inputs, repo=REPO):
    body = {"ref": "main"}
    if inputs:
        body["inputs"] = inputs
    api("POST", "/repos/%s/actions/workflows/%s/dispatches" % (repo, wf), token, body)


def age_section(token, now=None, fetch=None, robots=ROBOTS):
    """Markdown lines for the 3-hourly report: one line per robot, RED when older than 2 x interval. Never raises."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    fetch = fetch or (lambda wf: fetch_runs(token, wf))
    L = ["", "## Robot last-run age (RED = older than 2 x its interval)", "", "| Robot | Every | Last run | Last success | State |", "|---|---|---|---|---|"]
    reds = 0
    for wf, interval, _ in robots:
        try:
            runs = fetch(wf)
        except Exception as e:
            L.append("| %s | %d min | ? | ? | UNKNOWN (%s) |" % (wf[:-4], interval, type(e).__name__))
            continue
        a, s = age_minutes(newest_run(runs), now), age_minutes(last_success(runs), now)
        f = flag(interval, runs, now, wf)
        reds += f.startswith("RED")
        L.append("| %s | %d min | %s | %s | %s |" % (wf[:-4], interval, "never" if a is None else "%d min ago" % a, "never" if s is None else "%d min ago" % s, f))
    L.insert(1, "RED robots: %d" % reds if reds else "All robots on time.")
    return "\n".join(L) + "\n", reds


def main(now=None):
    token = os.environ.get("GITHUB_TOKEN", "")
    dry = os.environ.get("KEEPALIVE_DRY", "false").strip().lower() == "true"
    if not token:
        print("keepalive: no token")
        return 1
    now = now or datetime.datetime.now(datetime.timezone.utc)
    started = errors = 0
    for wf, interval, inputs in ROBOTS:
        try:
            runs = fetch_runs(token, wf)
            action, why = decide(interval, runs, now, wf)
            if action == "dispatch" and not dry:
                dispatch(token, wf, inputs)
                started += 1
            print("keepalive: %s %s (%s)%s" % (wf, action, why, " [dry]" if dry and action == "dispatch" else ""))
        except Exception as e:
            errors += 1
            print("keepalive: %s ERROR %s" % (wf, type(e).__name__))
    print("keepalive: started %d errors %d" % (started, errors))
    return 1 if errors == len(ROBOTS) else 0     # every robot failed (bad token, rate limit): make the check itself go red


if __name__ == "__main__":
    sys.exit(main())
