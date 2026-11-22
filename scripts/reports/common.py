"""Shared helpers for the report robots (inbox + backlinks report, backlink batch, daily funnel numbers).

PUBLIC repo: nothing here may print mail addresses, subjects, bodies, links or tokens. Scripts print counts
and error TYPE names only. All detail goes to the PRIVATE repo (imran0786/genie, branch ops-reports).
"""
import datetime, json, os, re, shutil, subprocess, urllib.request

PRIVATE = "imran0786/genie"
REPORT_BRANCH = "ops-reports"
WORK = os.environ.get("REPORTS_WORK", "/tmp/ops_reports")
FIXTURE_DIR = os.environ.get("FIXTURE_DIR", "")  # local tests: read <FIXTURE_DIR>/<branch>/<path>, never touch git
DRY = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
# Claude Haiku 5.5 list price, USD per million tokens (prompts up to 100K tokens), read on the official
# pricing page 19 Nov 2026. Used only for the cost line in the reports.
PRICE_IN = float(os.environ.get("PRICE_IN_PER_M", "0.10"))
PRICE_OUT = float(os.environ.get("PRICE_OUT_PER_M", "0.50"))
MAX_AI_CALLS = int(os.environ.get("MAX_AI_CALLS", "25"))        # hard cap per run
MAX_AI_INPUT_CHARS = int(os.environ.get("MAX_AI_INPUT_CHARS", "60000"))  # per call (about 15K tokens)
USAGE = {"calls": 0, "in": 0, "out": 0}
USD_LIMIT = None   # when set (batch: what is left of the day's dollar cap), haiku() stops once this run has spent that much
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


