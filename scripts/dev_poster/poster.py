#!/usr/bin/env python3
"""DEV POSTER (23 Nov 2026): ONE useful, original article a day on DEV (dev.to, account @inayaflow) through the official Forem API.

Imran 23 Nov: "do the API keys tomorrow first" -> cloud API-posting robot, one useful 600+ word post per tool/guide,
contextual followed link, indexed.

Flow: pick the page that was not used for 14 days -> read it on inayaflow.ai -> Claude Sonnet (web search + fetch) writes the
article -> offline checks (words, title, tags, link, numbers vs the fetched sources, 7-word overlap, banned phrases, handles)
-> Claude Haiku QA reads the article with the fetched source texts -> publish (POST https://dev.to/api/articles) -> read the
live page and record whether OUR link is followed. FAIL = nothing is published and the draft goes to the PRIVATE repo.

PUBLIC repo: stdout = page slug, status words, counts, cost. Never the key, never article text. Everything else goes to the
PRIVATE repo (imran0786/genie, branch ops-dev-poster) through the workflow. canonical_url is never set (our own article).
DEV's Terms (section 11): content must be on-topic, high-quality and "not designed primarily for the purposes of promotion or
creating backlinks" -> one article a day, real value first, one contextual link (two at most).
"""
import datetime, json, os, re, sys, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts", "reports"))
import common as c  # noqa: E402  (http_get, html_text, json_obj)

OPS = os.environ.get("DEV_OPS_DIR", os.path.join(ROOT, "ops"))
STATE_PATH = os.path.join(OPS, "dev_poster_state.json")
LOG_PATH = os.path.join(OPS, "dev_poster_log.md")
DRAFT_DIR = os.path.join(OPS, "dev_drafts")
DRY = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
WRITER = os.environ.get("DEV_WRITER_MODEL", "claude-sonnet-5-5")
QA_MODEL = os.environ.get("DEV_QA_MODEL", "claude-haiku-5-5")
USD_CAP = float(os.environ.get("DEV_RUN_USD_CAP", "0.60"))
RETRY_BELOW = float(os.environ.get("DEV_RETRY_BELOW_USD", "0.30"))   # a 2nd writer try only when this much or less has been spent
MAX_SEARCHES = int(os.environ.get("DEV_MAX_SEARCHES", "5"))
REST_DAYS = 14
MIN_WORDS, MAX_WORDS = 600, 1050
# USD per million tokens (official pricing, same table as scripts/job_runner/runner.py); web search $10 per 1,000
PRICES = {"claude-sonnet-5-5": (2.00, 10.00, 0.20), "claude-haiku-5-5": (0.10, 0.50, 0.01)}
SEARCH_PRICE = 0.01
API = "https://dev.to/api/articles"
UA = "inayaflow-dev-poster/1.0 (+https://inayaflow.ai)"

