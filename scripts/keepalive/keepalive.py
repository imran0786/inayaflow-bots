#!/usr/bin/env python3
"""KEEP-ALIVE (23 Nov 2026): GitHub's cron is best effort and drops or delays runs (23 Nov: backlink-batch, hunter and
directory-agent did not fire for ~5 h; inbox digest skipped 7 h; report robot 4.5 h). This check looks at the newest run of
every scheduled robot and starts (workflow_dispatch) any robot whose newest run is older than 1.5 x its interval.

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
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "imran0786/inayaflow-bots")
API = "https://api.github.com"
FACTOR = 1.5              # dispatch when the newest run is older than FACTOR x interval
RED_FACTOR = 2.0          # report shows RED when older than RED_FACTOR x interval
MAX_DISPATCH_PER_HOUR = 1
BUSY_MAX_MIN = 180          # a run queued / in progress for longer than this is stuck: it no longer blocks a dispatch and the report shows RED
BUSY_MAX_OVERRIDE = {"heartbeat.yml": 400}   # the heartbeat job legitimately runs ~5 h 40 min (timeout 350)
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
    ("directory-agent.yml", 180, {"dry_run": "false"}),
    ("ops-report.yml", 180, {}),
    ("heartbeat.yml", 180, {}),
    ("funnel-daily.yml", 1440, {}),
    ("link-checker.yml", 1440, {}),
    ("platform-poster.yml", 1440, {"dry_run": "false"}),   # publishes only when the repo variable PLATFORM_POSTER_LIVE is "true", so dry_run=false is safe
]


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


def decide(interval, runs, now, wf=""):
    """-> (action, reason). action is 'dispatch' or 'skip'. A busy run older than its limit is ignored (stuck); a fresh one blocks."""
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
    age = age_minutes(newest_run(runs), now)
    if age is None:
        return "dispatch", "never ran"
    if age > FACTOR * interval:
        return "dispatch", "last run %d min ago (limit %d)" % (age, FACTOR * interval)
    return "skip", "on time (%d min)" % age


def flag(interval, runs, now, wf=""):
    age = age_minutes(newest_run(runs), now)
    if stuck_busy(wf, runs, now):
        return "RED (stuck)"
    if age is None or age > RED_FACTOR * interval:
        return "RED"
    return "ok"


def api(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "inayaflow-keepalive", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


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


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    dry = os.environ.get("KEEPALIVE_DRY", "false").strip().lower() == "true"
    if not token:
        print("keepalive: no token")
        return 1
    now = datetime.datetime.now(datetime.timezone.utc)
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
