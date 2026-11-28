"""DataForSEO keyword volumes + difficulty (one-shot, spend-guarded).

Imran (28 Nov 2026): "We only have $1 ... use it very wisely because we won't be topping it up."
HARD CAP for everything this script ever spends: USD 0.35 (state kept in the private repo, ops-dataforseo/SPEND.json).

Prices read on dataforseo.com on 28 Nov 2026:
  Google Ads search volume, STANDARD queue: $0.06 per task (up to 1,000 keywords), results in 1-3 hours.
  Labs bulk keyword difficulty, live: $0.012 per task + $0.00012 per keyword (up to 1,000 keywords).
GET calls (task_get, user_data) are free.

Steps (env STEP):
  dry      -> read balance, count keywords and planned cost, post nothing.
  volume   -> post search-volume tasks (standard), poll until ready (max ~4 h), save CSV.
  difficulty -> bulk keyword difficulty for keywords with volume >= MIN_VOLUME from the volume CSV, save CSV.

PUBLIC repo: prints counts, costs and balances only. Results go to PRIVATE imran0786/genie, branch ops-dataforseo.
"""
import base64, csv, io, json, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(__file__))
import common as c  # noqa: E402

API = "https://api.dataforseo.com/v3"
BRANCH = "ops-dataforseo"
CAP_USD = 0.35
PRICE_VOLUME_TASK = 0.06
PRICE_KD_TASK = 0.012
PRICE_KD_ITEM = 0.00012
CHUNK = 1000
MIN_VOLUME = int(os.environ.get("MIN_VOLUME", "30"))
STEP = os.environ.get("STEP", "dry").strip().lower()
INPUT = os.environ.get("KEYWORDS_FILE", "data/dfs-input-2026-11-28.txt")
TAG = "2026-11-28"


