#!/usr/bin/env python3
"""Daily funnel numbers (once a day). READ-ONLY: one call to a database function that returns COUNTS only.

It does NOT use the service-role key. It calls the function ops_funnel_daily(p_token, p_days) with the site's
public (anon) key plus a read token; the function checks the token and returns daily totals (sign-ups, sign-up
sources, paying accounts, ChatGPT plugin calls). No names, e-mails or user ids ever leave the database.
Needs (GitHub Actions): SUPABASE_URL, SUPABASE_ANON_KEY (secret or variable; both are public values) and the
secret FUNNEL_READ_TOKEN. Missing = the report file says "NOT SET UP" and the run ends OK.
Writes ops/funnel_daily.md (+ ops/funnel_history.json) on the PRIVATE branch ops-reports. No AI call.
"""
import json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c

OUT = "ops/funnel_daily.md"
HIST = "ops/funnel_history.json"
DAYS = int(os.environ.get("FUNNEL_DAYS", "8"))


def rpc(url, anon, token, days, opener=urllib.request.urlopen):
    req = urllib.request.Request(url.rstrip("/") + "/rest/v1/rpc/ops_funnel_daily",
                                 data=json.dumps({"p_token": token, "p_days": days}).encode(),
                                 headers={"apikey": anon, "Authorization": "Bearer " + anon, "content-type": "application/json"})
    with opener(req, timeout=40) as r:
        return json.load(r)


def kv(d, top=6):
    items = sorted((d or {}).items(), key=lambda x: (-int(x[1] or 0), x[0]))
    return ", ".join("%s %s" % (k, v) for k, v in items[:top]) or "-"


def new_paying(data, hist):
    """Paying-account change against the last stored total (the table has no 'paid on' date)."""
    days = sorted(k for k in hist if k < data.get("today", ""))
    if not days:
        return None, None
    prev = hist[days[-1]].get("paying_total")
    if prev is None:
        return None, days[-1]
    return int(data.get("paying_total", 0)) - int(prev), days[-1]


ALERT_RATIO = 0.5      # alert when today's same-hours calls are below 50% of the previous days' average
ALERT_MIN_AVG = 5      # ...and that average is at least 5 (no alarm on tiny numbers)


def same_hours_rows(data):
    """[(day, same-hours calls, same-hours callers, new callers, returning callers)] newest first, or None when the
    database function has no same_hours numbers yet (SQL v4 not run). Real ChatGPT callers only (caller_kind subject)."""
    out = []
    for d in data.get("days", []):
        sh = (d.get("plugin") or {}).get("same_hours")
        if not isinstance(sh, dict):
            return None
        out.append((d.get("day"), int(sh.get("calls", 0) or 0), int(sh.get("callers", 0) or 0),
                    int(sh.get("new_callers", 0) or 0), int(sh.get("returning_callers", 0) or 0)))
    return out or None


def same_hours_check(rows):
    """Compare today (first row) with the average of the previous days that have data (days before the first
    day with any plugin call are skipped: no data there is not a quiet day). -> (today_calls, avg or None, n_prev, alert bool)."""
    if not rows:
        return 0, None, 0, False
    today_calls, prev = rows[0][1], rows[1:]
    # drop leading (oldest) days with no calls at all: the log did not exist yet
    while prev and prev[-1][1] == 0:
        prev = prev[:-1]
    prev = prev[:7]
    if not prev:
        return today_calls, None, 0, False
    avg = sum(r[1] for r in prev) / float(len(prev))
    return today_calls, avg, len(prev), (avg >= ALERT_MIN_AVG and today_calls < ALERT_RATIO * avg)


def alert_line(data):
    rows = same_hours_rows(data)
    today_calls, avg, n, alert = same_hours_check(rows)
    if not alert:
        return ""
    return ("**ALERT: ChatGPT plugin calls so far today (00:00 to %s UTC) = %d, below half of the usual %.1f for the same hours "
            "(average of the previous %d days).**" % (run_time(data), today_calls, avg, n))