class BudgetExceeded(RuntimeError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def today():
    return now().strftime("%Y-%m-%d")


def stamp():
    return now().strftime("%Y-%m-%d_%H%M")


# ---------- private repo (git, token from the environment, never printed) ----------

def _url():
    tok = os.environ.get("GENIE_LOGS_TOKEN")
    if not tok:
        raise RuntimeError("no GENIE_LOGS_TOKEN")
    return "https://x-access-token:%s@github.com/%s.git" % (tok, PRIVATE)


def git(*args, check=True, cwd=None):
    r = subprocess.run(["git", "-C", cwd or WORK, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed" % args[0])
    return r


def clone(branch=REPORT_BRANCH, work=None):
    """Shallow clone of one private branch into `work` (created empty when the branch does not exist yet)."""
    work = work or WORK
    if FIXTURE_DIR:
        os.makedirs(work, exist_ok=True)
        return work
    shutil.rmtree(work, ignore_errors=True)
    r = subprocess.run(["git", "clone", "-q", "--branch", branch, "--depth", "1", _url(), work], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", _url(), work], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("clone failed")
        git("checkout", "-q", "--orphan", branch, cwd=work)
        git("rm", "-rqf", ".", check=False, cwd=work)
    git("config", "user.name", "inayaflow-bot", cwd=work)
    git("config", "user.email", "contact@inayaflow.ai", cwd=work)
    return work


def fetch_file(branch, path, work=None):
    """Text of one file on another private branch ('' when missing). Read-only."""
    if FIXTURE_DIR:
        try:
            return open(os.path.join(FIXTURE_DIR, branch, path), encoding="utf-8").read()
        except OSError:
            return ""
    work = work or WORK
    if git("fetch", "-q", "--depth", "1", "origin", branch, check=False, cwd=work).returncode != 0:
        return ""
    r = subprocess.run(["git", "-C", work, "show", "FETCH_HEAD:" + path], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def list_files(branch, prefix, work=None):
    """File names under `prefix` on another private branch (after fetch_file/fetch of that branch)."""
    if FIXTURE_DIR:
        d = os.path.join(FIXTURE_DIR, branch, prefix)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []
    work = work or WORK
    if git("fetch", "-q", "--depth", "1", "origin", branch, check=False, cwd=work).returncode != 0:
        return []
    r = git("ls-tree", "--name-only", "FETCH_HEAD", prefix.rstrip("/") + "/", check=False, cwd=work)
    return sorted(os.path.basename(x) for x in r.stdout.split())


def read_tree(branch, prefixes, work=None, name_has=""):
    """{path: text} for every file under the given folders of another private branch (ONE fetch). Read-only."""
    out = {}
    if FIXTURE_DIR:
        for pre in prefixes:
            d = os.path.join(FIXTURE_DIR, branch, pre)
            for n in (sorted(os.listdir(d)) if os.path.isdir(d) else []):
                if name_has and name_has not in n:
                    continue
                try:
                    out[pre.rstrip("/") + "/" + n] = open(os.path.join(d, n), encoding="utf-8").read()
                except OSError:
                    pass
        return out
    work = work or WORK
    if git("fetch", "-q", "--depth", "1", "origin", branch, check=False, cwd=work).returncode != 0:
        return out
    sha = git("rev-parse", "FETCH_HEAD", cwd=work).stdout.strip()
    for pre in prefixes:
        r = git("ls-tree", "--name-only", sha, pre.rstrip("/") + "/", check=False, cwd=work)
        for path in r.stdout.split():
            if name_has and name_has not in path.rsplit("/", 1)[-1]:
                continue
            x = subprocess.run(["git", "-C", work, "show", "%s:%s" % (sha, path)], capture_output=True)
            if x.returncode == 0:
                out[path] = x.stdout.decode("utf-8", "replace")
    return out


_RANK = {"replied": 4, "sent": 3, "queued": 2, "skipped": 1}


def merge_state(ours, theirs, key=""):
    """Merge two versions of a JSON state file, key by key. Nothing is lost and nothing is blindly overwritten:
    numbers (counters) take the larger value; lists are joined without repeats; an outreach entry that exists on both sides keeps the
    one that is further along (replied > sent > queued > skipped), then the newer date, then ours; timestamps (last_run) take the newer."""
    if isinstance(ours, dict) and isinstance(theirs, dict):
        if "status" in ours and "status" in theirs:
            a, b = (_RANK.get(str(ours.get("status")), 0), str(ours.get("date", ""))), (_RANK.get(str(theirs.get("status")), 0), str(theirs.get("date", "")))
            return ours if a >= b else theirs
        out = dict(theirs)
        for k, v in ours.items():
            out[k] = merge_state(v, theirs[k], k) if k in theirs else v
        return out
    if isinstance(ours, list) and isinstance(theirs, list):
        seen, out = set(), []
        for x in theirs + ours:
            r = json.dumps(x, sort_keys=True)
            if r not in seen:
                seen.add(r)
                out.append(x)
        return out
    if isinstance(ours, (int, float)) and isinstance(theirs, (int, float)) and not isinstance(ours, bool):
        return max(ours, theirs)
    if key in ("last_run", "generated") and isinstance(ours, str) and isinstance(theirs, str):
        return max(ours, theirs)
    return ours


def merge_json(ours, theirs):
    """Candidates file ({"sites": [...]}) -> merge_candidates (newer checked row wins); prospect lists (rows with "site") -> union by site
    and page, theirs first; anything else -> merge_state."""
    if isinstance(ours, dict) and isinstance(theirs, dict) and "sites" in ours and "sites" in theirs:
        import merge_candidates
        return merge_candidates.merge(ours, theirs)
    if isinstance(ours, list) and isinstance(theirs, list) and all(isinstance(x, dict) and "site" in x for x in ours + theirs):
        seen, out = set(), []
        for x in theirs + ours:
            k = (str(x.get("site")), str(x.get("page")))
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out
    return merge_state(ours, theirs)


def commit_push(msg, branch=REPORT_BRANCH, work=None):
    """Commit everything in `work` and push `branch`. On a push race: rebase; if the rebase conflicts, abort it, fetch, reset to the remote
    branch and RE-APPLY our change by merging each JSON file we touched with the remote version (merge_json), then commit and push again.
    Up to 3 tries; then RuntimeError (callers treat that as "nothing was saved")."""
    work = work or WORK
    if FIXTURE_DIR:
        return False
    git("add", "-A", cwd=work)
    if git("diff", "--cached", "--quiet", check=False, cwd=work).returncode == 0:
        return False
    git("commit", "-q", "-m", msg, cwd=work)
    for _ in range(3):
        if git("push", "-q", "origin", branch, check=False, cwd=work).returncode == 0:
            return True
        if git("pull", "-q", "--rebase", "origin", branch, check=False, cwd=work).returncode != 0:
            git("rebase", "--abort", check=False, cwd=work)     # a conflict never leaves a half-done rebase behind
            names = git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD", cwd=work).stdout.split()
            ours = {}
            for n in names:
                r = git("show", "HEAD:" + n, check=False, cwd=work)
                if r.returncode == 0:
                    ours[n] = r.stdout
            git("fetch", "-q", "origin", branch, cwd=work)
            git("reset", "-q", "--hard", "FETCH_HEAD", cwd=work)
            for n, text in ours.items():
                path = os.path.join(work, n)
                merged = text
                if n.endswith(".json") and os.path.exists(path):
                    try:
                        merged = json.dumps(merge_json(json.loads(text), json.load(open(path, encoding="utf-8"))), indent=1, ensure_ascii=False, sort_keys=True)
                    except Exception:
                        merged = text
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(merged)
            git("add", "-A", cwd=work)
            if git("diff", "--cached", "--quiet", check=False, cwd=work).returncode == 0:
                return True                                     # the remote already holds everything we wanted to save
            git("commit", "-q", "-m", msg + " (merged)", cwd=work)
    raise RuntimeError("push failed")


def read_json(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, sort_keys=True)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def error_notice(path, robot, exc):
    """One line at the top of a report file when a run fails (error TYPE only, older content kept below)."""
    line = "> ROBOT ERROR %s UTC: %s failed (%s). The content below is from the last good run.\n\n" % (
        now().strftime("%Y-%m-%d %H:%M"), robot, type(exc).__name__)
    try:
        old = open(path, encoding="utf-8").read()
    except OSError:
        old = ""
    old = re.sub(r"\A(> ROBOT ERROR[^\n]*\n\n)+", "", old)
    write_text(path, line + old)


# ---------- markdown tables ----------

def md_rows(text, section=None):
    """Rows (list of cell lists) of the pipe tables in `text`; header and divider rows are dropped.
    With `section`, only tables under the first heading that contains that text."""
    rows, active, seen_header = [], section is None, False
    for line in (text or "").splitlines():
        if line.startswith("#"):
            active = section is None or section.lower() in line.lower()
            seen_header = False
            continue
        if not active or not line.startswith("|"):
            if not line.startswith("|"):
                seen_header = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        if not seen_header:
            seen_header = True
            continue
        rows.append(cells)
    return rows


def cell(s, limit=160):
    return re.sub(r"\s+", " ", str(s or "").replace("|", "/")).strip()[:limit]


# ---------- Claude Haiku (raw HTTP like the other robots in this repo; capped) ----------

def haiku(system, user, max_tokens=3000, post=None):
    """One capped Haiku call. Returns the text. Raises BudgetExceeded past MAX_AI_CALLS."""
    if USAGE["calls"] >= MAX_AI_CALLS:
        raise BudgetExceeded("AI call cap reached")
    if USD_LIMIT is not None and cost_usd() >= USD_LIMIT:
        raise BudgetExceeded("AI dollar cap reached")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    if len(user) > MAX_AI_INPUT_CHARS:
        user = user[:MAX_AI_INPUT_CHARS] + "\n[INPUT CUT at %d characters]" % MAX_AI_INPUT_CHARS
    body = {"model": MODEL, "max_tokens": max_tokens, "output_config": {"effort": "low"}, "system": system,
            "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    USAGE["calls"] += 1
    data = (post or _post)("https://api.anthropic.com/v1/messages", body, headers)
    u = data.get("usage") or {}
    USAGE["in"] += int(u.get("input_tokens", 0) or 0) + int(u.get("cache_read_input_tokens", 0) or 0) + int(u.get("cache_creation_input_tokens", 0) or 0)
    USAGE["out"] += int(u.get("output_tokens", 0) or 0)
    if data.get("stop_reason") == "refusal":
        raise RuntimeError("refusal")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def _post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def json_obj(raw):
    """First JSON object in a model answer, or None."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def cost_usd():
    return USAGE["in"] / 1e6 * PRICE_IN + USAGE["out"] / 1e6 * PRICE_OUT


def cost_line():
    return "AI use this run: %d Haiku calls, %d tokens in, %d out, about $%.4f (estimate at $%.2f / $%.2f per million)." % (
        USAGE["calls"], USAGE["in"], USAGE["out"], cost_usd(), PRICE_IN, PRICE_OUT)


# ---------- web (GET only) ----------

def http_get(url, timeout=15, limit=600000):
    """(status, final_url, text). Never raises. GET only: no forms, no sign-ups."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5", "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(limit)
            return r.status, r.geturl(), raw.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, url, ""
    except Exception:
        return 0, url, ""


def reg_domain(host):
    parts = (host or "").lower().split(":")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


def html_text(html, limit=4000):
    t = re.sub(r"<(style|script|noscript|svg)\b.*?</\1>", " ", html or "", flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;|&#160;", " ", t)
    t = re.sub(r"&amp;", "&", t)
    return re.sub(r"\s+", " ", t).strip()[:limit]


def html_title(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return html_text(m.group(1), 200) if m else ""
