"""AUTO-DEPLOY ROBOT: the rules, as pure functions (no git, no network, no clock of their own).

Every function here answers one question about a candidate change and returns the reasons for a refusal as a list of
plain sentences. An empty list means "this check found nothing against it". The robot (robot.py) deploys only when
EVERY check returns an empty list. All limits come from config.json next to this file.
"""
import datetime, difflib, html, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SHA_RE = re.compile(r"[0-9a-f]{40}")
BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,120}")
LOCKED_KEYS = {"slug", "path", "url", "canonical", "id", "icon", "key", "src", "image", "type", "locale", "lang"}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def load_config(path=None):
    with open(path or os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


# ---------- 1. which branches may be candidates ----------

def branch_ok(branch, prefixes):
    """A candidate branch name: plain characters, one of the allowed prefixes, never main / master / ops-*."""
    b = str(branch or "")
    if not BRANCH_RE.fullmatch(b) or ".." in b or b.endswith("/") or b.endswith(".lock") or "//" in b:
        return False
    if b in ("main", "master", "HEAD") or b.startswith("ops-") or b.startswith("refs/"):
        return False
    return any(b.startswith(p) for p in prefixes)


def parse_request(text, cfg):
    """A request file (JSON: branch, sha, optional note). Returns (request dict, None) or (None, reason)."""
    try:
        d = json.loads(text)
    except Exception:
        return None, "request file is not JSON"
    if not isinstance(d, dict):
        return None, "request file is not a JSON object"
    branch, sha = str(d.get("branch", "")), str(d.get("sha", "")).lower()
    if not branch_ok(branch, cfg["request_prefixes"]):
        return None, "branch name not allowed for a deploy request"
    if not SHA_RE.fullmatch(sha):
        return None, "sha must be the full 40-character commit id (the robot deploys exactly that commit)"
    return {"branch": branch, "sha": sha, "note": str(d.get("note", ""))[:300]}, None


# ---------- 2 + 3. allow-list and deny-list ----------

def classify(path, cfg):
    """('deny', why) | ('allow', rule) | ('unlisted', '')  for one path. Deny wins over allow."""
    p = str(path)
    skip_kw = any(p.startswith(pre) for pre in cfg.get("keyword_deny_skip_under", []))
    for d in cfg["deny"]:
        if d.get("keyword") and skip_kw:
            continue
        if re.search(d["pattern"], p, re.I):
            return "deny", d["why"]
    hits = [a for a in cfg["allow"] if re.fullmatch(a["pattern"].lstrip("^").rstrip("$"), p)]
    if len(hits) == 1:
        return "allow", hits[0]
    return "unlisted", ""


def parse_raw(raw):
    """Rows of `git diff-tree -r --raw -z --no-renames A B`: [{path, status, old_mode, new_mode}]."""
    out, parts = [], [x for x in (raw or "").split("\0")]
    i = 0
    while i < len(parts):
        head = parts[i]
        if not head.startswith(":"):
            i += 1
            continue
        f = head[1:].split(" ")
        if len(f) < 5 or i + 1 >= len(parts):
            out.append({"path": "?", "status": "?", "old_mode": "?", "new_mode": "?"})
            break
        out.append({"path": parts[i + 1], "status": f[4][:1], "old_mode": f[0], "new_mode": f[1]})
        i += 2
    return out


def parse_numstat(text):
    """{path: (added, deleted)} from `git diff-tree -r --numstat -z --no-renames`; binary files give (None, None)."""
    out = {}
    for row in (text or "").split("\0"):
        m = re.fullmatch(r"(\d+|-)\t(\d+|-)\t(.+)", row.strip("\n"), re.S)
        if m:
            a, d = m.group(1), m.group(2)
            out[m.group(3)] = (None, None) if "-" in (a, d) else (int(a), int(d))
    return out


def check_changes(changes, numstat, diff_bytes, cfg):
    """Points 2, 3, 4: every touched file allowed, none denied, no delete / rename / odd file type, size limits.
    Returns (reasons, rules) where rules = {path: allow rule}."""
    reasons, rules, lim = [], {}, cfg["limits"]
    if not changes:
        return ["nothing would change on main"], rules
    for c in changes:
        p, st = c["path"], c["status"]
        verdict, info = classify(p, cfg)
        if verdict == "deny":
            reasons.append("NEEDS DIRECTOR: %s touches a never-automatic area (%s)" % (p, info))
            continue
        if verdict != "allow":
            reasons.append("NEEDS DIRECTOR: %s is not on the allow-list" % p)
            continue
        if st not in ("A", "M"):
            reasons.append("NEEDS DIRECTOR: %s is %s (only added or modified files are automatic; no delete, rename or type change)" % (
                p, {"D": "deleted", "R": "renamed", "C": "copied", "T": "a file-type change"}.get(st, "status " + st)))
            continue
        if st not in info["status"]:
            reasons.append("NEEDS DIRECTOR: %s may only be %s by the robot, not %s" % (p, " or ".join(info["status"]), st))
            continue
        if c["new_mode"] != "100644" or (st == "M" and c["old_mode"] != "100644"):
            reasons.append("NEEDS DIRECTOR: %s is not a plain file (mode %s)" % (p, c["new_mode"]))
            continue
        if numstat.get(p, (None, None))[0] is None:
            reasons.append("NEEDS DIRECTOR: %s is binary or has no line count" % p)
            continue
        rules[p] = info
    if len(changes) > lim["max_files"]:
        reasons.append("TOO BIG: %d files changed (limit %d)" % (len(changes), lim["max_files"]))
    lines = sum((a or 0) + (d or 0) for a, d in numstat.values())
    if lines > lim["max_changed_lines"]:
        reasons.append("TOO BIG: %d changed lines (limit %d)" % (lines, lim["max_changed_lines"]))
    if diff_bytes > lim["max_diff_bytes"]:
        reasons.append("TOO BIG: the diff is %d bytes (limit %d)" % (diff_bytes, lim["max_diff_bytes"]))
    return reasons, rules


# ---------- text checks shared by guides and copy ----------

def added_lines(old, new):
    """Lines that are in `new` and not in `old` (what the change adds)."""
    a, b = (old or "").split("\n"), (new or "").split("\n")
    out = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            out.extend(b[j1:j2])
    return out


def check_banned(path, lines, cfg):
    reasons = []
    for rule in cfg["banned_added_text"]:
        rx = re.compile(rule["pattern"], re.M)
        for ln in lines:
            if rx.search(ln):
                reasons.append("NEEDS DIRECTOR: %s adds text the robot may not publish (%s)" % (path, rule["why"]))
                break
    longest = max((len(x) for x in lines), default=0)
    if longest > cfg["limits"]["max_line_chars"]:
        reasons.append("TOO BIG: %s adds a line of %d characters (limit %d)" % (path, longest, cfg["limits"]["max_line_chars"]))
    return reasons


# ---------- guides (content/blog/*.mdx) ----------

def split_frontmatter(text):
    """(frontmatter, body) or (None, None). The first line must be exactly '---': gray-matter runs '---js' as code."""
    if not text.startswith("---\n"):
        return None, None
    end = text.find("\n---\n", 3)
    if end < 0:
        return None, None
    return text[4:end + 1], text[end + 5:]


def fm_value(fm, key):
    """(count of top-level `key:` lines, unquoted value of the first)."""
    vals = re.findall(r"(?m)^%s:[ \t]*(.*)$" % re.escape(key), fm or "")
    if not vals:
        return 0, ""
    v = vals[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        q, v = v[0], v[1:-1]
        v = v.replace('\\"', '"').replace("\\\\", "\\") if q == '"' else v.replace("''", "'")
    return len(vals), v


def _day(s):
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def check_guide(path, old, new, today, cfg):
    """One guide file. `old` is None for a new guide. Returns (reasons, info) with info = {slug, title, url, new}."""
    stem = os.path.basename(path)[:-4]
    info = {"slug": stem, "title": "", "url": "%s/blog/%s" % (cfg["site"], stem), "new": old is None}
    R = []
    if "\r" in new or "\0" in new:
        return ["NEEDS DIRECTOR: %s has Windows line ends or a NUL byte" % path], info
    fm, body = split_frontmatter(new)
    if fm is None:
        return ["NEEDS DIRECTOR: %s must start with a plain '---' frontmatter block (a '---js' block would run as code at build time)" % path], info
    if re.search(r"(?m)(^|[\s\[{,:-])!(!|<|[A-Za-z])", fm):
        R.append("NEEDS DIRECTOR: %s frontmatter uses a YAML tag ('!...')" % path)
    for key in ("title", "date", "slug", "updated", "tool"):
        if fm_value(fm, key)[0] > 1:
            R.append("NEEDS DIRECTOR: %s frontmatter has '%s:' twice" % (path, key))
    n, title = fm_value(fm, "title")
    if n != 1 or not (10 <= len(title) <= 120):
        R.append("NEEDS DIRECTOR: %s needs one title of 10 to 120 characters" % path)
    info["title"] = title
    n, slug = fm_value(fm, "slug")
    if n and slug != stem:
        R.append("NEEDS DIRECTOR: %s says slug '%s' but the file name gives '%s' (it could take over another page's address)" % (path, slug[:60], stem))
    lo, hi = today - datetime.timedelta(days=3), today + datetime.timedelta(days=1)
    n, date = fm_value(fm, "date")
    d = _day(date)
    if n != 1 or d is None:
        R.append("NEEDS DIRECTOR: %s needs one date: YYYY-MM-DD" % path)
    elif old is None and not (lo <= d <= hi):
        R.append("NEEDS DIRECTOR: %s is new but dated %s (honest dates: a new guide carries today's date)" % (path, date))
    n_up, updated = fm_value(fm, "updated")
    if old is not None:
        ofm, _ = split_frontmatter(old)
        if ofm is None:
            R.append("NEEDS DIRECTOR: the live version of %s has no plain frontmatter" % path)
        else:
            if fm_value(ofm, "date")[1] != date:
                R.append("NEEDS DIRECTOR: %s changes its first-published date (honest dates)" % path)
            if updated != fm_value(ofm, "updated")[1]:
                u = _day(updated)
                if u is None or not (lo <= u <= hi):
                    R.append("NEEDS DIRECTOR: %s sets 'updated' to %s, which is not today (honest dates)" % (path, updated[:20] or "nothing"))
    elif n_up:
        u = _day(updated)
        if u is None or u > hi or (d and u < d):
            R.append("NEEDS DIRECTOR: %s has an 'updated' date that does not fit a new guide" % path)
    if len((body or "").strip()) < 400:
        R.append("NEEDS DIRECTOR: %s has almost no text under the frontmatter" % path)
    R += check_banned(path, added_lines(old, new), cfg)
    return R, info


def new_guide_count(changes):
    return sum(1 for c in changes if c["status"] == "A" and re.fullmatch(r"content/blog/[^/]+\.mdx", c["path"]))


# ---------- copy files (lib/*Copy.ts and friends): judged from the TypeScript checker's report ----------

def hosts_in(text):
    return {h.lower() for h in re.findall(r"https?://([A-Za-z0-9.-]+)", text or "")}


def check_copy(path, report, old_text, routes, today, cfg):
    """`report` = what ts_strings_only.cjs printed for this file: {ok, reason, changed: [{old, new, key, kind}]}.
    The checker proved that ONLY the text inside string literals changed; this decides which string changes are fine."""
    if not isinstance(report, dict) or not report.get("ok"):
        return ["NEEDS DIRECTOR: %s changes code, not only words (%s)" % (path, str((report or {}).get("reason", "no checker report"))[:200])]
    R, old_hosts = [], hosts_in(old_text) | set(cfg.get("copy_hosts_ok", []))
    changed = report.get("changed") or []
    if not changed and not report.get("table_changed"):
        return ["NEEDS DIRECTOR: %s changed but no string or table row did (comments or layout only: nothing to deploy)" % path]
    for ch in changed:
        old, new, key = str(ch.get("old", "")), str(ch.get("new", "")), str(ch.get("key") or "")
        label = "%s (%s)" % (path, key or "text")
        if key.lower() in LOCKED_KEYS:
            R.append("NEEDS DIRECTOR: %s is an address or id, not copy" % label)
            continue
        if len(new) > 2500:
            R.append("NEEDS DIRECTOR: %s is longer than 2,500 characters" % label)
        if re.search(r"<\s*[A-Za-z/!]", new) and not re.search(r"<\s*[A-Za-z/!]", old):
            R.append("NEEDS DIRECTOR: %s adds HTML" % label)
        if "://" in new or "://" in old or "www." in new.lower():
            bad = sorted(hosts_in(new) - old_hosts)
            plain_http = len(re.findall(r"://", new)) != len(re.findall(r"https://", new))
            bare_www = "www." in new.lower() and "www." not in old.lower()
            if bad or plain_http or bare_www:
                R.append("NEEDS DIRECTOR: %s adds a link to a site this file did not link before, or a non-https link (%s)" % (label, ", ".join(bad)[:120] or "unclear address"))
            continue
        if old.startswith("/") or new.startswith("/"):
            target = new.split("#")[0].rstrip("/") or "/"
            if not (old.startswith("/") and new.startswith("/") and target in routes and re.fullmatch(r"/[A-Za-z0-9/_-]*(#[a-z0-9-]+)?", new)):
                R.append("NEEDS DIRECTOR: %s points to %s, which is not a page the robot knows" % (label, new[:80]))
            continue
        if DATE_RE.fullmatch(old) or DATE_RE.fullmatch(new):
            o, n = _day(old), _day(new)
            if not (o and n and o <= n <= today + datetime.timedelta(days=1)):
                R.append("NEEDS DIRECTOR: %s changes a date to %s (must be a real date, not before the old one, not in the future)" % (label, new[:20]))
            continue
        prose_old = ch.get("kind") == "template" or " " in old.strip()
        if not prose_old or (old.strip() and not new.strip()):
            R.append("NEEDS DIRECTOR: %s is a short code-like value ('%s'), not a sentence" % (label, old[:40]))
    R += check_banned(path, [str(c.get("new", "")) for c in changed], cfg)
    return R


def copy_title_markers(report):
    """New titles of a copy file (plain strings under a `title` / `h1` key): the live page must show one of them."""
    out = []
    for ch in (report or {}).get("changed") or []:
        if str(ch.get("key") or "") in ("title", "h1") and ch.get("kind") == "string" and len(str(ch.get("new", ""))) >= 10:
            out.append(str(ch["new"]))
    return out


# ---------- 5. tests ----------

def parse_tests(out):
    """Totals and failing names from `node --test` output (spec or TAP style). Same reader as the job runner, names not cut."""
    def num(k):
        m = re.findall(r"(?m)^(?:#|ℹ)\s+%s\s+(\d+)" % k, out or "")
        return int(m[-1]) if m else None
    names = []
    for m in re.finditer(r"(?m)^\s*(?:✖|not ok \d+ -)\s+(.+?)(?:\s+\(\d+(?:\.\d+)?ms\))?\s*$", out or ""):
        n = m.group(1).strip()
        if n not in names and not n.lower().startswith("failing tests"):
            names.append(n[:200])
    return {"tests": num("tests"), "pass": num("pass"), "fail": num("fail"), "failing": names}


def tests_line(t):
    if not t or t.get("tests") is None:
        return "tests did not run"
    return "%d of %d pass, %d fail" % (t.get("pass") or 0, t["tests"], t.get("fail") or 0)


def compare_tests(base, now):
    """No NEW failure, no test gone missing. Returns reasons."""
    if not base or base.get("tests") is None:
        return ["TESTS: the baseline run on main gave no totals"]
    if not now or now.get("tests") is None:
        return ["TESTS: the run on the candidate gave no totals (crash or timeout)"]
    R = []
    new = [n for n in now.get("failing", []) if n not in set(base.get("failing", []))]
    if new:
        R.append("TESTS: %d new failure(s): %s" % (len(new), "; ".join(new[:6])))
    if (now.get("fail") or 0) > (base.get("fail") or 0):
        R.append("TESTS: %d tests fail on the candidate, %d on main" % (now.get("fail") or 0, base.get("fail") or 0))
    if now["tests"] < base["tests"]:
        R.append("TESTS: the candidate runs %d tests, main runs %d (tests went missing)" % (now["tests"], base["tests"]))
    if (now.get("pass") or 0) < (base.get("pass") or 0):
        R.append("TESTS: fewer tests pass on the candidate (%d) than on main (%d)" % (now.get("pass") or 0, base.get("pass") or 0))
    if (now.get("fail") or 0) == 0 and now.get("exit", 0) != 0 and base.get("exit", 0) == 0:
        R.append("TESTS: the candidate run ended with an error code although no test is listed as failing")
    return R


# ---------- 6. the review call ----------

def review_max_cost(prompt_chars, cfg):
    """The most one review can cost: the whole prompt at ~3 characters per token (rounds UP) plus every output token."""
    r = cfg["review"]
    return (prompt_chars / 3.0) * r["price_in_per_m"] / 1e6 + r["max_tokens"] * r["price_out_per_m"] / 1e6


def review_cost(usage, cfg):
    r, u = cfg["review"], usage or {}
    tin = sum(int(u.get(k, 0) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return tin * r["price_in_per_m"] / 1e6 + int(u.get("output_tokens", 0) or 0) * r["price_out_per_m"] / 1e6


def parse_verdict(data):
    """(passed, text) from the API answer. PASS only when the model ended normally and its FIRST line is exactly PASS."""
    if not isinstance(data, dict):
        return False, "no answer"
    stop = data.get("stop_reason")
    text = "\n".join(b.get("text", "") for b in (data.get("content") or []) if isinstance(b, dict) and b.get("type") == "text").strip()
    if stop != "end_turn":
        return False, "FAIL (the review did not finish: %s)\n%s" % (stop, text[:1500])
    first = text.split("\n", 1)[0].strip()
    return first == "PASS", text[:6000]


# ---------- 8. live checks ----------

def norm(s):
    return re.sub(r"\s+", " ", html.unescape(html.unescape(str(s or "")))).replace("’", "'").replace("‘", "'").strip().lower()


def page_shows(page_html, marker):
    return bool(marker) and norm(marker) in norm(page_html)


def looks_like_error_page(page_html):
    t = page_html or ""
    return ("InayaFlow" not in t) or bool(re.search(r"Application error|This page could not be found|Deployment is building|__next_error__", t))


def urls_for(rules, guide_infos, copy_reports, cfg):
    """Pages to fetch after a deploy, one group per changed file: [{path, kind, urls, markers}].
    EVERY url must answer 200 with a real InayaFlow page. When a group has markers (a guide's title; a copy file's
    changed title / h1), at least ONE of its pages must show one of them."""
    out = []
    for path, rule in sorted(rules.items()):
        if rule["kind"] == "guide":
            g = guide_infos.get(path) or {}
            out.append({"path": path, "kind": "guide", "urls": [g.get("url")], "markers": [g.get("title", "")]})
        elif rule["kind"] == "copy":
            urls = [cfg["site"] + (u if u != "/" else "") for u in rule.get("urls", [])]
            out.append({"path": path, "kind": "copy", "urls": urls, "markers": copy_title_markers((copy_reports or {}).get(path))})
    return out


# ---------- 9 + 10. caps and the log ----------

def day_counts(state, day):
    d = (state.get("days") or {}).get(day) or {}
    return int(d.get("deploys", 0) or 0), float(d.get("review_usd", 0.0) or 0.0), int(d.get("reverts", 0) or 0)


def check_caps(state, day, new_guides_here, guides_on_main_today, cfg):
    """Daily caps. A capped candidate is not refused for good: it waits for the next day."""
    lim = cfg["limits"]
    deploys, _spent, _rev = day_counts(state, day)
    R = []
    if deploys >= lim["deploys_per_day"]:
        R.append("CAP: %d deploys already today (limit %d a day)" % (deploys, lim["deploys_per_day"]))
    if new_guides_here and new_guides_here + guides_on_main_today > lim["new_guides_per_day"]:
        R.append("CAP: %d new guide(s) here + %d already published today is over the limit of %d new guides a day" % (
            new_guides_here, guides_on_main_today, lim["new_guides_per_day"]))
    return R


def cell(s, limit=220):
    return re.sub(r"\s+", " ", str(s or "").replace("|", "/")).strip()[:limit]


def log_row(e):
    return "| %s | %s | %s | `%s` | %s | %s |" % (cell(e.get("time")), cell(e.get("result")), cell(e.get("branch"), 80), cell(e.get("sha", ""))[:10],
                                                cell(", ".join(e.get("files") or []), 160), cell(e.get("reason"), 300))


def deploys_section(jsonl_text, now, hours=24):
    """The 'Deploys' block for the report robots, from ops/deploy_log.jsonl (newest entries first in the output)."""
    rows, since = [], now - datetime.timedelta(hours=hours)
    for line in (jsonl_text or "").splitlines():
        try:
            e = json.loads(line)
            t = datetime.datetime.strptime(e["time"], "%Y-%m-%d %H:%M").replace(tzinfo=datetime.timezone.utc)
        except Exception:
            continue
        if t >= since:
            rows.append(e)
    rows.sort(key=lambda e: e["time"], reverse=True)
    count = lambda k: sum(1 for e in rows if str(e.get("result", "")).startswith(k))
    L = ["## Deploys by the robot, last %d hours" % hours, "",
         "Deployed: %d. Reverted: %d. Refused / QA FAIL / tests: %d. Dry runs: %d. Source: ops-deploy ops/deploy_log.md." % (
             count("DEPLOYED"), count("REVERT"), sum(1 for e in rows if str(e.get("result", "")).split(" ")[0] in ("REFUSED", "QA", "TESTS", "ERROR")), count("DRY")), ""]
    if not rows:
        return "\n".join(L + ["None.", ""])
    L += ["| time (UTC) | result | branch | commit | files | reason / note |", "|---|---|---|---|---|---|"]
    L += [log_row(e) for e in rows[:40]]
    return "\n".join(L + [""])