def run_time(data):
    return str(data.get("now_utc") or c.now().strftime("%H:%M"))


def build(data, hist):
    days = data.get("days", [])
    L = ["# Daily funnel numbers", "",
         "Made %s UTC by the funnel robot (read-only, counts only). Days are UTC; today is not finished yet." % c.now().strftime("%Y-%m-%d %H:%M"), ""]
    al = alert_line(data)
    if al:
        L += [al, ""]
    L += ["## Sign-ups", "",
         "| day | sign-ups | with a source record | by source bucket | came from (funnel_from) |", "|---|---|---|---|---|"]
    for d in days:
        L.append("| %s | %s | %s | %s | %s |" % (d.get("day"), d.get("signups", 0), d.get("with_source", 0), kv(d.get("buckets")), kv(d.get("from"))))
    diff, since = new_paying(data, hist)
    L += ["", "## Visitors by source (every visit, not only sign-ups)", "",
          "Live since 20 Nov 2026 22:35 UTC (table visit_sources). One visit = the first page of a browser tab session. "
          "Our own test visit of 20 Nov (utm_source director-check) is in that day's number.", "",
          "| day | visits | by source bucket | AI visits from (top 10) |", "|---|---|---|---|"]
    for d in days:
        v = d.get("visits")
        if not isinstance(v, dict):
            L.append("| %s | not counted yet | - | - |" % d.get("day"))
            continue
        L.append("| %s | %s | %s | %s |" % (d.get("day"), v.get("total", 0), kv(v.get("by_bucket"), 8), kv(v.get("ai_from"), 10)))
    L += ["", "## Paying accounts", "",
          "- Paying accounts now: %s (%s)." % (data.get("paying_total", 0), kv(data.get("paying_by_tier"))),
          "- New paying accounts: %s." % ("not known yet (first run, tomorrow's run compares with today)" if diff is None else "%+d since the %s report" % (diff, since)),
          "", "## Free reports on /analyze (public page)", "",
          "Counted from public_analysis_budget (started) and ai_calls (finished). These reports are NOT in channel_analysis_runs.", "",
          "| day | started | finished | started but not finished |", "|---|---|---|---|"]
    for d in days:
        a = d.get("analyze") or {}
        st, fi = int(a.get("started", 0) or 0), int(a.get("finished", 0) or 0)
        L.append("| %s | %s | %s | %s |" % (d.get("day"), st, fi, max(0, st - fi)))
    L += ["", "## ChatGPT plugin (production, real ChatGPT callers only)", "",
          "Calls from outside ChatGPT (scripts, scanners) are in their own column and not in the other numbers.", "",
          "| day | calls | answered ok | refused (logged as invalid_input) | real callers | real callers with NO answer | different accounts | outside calls | by tool | by outcome |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for d in days:
        p = d.get("plugin") or {}
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            d.get("day"), p.get("calls", 0), p.get("ok", 0), (p.get("by_outcome") or {}).get("invalid_input", 0), p.get("callers", 0),
            p.get("no_answer_callers", 0), p.get("handles", 0), p.get("outside_calls", 0), kv(p.get("by_tool"), 8), kv(p.get("by_outcome"), 8)))

    L += ["", "### ChatGPT plugin: same hours and new vs returning callers (real ChatGPT callers only)", ""]
    rows = same_hours_rows(data)
    if rows is None:
        L += ["Not available yet: the database function has no same-hours numbers until the SQL update "
              "(ROBOTS_FUNNEL_READER_v4_same_hours.sql) is run.", ""]
    else:
        t_calls, avg, n, alert = same_hours_check(rows)
        L += ["Same hours = 00:00 UTC to %s UTC (this report's time) on every day, so a quiet night is compared with quiet nights. "
              "New caller = first call ever on that day; returning = seen on an earlier day. New/returning cover the whole day "
              "(today so far)." % run_time(data), "",
              "- Today same hours: %d calls. Previous %d days' same-hours average: %s. %s" % (
                  t_calls, n, "not enough days yet" if avg is None else "%.1f" % avg,
                  "BELOW the alert line (under 50%% of the average, average at least %d)." % ALERT_MIN_AVG if alert else "No alert."),
              "",
              "| day | calls, same hours | callers, same hours | new callers (whole day) | returning callers (whole day) |",
              "|---|---|---|---|---|"]
        for day, calls, callers, new, ret in rows:
            L.append("| %s | %d | %d | %d | %d |" % (day, calls, callers, new, ret))
    L += ["", "## Agents outside ChatGPT (30-day trial, Imran 19 Nov: report every day)", "",
          "Programs other than ChatGPT that called the plugin (Claude, Cursor, scripts...). The program name is what the caller says it is.", "",
          "| day | agents (different callers) | calls | by program |", "|---|---|---|---|"]
    for d in days:
        p = d.get("plugin") or {}
        n = int(p.get("outside_callers", 0) or 0)
        if "outside_callers" not in p:
            who = "not counted yet (needs the client-name update)"
        else:
            who = "none" if n == 0 else str(n)
        L.append("| %s | %s | %s | %s |" % (d.get("day"), who, p.get("outside_calls", 0), kv(p.get("outside_by_client"), 8)))
    L += ["", "Refusal reasons: the plugin log stores ONE label (invalid_input) for three different niche answers (no niche given, niche word not known, "
          "niche not clear from the account). The real reason is not in the table, so it cannot be split until the app logs it."]
    L += ["", "Not in this report: page visits and the sticky-box events (they live in Vercel Analytics, which this robot cannot read).", ""]
    return "\n".join(L)