# (slug, url, kind, what it is, topic)
PAGES = [
    ("follower-count-checker", "https://inayaflow.ai/tools/tiktok-follower-count-checker", "tool", "free TikTok follower count checker (any @username, no login)", "checking a TikTok account's followers, likes and videos"),
    ("money-calculator", "https://inayaflow.ai/tools/tiktok-money-calculator", "tool", "free TikTok money calculator (estimates Creator Rewards earnings from views)", "how much TikTok pays creators"),
    ("shadowban-checker", "https://inayaflow.ai/tools/tiktok-shadowban-checker", "tool", "free TikTok shadowban self-check (compares recent views with usual views)", "sudden drops in TikTok views"),
    ("hashtag-generator", "https://inayaflow.ai/tools/tiktok-hashtag-generator", "tool", "free TikTok hashtag generator (topic to up to 5 hashtags)", "choosing TikTok hashtags"),
    ("hashtag-counter", "https://inayaflow.ai/tools/tiktok-hashtag-counter", "tool", "free TikTok hashtag counter and caption length checker", "TikTok hashtag limit and caption length"),
    ("coins-to-usd", "https://inayaflow.ai/tools/tiktok-coins-to-usd", "tool", "free TikTok coins to USD calculator", "TikTok coins, gifts and what they are worth"),
    ("engagement-rate-calculator", "https://inayaflow.ai/tools/tiktok-engagement-rate-calculator", "tool", "free TikTok engagement rate calculator", "TikTok engagement rate"),
    ("best-time-to-post", "https://inayaflow.ai/tools/best-time-to-post-on-tiktok-today", "tool", "free tool showing the best time to post on TikTok today", "when to post on TikTok"),
    ("username-generator", "https://inayaflow.ai/tools/tiktok-username-generator", "tool", "free TikTok username generator", "choosing a TikTok username"),
    ("guide-hashtag-strategy", "https://inayaflow.ai/blog/tiktok-hashtag-strategy", "guide", "our guide to TikTok hashtag strategy", "TikTok hashtag strategy"),
    ("guide-trending-hashtags", "https://inayaflow.ai/blog/trending-tiktok-hashtags", "guide", "our guide to trending TikTok hashtags", "trending TikTok hashtags"),
    ("guide-creator-rewards", "https://inayaflow.ai/blog/tiktok-creator-rewards-program", "guide", "our guide to the TikTok Creator Rewards Program", "TikTok Creator Rewards Program"),
    ("guide-shadowban", "https://inayaflow.ai/blog/tiktok-shadowban", "guide", "our guide to TikTok shadowbans", "TikTok shadowbans and view drops"),
    ("guide-best-time", "https://inayaflow.ai/blog/best-time-to-post-on-tiktok", "guide", "our guide to the best time to post on TikTok", "when to post on TikTok"),
    ("guide-how-much-pay", "https://inayaflow.ai/blog/how-much-does-tiktok-pay", "guide", "our guide to how much TikTok pays", "how much TikTok pays creators"),
    ("guide-how-many-views", "https://inayaflow.ai/blog/how-many-views-is-good-on-tiktok", "guide", "our guide to how many views is good on TikTok", "TikTok view benchmarks"),
    ("guide-how-often-post", "https://inayaflow.ai/blog/how-often-should-you-post-on-tiktok", "guide", "our guide to how often to post on TikTok", "TikTok posting frequency"),
    ("guide-captions", "https://inayaflow.ai/blog/tiktok-captions", "guide", "our guide to TikTok captions", "writing TikTok captions"),
    ("guide-analytics", "https://inayaflow.ai/blog/tiktok-analytics", "guide", "our guide to TikTok analytics", "reading TikTok analytics"),
    ("guide-video-size-length", "https://inayaflow.ai/blog/tiktok-video-size-and-length", "guide", "our guide to TikTok video size and length", "TikTok video size, length and format"),
]

BANNED = ["delve", "game-changer", "game changer", "in today's fast-paced", "in today's digital", "unlock the power", "unlock your", "dive into", "dive deep",
          "navigate the", "landscape", "tapestry", "elevate your", "supercharge", "revolutionize", "seamlessly", "cutting-edge", "in conclusion", "it's important to note",
          "it is important to note", "ever-evolving", "ever-changing", "look no further", "take your", "to the next level", "harness the", "embark on", "realm of",
          "testament to", "stands as", "a must-have", "leverage"]
STAT_RE = re.compile(r"(\$|£|€)\s?\d[\d,\.]*|\d[\d,\.]*\s?(%|percent\b|million\b|billion\b|thousand\b|k\b|seconds?\b|minutes?\b|hours?\b|days?\b|hashtags?\b|characters?\b|followers?\b|views?\b|likes?\b|coins?\b)", re.I)
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


# ---------- state ----------

def read_json(path, default):
    try:
        return json.load(open(path, encoding="utf8"))
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path, "w", encoding="utf8"), indent=1, sort_keys=True)


def utc_today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def pick_page(state, today, pages=PAGES):
    """Oldest-used page that has rested REST_DAYS (never used = first). None when every page is still resting."""
    d0 = datetime.date.fromisoformat(today)
    last = {e["slug"]: e["date"] for e in state.get("posts", []) if e.get("slug") and e.get("date")}
    ok = []
    for p in pages:
        used = last.get(p[0])
        if used and (d0 - datetime.date.fromisoformat(used)).days < REST_DAYS:
            continue
        ok.append((used or "0000-00-00", pages.index(p), p))
    ok.sort(key=lambda x: (x[0], x[1]))
    return ok[0][2] if ok else None


# ---------- checks (offline) ----------

def words(text):
    return re.findall(r"[A-Za-z0-9']+", text)


def norm_num(s):
    return re.sub(r"[^\d.]", "", s).strip(".")