def _auth():
    login, pw = os.environ.get("DATAFORSEO_LOGIN", ""), os.environ.get("DATAFORSEO_PASSWORD", "")
    if not login or not pw:
        raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set")
    return "Basic " + base64.b64encode(("%s:%s" % (login, pw)).encode()).decode()


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, headers={"Authorization": _auth(), "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def balance():
    j = call("GET", "/appendix/user_data")
    return float(j["tasks"][0]["result"][0]["money"]["balance"])


def chunks(xs, n=CHUNK):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def load_keywords():
    kws, seen = [], set()
    for line in open(INPUT, encoding="utf-8"):
        k = line.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            kws.append(k)
    return kws


def spend_state(work):
    return c.read_json(os.path.join(work, "SPEND.json"), {"spent": 0.0, "runs": []})


def record(work, state, step, tasks, cost, bal_before, bal_after):
    state["spent"] = round(state["spent"] + cost, 4)
    state["runs"].append({"at": c.stamp(), "step": step, "tasks": tasks, "cost": round(cost, 4),
                          "balance_before": bal_before, "balance_after": bal_after})
    c.write_json(os.path.join(work, "SPEND.json"), state)
    lines = ["# DataForSEO spend (cap $%.2f, Imran: use the $1 wisely)" % CAP_USD, "",
             "| when (UTC) | step | tasks | cost $ | balance before | balance after |", "|---|---|---|---|---|---|"]
    for r in state["runs"]:
        lines.append("| %s | %s | %s | %.4f | %s | %s |" % (r["at"], r["step"], r["tasks"], r["cost"], r["balance_before"], r["balance_after"]))
    lines += ["", "Total spent by this robot: $%.4f of $%.2f cap." % (state["spent"], CAP_USD)]
    c.write_text(os.path.join(work, "SPEND.md"), "\n".join(lines) + "\n")


def guard(state, planned, bal):
    left = CAP_USD - state["spent"]
    print("planned $%.4f | robot spent so far $%.4f | cap left $%.4f | balance $%.4f" % (planned, state["spent"], left, bal))
    if planned > left + 1e-9:
        raise c.BudgetExceeded("planned cost over the cap")
    if planned > bal + 1e-9:
        raise c.BudgetExceeded("planned cost over the balance")


def do_volume(work, state, kws, dry):
    groups = chunks(kws)
    planned = 0.0 if os.path.exists(os.path.join(work, "volume-tasks.json")) else PRICE_VOLUME_TASK * len(groups)
    bal = balance()
    print("volume: %d keywords -> %d tasks" % (len(kws), len(groups)))
    guard(state, planned, bal)
    if dry:
        return
    if os.path.exists(os.path.join(work, "keyword-volumes-%s.csv" % TAG)):
        raise RuntimeError("volume CSV already exists: refusing to pay twice")
    tasks_file = os.path.join(work, "volume-tasks.json")
    if os.path.exists(tasks_file):   # tasks already paid for: only fetch them again (GET is free), never post twice
        ids = c.read_json(tasks_file, {}).get("ids", [])
        print("re-using %d posted tasks (no new spend)" % len(ids))
    else:
        body = [{"keywords": g, "location_code": 2840, "language_code": "en", "tag": "inayaflow-%d" % i} for i, g in enumerate(groups)]
        j = call("POST", "/keywords_data/google_ads/search_volume/task_post", body)
        ids, cost = [], float(j.get("cost") or 0)
        for t in j.get("tasks", []):
            if t.get("status_code") == 20100:
                ids.append(t["id"])
            else:
                print("task refused: status %s" % t.get("status_code"))
        c.write_json(tasks_file, {"ids": ids, "posted_at": c.stamp(), "cost": cost})
        record(work, state, "volume task_post", len(ids), cost, bal, balance())
        c.commit_push("dataforseo: volume tasks posted", BRANCH, work)   # save ids at once, so a crash never means paying again
    rows, pending = [], list(ids)
    deadline = time.time() + 4.5 * 3600
    while pending and time.time() < deadline:
        time.sleep(300)
        for tid in list(pending):
            g = call("GET", "/keywords_data/google_ads/search_volume/task_get/%s" % tid)
            t = g["tasks"][0]
            if t.get("status_code") == 20000:
                for it in t.get("result") or []:
                    ms = it.get("monthly_searches") or []
                    rows.append({"keyword": it.get("keyword"), "search_volume": it.get("search_volume"),
                                 "competition": it.get("competition"), "competition_index": it.get("competition_index"),
                                 "cpc": it.get("cpc"),
                                 "monthly_last12": ";".join("%s-%02d:%s" % (m.get("year"), m.get("month"), m.get("search_volume")) for m in ms[:12])})
                pending.remove(tid)
        print("ready %d / %d" % (len(ids) - len(pending), len(ids)))
    if pending:
        raise RuntimeError("tasks not ready in time; ids saved in volume-tasks.json (GET again later, free)")
    write_csv(os.path.join(work, "keyword-volumes-%s.csv" % TAG), rows)
    print("volumes saved: %d rows, %d with volume >= %d" % (len(rows), sum(1 for r in rows if (r["search_volume"] or 0) >= MIN_VOLUME), MIN_VOLUME))


def do_difficulty(work, state, dry):
    path = os.path.join(work, "keyword-volumes-%s.csv" % TAG)
    if not os.path.exists(path):
        raise RuntimeError("run the volume step first")
    kws = [r["keyword"] for r in csv.DictReader(open(path, encoding="utf-8")) if r["search_volume"] and int(float(r["search_volume"])) >= MIN_VOLUME]
    groups = chunks(kws)
    planned = sum(PRICE_KD_TASK + PRICE_KD_ITEM * len(g) for g in groups)
    bal = balance()
    print("difficulty: %d keywords (volume >= %d) -> %d tasks" % (len(kws), MIN_VOLUME, len(groups)))
    guard(state, planned, bal)
    if dry or not kws:
        return
    if os.path.exists(os.path.join(work, "keyword-difficulty-%s.csv" % TAG)):
        raise RuntimeError("difficulty CSV already exists: refusing to pay twice")
    rows, cost = [], 0.0
    for g in groups:
        j = call("POST", "/dataforseo_labs/google/bulk_keyword_difficulty/live", [{"keywords": g, "location_code": 2840, "language_code": "en"}])
        cost += float(j.get("cost") or 0)
        for t in j.get("tasks", []):
            for res in t.get("result") or []:
                for it in res.get("items") or []:
                    rows.append({"keyword": it.get("keyword"), "keyword_difficulty": it.get("keyword_difficulty")})
    write_csv(os.path.join(work, "keyword-difficulty-%s.csv" % TAG), rows)
    record(work, state, "difficulty live", len(groups), cost, bal, balance())
    print("difficulty saved: %d rows" % len(rows))


def write_csv(path, rows):
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), lineterminator=chr(10))
        w.writeheader()
        w.writerows(rows)
    c.write_text(path, buf.getvalue())


def main():
    work = c.clone(BRANCH)
    state = spend_state(work)
    dry = STEP == "dry"
    try:
        if STEP in ("dry", "volume"):
            do_volume(work, state, load_keywords(), dry)
        if STEP in ("dry", "difficulty") and (STEP == "difficulty" or os.path.exists(os.path.join(work, "keyword-volumes-%s.csv" % TAG))):
            do_difficulty(work, state, dry)
    except c.BudgetExceeded as e:
        print("STOPPED: %s" % e)
        sys.exit(1)
    finally:
        if not dry:
            c.commit_push("dataforseo: %s %s" % (STEP, c.stamp()), BRANCH, work)


if __name__ == "__main__":
    main()