def not_set_up(missing):
    return "\n".join(["# Daily funnel numbers", "",
                      "NOT SET UP (%s UTC). The funnel robot ran but has no way to read the database yet." % c.now().strftime("%Y-%m-%d %H:%M"), "",
                      "Missing in the bots repo (GitHub, Settings, Secrets and variables, Actions): " + ", ".join(missing) + ".",
                      "Steps: docs/genie/ROBOTS_REPORTS_HOWTO.md in the inayaflow-social project.", ""])


def main():
    work = c.clone()
    path = os.path.join(work, OUT)
    try:
        url, anon, tok = (os.environ.get(n, "").strip() for n in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "FUNNEL_READ_TOKEN"))
        missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_ANON_KEY", anon), ("FUNNEL_READ_TOKEN", tok)) if not v]
        if missing:
            c.write_text(path, not_set_up(missing))
            c.commit_push("ops: funnel robot not set up")
            print("funnel: NOT SET UP missing=%d" % len(missing))
            return
        data = rpc(url, anon, tok, DAYS)
        if not isinstance(data, dict) or "days" not in data:
            raise RuntimeError("unexpected answer")
        hist = c.read_json(os.path.join(work, HIST), {})
        c.write_text(path, build(data, hist))
        if not c.DRY and data.get("today"):
            hist[data["today"]] = {"paying_total": data.get("paying_total"), "paying_by_tier": data.get("paying_by_tier")}
            c.write_json(os.path.join(work, HIST), {k: hist[k] for k in sorted(hist)[-400:]})
        saved = c.commit_push("ops: daily funnel numbers")
        print("funnel: days=%d saved=%s dry=%s" % (len(data["days"]), saved, c.DRY))
    except Exception as e:
        why = ""
        try:
            body = json.loads(e.read().decode("utf-8", "replace")) if hasattr(e, "read") else {}
            why = "%s %s" % (str(body.get("code", ""))[:12], str(body.get("message", body.get("hint", "")))[:60])
        except Exception:
            pass
        print("funnel: FAILED %s %s %s" % (type(e).__name__, getattr(e, "code", ""), why))
        try:
            c.error_notice(path, "daily funnel numbers", e)
            c.commit_push("ops: funnel error notice")
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