def seven_word_overlap(article, reference, n=7):
    """First n-word sequence the article shares with the reference text, or ''. Links and URLs are stripped first."""
    def toks(t):
        t = re.sub(r"https?://\S+", " ", t)
        return [w.lower() for w in re.findall(r"[A-Za-z0-9']+", t)]
    a, r = toks(article), toks(reference)
    grams = {" ".join(r[i:i + n]) for i in range(len(r) - n + 1)}
    for i in range(len(a) - n + 1):
        g = " ".join(a[i:i + n])
        if g in grams:
            return g
    return ""


def check_article(art, target_url, page_text, source_texts, first_person_ok=False):
    """-> list of problems (empty = passes the offline gate). art = dict(title, description, tags, body, sources).
    source_texts = {url: fetched text} for every external link in the body (None/'' = could not be fetched)."""
    p = []
    body, title = art.get("body", ""), art.get("title", "")
    wc = len(words(re.sub(r"```.*?```", " ", body, flags=re.S)))
    if not (MIN_WORDS <= wc <= MAX_WORDS):
        p.append("words %d outside %d-%d" % (wc, MIN_WORDS, MAX_WORDS))
    if not title or len(title) > 70:
        p.append("title missing or over 70 characters")
    tags = art.get("tags") or []
    if not (1 <= len(tags) <= 4) or any(not re.fullmatch(r"[a-z0-9]{1,30}", t) for t in tags):
        p.append("tags must be 1-4 lowercase letters/digits")
    links = LINK_RE.findall(body)
    ours = [u for _, u in links if "inayaflow.ai" in u]
    if ours.count(target_url) != 1:
        p.append("target link missing or repeated (%d)" % ours.count(target_url))
    if len(ours) > 2:
        p.append("more than 2 inayaflow.ai links")
    if re.search(r"inayaflow\.ai", re.sub(r"\]\(https?://[^)\s]+\)", "]", body)):
        p.append("bare inayaflow.ai text outside a link")
    ext = [u for _, u in links if "inayaflow.ai" not in u]
    if len(set(ext)) < 2:
        p.append("fewer than 2 external source links")
    allowed = {u.rstrip("/") for u in (art.get("sources") or [])}
    for u in set(ext):
        if u.rstrip("/") not in allowed:
            p.append("linked source not in the writer's source list")
            break
        if not source_texts.get(u):
            p.append("a linked source could not be opened")
            break
    low = body.lower() + " " + title.lower()
    hit = [b for b in BANNED if b in low]
    if hit:
        p.append("banned phrase: %s" % hit[0])
    if re.search(r"(?<![\w/.])@(?!inayaflow\b)[A-Za-z0-9_.]{2,}", re.sub(r"```.*?```", " ", body, flags=re.S)):
        p.append("contains an @handle")
    if not first_person_ok and re.search(r"\b(I|I'm|I've|I'll|my|me)\b", re.sub(r"```.*?```", " ", body, flags=re.S)):
        p.append("first person singular (use 'we')")
    corpus = " ".join(list(source_texts.values()) + [page_text])
    corpus_nums = {norm_num(m) for m in re.findall(r"\d[\d,\.]*\d|\d", corpus)}
    for para in re.split(r"\n\s*\n", re.sub(r"```.*?```", " ", body, flags=re.S)):
        if para.lstrip().startswith("#"):
            continue
        for m in STAT_RE.finditer(para):
            num = norm_num(m.group(0))
            if not num:
                continue
            if not LINK_RE.search(para):
                p.append("number '%s' in a paragraph with no source link" % m.group(0).strip()[:30])
            elif num not in corpus_nums:
                p.append("number '%s' not found in the fetched sources" % m.group(0).strip()[:30])
    ov = seven_word_overlap(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body), page_text)
    if ov:
        p.append("7-word overlap with the inayaflow.ai page")
    return p[:12]


# ---------- Anthropic (raw HTTP, metered) ----------

class Meter:
    def __init__(self):
        self.cost = 0.0
        self.calls = 0
        self.searches = 0

    def add(self, model, usage):
        u = usage or {}
        pin, pout, pcr = PRICES[model]
        fresh, cr, cw, out = (int(u.get(k, 0) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        s = int(((u.get("server_tool_use") or {}).get("web_search_requests", 0)) or 0)
        self.cost += (fresh * pin + cr * pcr + cw * pin * 1.25 + out * pout) / 1e6 + s * SEARCH_PRICE
        self.searches += s
        self.calls += 1


def _post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def anthropic(model, system, user, meter, post=None, tools=None, max_tokens=6000, messages=None):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and post is None:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": messages or [{"role": "user", "content": user}]}
    if tools:
        body["tools"] = tools
    headers = {"x-api-key": key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}
    data = (post or _post_json)("https://api.anthropic.com/v1/messages", body, headers)
    meter.add(model, data.get("usage"))
    return data


def run_writer(system, user, meter, post=None):
    """Sonnet with web search + fetch. Handles pause_turn. Returns the final text. Stops when the cost cap is near."""
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES},
             {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 4, "max_content_tokens": 10000}]
    messages = [{"role": "user", "content": user}]
    text = ""
    for _ in range(6):
        if meter.cost >= USD_CAP - 0.12:
            raise RuntimeError("cost cap reached before the article was finished")
        data = anthropic(WRITER, system, None, meter, post=post, tools=tools, max_tokens=6000, messages=messages)
        content = data.get("content") or []
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text") or text
        if data.get("stop_reason") == "refusal":
            raise RuntimeError("refusal")
        if data.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": content})
            continue
        return text
    raise RuntimeError("writer did not finish")


WRITER_SYSTEM = """You write one article for DEV (dev.to) for the company InayaFlow. Voice: "we" (never I, me, my). Plain, friendly, specific English.
RULES (all are checked by code afterwards; a break means nothing is published):
- 700 to 950 words in Markdown. A NEW angle: a practical question a creator or developer really has. Do not summarise or copy the InayaFlow page; add your own examples and reasoning.
- Real numbers only: use web_search / web_fetch to find them in official or primary sources (TikTok help pages, research, platform docs). EVERY paragraph that contains a number, percent or dollar amount must contain a Markdown link to the page where that number is printed, and the number must appear on that page exactly as you write it. No invented, rounded-up or remembered statistics. If you cannot source a number, leave it out.
- At least 2 different external source links. Use only pages you opened.
- Exactly ONE link to the target InayaFlow page (given below), placed where it truly helps the reader, written as [descriptive words](URL). No other inayaflow.ai link except at most one more if truly useful. Never write the bare text inayaflow.ai. Do not describe features of our page that are not in the PAGE TEXT below.
- No @usernames, no other creators' names, no hype, no calls like "sign up now". Be honest about limits (our tools are free helpers, not magic).
- Banned words and phrases: delve, game-changer, landscape, tapestry, dive into, unlock, elevate, supercharge, revolutionize, seamlessly, cutting-edge, in conclusion, it's important to note, ever-evolving, look no further, to the next level, harness, embark, realm of, testament, leverage, must-have, take your ... to the next level.
- Title: 70 characters or fewer, no clickbait. Four DEV tags (lowercase letters/digits only, e.g. tiktok, socialmedia, webdev, productivity).
- Use headings (##), short paragraphs, one small list or table where it helps.
OUTPUT FORMAT, exactly, nothing before or after:
TITLE: <title>
DESCRIPTION: <one sentence, 150 characters or fewer>
TAGS: tag1,tag2,tag3,tag4
SOURCES: <url1> | <url2> | <url3>
===BODY===
<markdown body, no title line>"""


def parse_writer(text):
    m = re.search(r"TITLE:\s*(.+)\nDESCRIPTION:\s*(.+)\nTAGS:\s*(.+)\nSOURCES:\s*(.+)\n===BODY===\n(.*)", text or "", re.S)
    if not m:
        return None
    return {"title": m.group(1).strip(), "description": m.group(2).strip()[:200],
            "tags": [t.strip().lower().lstrip("#") for t in m.group(3).split(",") if t.strip()][:4],
            "sources": [u.strip() for u in re.split(r"\s*\|\s*", m.group(4)) if u.strip().startswith("http")],
            "body": m.group(5).strip()}


QA_SYSTEM = """You are a strict fact-check editor. You get an article and the text of the pages it links to. Answer ONLY a JSON object:
{"verdict":"PASS" or "FAIL","reasons":["..."]}
FAIL if ANY of: a number, percent, date or factual claim is not supported by the linked page text given; a claim sounds invented or is attributed to a source that does not say it;
it names another creator, brand-new product claim or @handle; it promises results the tool cannot give; the InayaFlow link is pushy or off-topic; it reads as an advert instead of a useful article; it makes a legal, medical or financial claim without a source.
PASS only when every checkable claim is supported."""


def qa_gate(art, target_url, source_texts, page_text, meter, post=None):
    srcs = "\n\n".join("SOURCE %s:\n%s" % (u, (t or "")[:3500]) for u, t in source_texts.items())
    user = "TARGET PAGE %s (our own page):\n%s\n\n%s\n\nARTICLE TITLE: %s\n\n%s" % (target_url, page_text[:2500], srcs, art["title"], art["body"])
    data = anthropic(QA_MODEL, QA_SYSTEM, user[:60000], meter, post=post, max_tokens=700)
    raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    j = c.json_obj(raw)
    if not j or j.get("verdict") not in ("PASS", "FAIL"):
        return False, ["QA answer not understood"]
    return j["verdict"] == "PASS", [str(r)[:160] for r in (j.get("reasons") or [])][:5]


# ---------- DEV ----------

def publish(art, api_key, post=None):
    """POST https://dev.to/api/articles. canonical_url is NOT sent. Returns the response dict."""
    body = {"article": {"title": art["title"], "body_markdown": art["body"], "published": True,
                        "description": art.get("description", ""), "tags": art["tags"]}}
    headers = {"api-key": api_key, "accept": "application/vnd.forem.api-v1+json", "content-type": "application/json", "user-agent": UA}
    return (post or _post_json)(API, body, headers)


def link_rel(html, target_url):
    """rel attribute of OUR link in the live page HTML: 'followed' / 'nofollow' / 'ugc' / 'sponsored' / 'missing'."""
    for m in re.finditer(r"<a\b[^>]*>", html or "", re.I):
        tag = m.group(0)
        h = re.search(r'href="([^"]+)"', tag)
        if h and h.group(1).replace("&amp;", "&").rstrip("/").split("?")[0] == target_url.rstrip("/"):
            r = re.search(r'rel="([^"]*)"', tag)
            rel = (r.group(1).lower() if r else "")
            for k in ("nofollow", "ugc", "sponsored"):
                if k in rel:
                    return k
            return "followed"
    return "missing"


# ---------- main ----------

def log(line):
    os.makedirs(OPS, exist_ok=True)
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf8") as f:
        if new:
            f.write("# DEV poster log (private)\n\n| date (UTC) | page | result | article URL | our link | cost | note |\n|---|---|---|---|---|---|---|\n")
        f.write(line.rstrip() + "\n")


def run(post_ai=None, post_dev=None, getter=None, sleeper=time.sleep):
    getter = getter or c.http_get
    today = utc_today()
    state = read_json(STATE_PATH, {"posts": [], "attempts": {}})
    meter = Meter()
    if any(e.get("date") == today and e.get("url") for e in state["posts"]):
        print("dev_poster: already published today, nothing to do")
        return "ALREADY_DONE"
    tries_today = state.get("attempts", {}).get(today, 0)
    if tries_today >= 2 and not DRY:
        print("dev_poster: 2 failed attempts today, stop (the Director looks at the drafts)")
        return "STOPPED"
    page = pick_page(state, today)
    if not page:
        print("dev_poster: every page is resting, nothing to write")
        return "RESTING"
    slug, url, kind, what, topic = page
    st, _, html = getter(url)
    page_text = c.html_text(html, 6000) if st == 200 else ""
    if st != 200 or len(page_text) < 200:
        print("dev_poster: target page not readable (HTTP %s), stop" % st)
        log("| %s | %s | STOP page not readable | | | $0 | HTTP %s |" % (today, slug, st))
        return "PAGE_DOWN"
    past = [e.get("title", "") for e in state["posts"][-30:] if e.get("title")]
    user = ("TARGET PAGE: %s\nIT IS: %s (%s)\nTOPIC AREA: %s\nPAGE TEXT (the only facts you may state about our page):\n%s\n\n"
            "ARTICLES WE ALREADY PUBLISHED (do not repeat these angles):\n%s\n\nWrite today's article now.") % (
        url, what, kind, topic, page_text, "\n".join("- " + t for t in past) or "(none yet)")
    problems, art, source_texts = ["no draft"], None, {}
    for attempt in (1, 2):
        if attempt == 2 and meter.cost > RETRY_BELOW:
            break
        extra = "" if attempt == 1 else "\n\nYOUR LAST DRAFT FAILED THESE CHECKS, fix them: " + "; ".join(problems)
        try:
            art = parse_writer(run_writer(WRITER_SYSTEM, user + extra, meter, post=post_ai))
        except Exception as e:
            problems = ["writer error %s" % type(e).__name__]
            break
        if not art:
            problems = ["writer output not in the required format"]
            continue
        ext = sorted({u for _, u in LINK_RE.findall(art["body"]) if "inayaflow.ai" not in u})[:8]
        source_texts = {}
        for u in ext:
            s2, _, h2 = getter(u)
            source_texts[u] = c.html_text(h2, 20000) if s2 == 200 else ""
        problems = check_article(art, url, page_text, source_texts)
        sl, _, _ = getter(url)
        if sl != 200:
            problems.append("target link not live (HTTP %s)" % sl)
        if not problems:
            break
    ok = not problems
    reasons = problems
    if ok:
        try:
            ok, reasons = qa_gate(art, url, source_texts, page_text, meter, post=post_ai)
        except Exception as e:
            ok, reasons = False, ["QA error %s" % type(e).__name__]
    state.setdefault("attempts", {})[today] = tries_today + 1
    cost = "$%.3f" % meter.cost
    if not ok:
        if art:
            os.makedirs(DRAFT_DIR, exist_ok=True)
            open(os.path.join(DRAFT_DIR, "%s_%s.md" % (today, slug)), "w", encoding="utf8").write(
                "# FAILED DRAFT (not published)\n\nReasons: %s\n\nTitle: %s\n\n%s\n" % ("; ".join(reasons), art["title"], art["body"]))
        log("| %s | %s | FAIL, not published | | | %s | %s |" % (today, slug, cost, "; ".join(reasons)[:300]))
        write_json(STATE_PATH, state)
        print("dev_poster: FAIL page=%s problems=%d cost=%s" % (slug, len(reasons), cost))
        return "FAIL"
    if DRY:
        os.makedirs(DRAFT_DIR, exist_ok=True)
        open(os.path.join(DRAFT_DIR, "%s_%s_DRYRUN.md" % (today, slug)), "w", encoding="utf8").write(
            "# DRY RUN (not published)\n\nTitle: %s\nTags: %s\n\n%s\n" % (art["title"], ",".join(art["tags"]), art["body"]))
        log("| %s | %s | DRY RUN passed all checks, not published | | | %s | |" % (today, slug, cost))
        state["attempts"][today] = tries_today          # a dry run does not use up a real attempt
        write_json(STATE_PATH, state)
        print("dev_poster: DRY RUN ok page=%s cost=%s (publish endpoint NOT called)" % (slug, cost))
        return "DRY_OK"
    key = os.environ.get("DEV_API_KEY")
    if not key:
        print("dev_poster: no DEV_API_KEY, stop")
        return "NO_KEY"
    try:
        res = publish(art, key, post=post_dev)
    except urllib.error.HTTPError as e:
        log("| %s | %s | DEV refused HTTP %s | | | %s | %s |" % (today, slug, e.code, cost, "429 = rate limit; retried next run" if e.code == 429 else ""))
        write_json(STATE_PATH, state)
        print("dev_poster: DEV answered HTTP %s" % e.code)
        return "DEV_ERROR"
    except Exception as e:
        log("| %s | %s | DEV error %s | | | %s | |" % (today, slug, type(e).__name__, cost))
        write_json(STATE_PATH, state)
        print("dev_poster: DEV error %s" % type(e).__name__)
        return "DEV_ERROR"
    art_url = res.get("url") or ""
    sleeper(20)
    s3, _, live = getter(art_url) if art_url else (0, "", "")
    rel = link_rel(live, url) if s3 == 200 else "unknown (page HTTP %s)" % s3
    state["posts"].append({"date": today, "slug": slug, "title": art["title"], "url": art_url, "id": res.get("id"), "target": url, "rel": rel})
    log("| %s | %s | PUBLISHED | %s | %s | %s | |" % (today, slug, art_url, rel, cost))
    write_json(STATE_PATH, state)
    print("dev_poster: PUBLISHED page=%s link=%s cost=%s" % (slug, rel, cost))
    return "PUBLISHED"


if __name__ == "__main__":
    run()
