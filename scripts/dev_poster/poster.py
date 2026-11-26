#!/usr/bin/env python3
"""DEV POSTER (23 Nov 2026): ONE useful, original article a day on DEV (dev.to, account @inayaflow) through the official Forem API.

Imran 23 Nov: "do the API keys tomorrow first" -> cloud API-posting robot, one useful 600+ word post per tool/guide,
contextual followed link, indexed.

Flow: pick the page that was not used for 14 days -> read it on inayaflow.ai -> Claude Sonnet (web search + fetch) writes the
article -> offline checks (words, title, tags, link, numbers vs the fetched sources, 7-word overlap, banned phrases, handles)
-> Claude Haiku QA reads the article with the fetched source texts -> publish (POST https://dev.to/api/articles) -> read the
live page and record whether OUR link is followed. FAIL = nothing is published and the draft goes to the PRIVATE repo.

HARD LIMITS (QA round 4): $0.60 a UTC DAY in total (every run, dry or live, writer + QA + retry: the cost is checked BEFORE each model call with a
reserve for that call and for the QA call that must follow; the day's spend is kept in the state file); at most 2 dry runs a day; one article a day
(DEV itself is asked "what did we publish today?" before and a pending marker is written before the POST); nothing runs unless the private state was
restored (DEV_STATE_OK); nothing is published unless BOTH the run is not dry AND the repository switch DEV_POSTER_LIVE is "true" (also for manual runs).

PUBLIC repo: stdout = page slug, status words, counts, cost. Never the key, never article text. Everything else goes to the
PRIVATE repo (imran0786/genie, branch ops-dev-poster) through the workflow. canonical_url is never set (our own article).
DEV's Terms (section 11): content must be on-topic, high-quality and "not designed primarily for the purposes of promotion or
creating backlinks" -> one article a day, real value first, one contextual link (two at most).
"""
import datetime, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts", "reports"))
import common as c  # noqa: E402  (http_get, html_text, json_obj)
import targets as tg  # noqa: E402  (QUICK_WIN_PATHS: the one shared quick-win list; Director updates weekly from Search Console, last 24 Nov 2026)

OPS = os.environ.get("DEV_OPS_DIR", os.path.join(ROOT, "ops"))
STATE_PATH = os.path.join(OPS, "dev_poster_state.json")
LOG_PATH = os.path.join(OPS, "dev_poster_log.md")
DRAFT_DIR = os.path.join(OPS, "dev_drafts")
DRY = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
WRITER = os.environ.get("DEV_WRITER_MODEL", "claude-sonnet-5-5")
QA_MODEL = os.environ.get("DEV_QA_MODEL", "claude-haiku-5-5")
DAY_CAP = float(os.environ.get("DEV_DAY_USD_CAP", "0.60"))        # HARD cap per UTC day over ALL runs (dry and live), writer + QA + retry
USD_CAP = DAY_CAP                                                  # (older name, same value)
WRITER_RESERVE = 0.20   # most one Sonnet request can cost: <= ~45k tokens in x $2/M (prompt + 3 searches + 3 pages of <= 6000 tokens) + 3500 out x $10/M + 3 searches x $0.01 = ~$0.16
QA_RESERVE = 0.02       # most the Haiku QA request can cost: <= 20k tokens in x $0.10/M + 12000 out (the retry) x $0.50/M = ~$0.008, rounded up
RETRY_BELOW = DAY_CAP - WRITER_RESERVE - QA_RESERVE                # a 2nd writer try only when it can still be paid AND still be QA-checked
MAX_DRY_PER_DAY = 2
MIN_SOURCE_CHARS = 300      # a fetched page with less text than this (e.g. only "TikTok Support") is "unreadable": it cannot back a number
PAST_ARTICLES_KEPT = 30                                            # bodies whose 7-word shingles are remembered for the similarity check
MAX_SEARCHES = int(os.environ.get("DEV_MAX_SEARCHES", "3"))        # was 5 (23 Nov dry run 2 cost $0.433)
MAX_FETCHES = 3                                                    # was 4
FETCH_TOKENS = 6000                                                # per fetched page, was 10000
WRITER_MAX_TOKENS = 8000                                           # 700-950 words are ~1,300 tokens + Sonnet's thinking (26 Nov: 3500 -> 8000; 45k in x $2/M + 8000 x $10/M + 3 searches = $0.20 = WRITER_RESERVE)
WRITER_EFFORT = os.environ.get("DEV_WRITER_EFFORT", "medium")      # 26 Nov: set explicitly (Sonnet 5.5 default is high); same as the platform poster writer
# 26 Nov: claude-haiku-5-5 THINKS by default and the thinking counts toward max_tokens: the old QA max_tokens=700 often came back with
# NO text ("QA answer not understood"). Effort is now explicit and the QA call has room to think; an empty reply is logged and asked
# ONCE more with twice the room (12000 x $0.50/M = $0.006, inside QA_RESERVE).
QA_MAX_TOKENS = int(os.environ.get("DEV_QA_MAX_TOKENS", "6000"))
QA_EFFORT = os.environ.get("DEV_QA_EFFORT", "medium")              # same as the platform poster QA
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
    ("guide-no-views", "https://inayaflow.ai/blog/why-is-my-tiktok-getting-no-views", "guide", "our guide to why a TikTok gets no views", "why a TikTok video gets no views"),
    ("guide-views-going-down", "https://inayaflow.ai/blog/why-are-my-tiktok-views-going-down", "guide", "our guide to why TikTok views go down", "TikTok views going down"),
    ("guide-hooks", "https://inayaflow.ai/blog/tiktok-hooks-that-stop-the-scroll", "guide", "our guide to TikTok hooks that stop the scroll", "TikTok video hooks"),
    ("guide-growth-tool", "https://inayaflow.ai/blog/tiktok-growth-tool", "guide", "our guide to choosing a TikTok growth tool", "TikTok growth tools"),
    ("analyze", "https://inayaflow.ai/analyze", "tool", "free TikTok account analyzer: paste any @username for a full report", "analysing a TikTok account"),
]

# ---------- push priority (24 Nov 2026): data/push_priority.json, edited by the Director, no code change ----------
PRIORITY_PATH = os.path.join(ROOT, "data", "push_priority.json")


def load_priority(pages=None, path=None):
    """-> {slug: tier index (0 = tier 1)} for slugs that are in `pages`. Unknown slugs are ignored with a printed warning; missing/unreadable/bad file = {} (old behaviour)."""
    pages = PAGES if pages is None else pages
    d = read_json(path or PRIORITY_PATH, None)
    tiers = d.get("tiers") if isinstance(d, dict) else None
    if not isinstance(tiers, list):
        return {}
    known = {p[0] for p in pages}
    out = {}
    for ti, tier in enumerate(tiers):
        if not isinstance(tier, list):
            continue
        for slug in tier:
            if slug not in known:
                print("WARNING: push_priority.json slug not in PAGES, ignored: %s" % (slug,))
            elif slug not in out:
                out[slug] = ti
    return out


def tier_of(slug, prio, pages=None):
    """Tier index; pages not listed come after every listed tier (tier 3 by default)."""
    return prio.get(slug, (max(prio.values()) + 1) if prio else 0)

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
    """Highest-priority tier first (data/push_priority.json), then oldest-used page that has rested REST_DAYS (never used = first). None when every page is still resting."""
    d0 = datetime.date.fromisoformat(today)
    last = {e["slug"]: e["date"] for e in state.get("posts", []) if e.get("slug") and e.get("date")}
    ok = []
    prio = load_priority(pages)
    if not prio:     # no Director tier file: the quick-win pages (shared list in targets.py) are tier 1, everything else tier 2 (tools stay in, they just come after)
        prio = {p[0]: 0 for p in pages if tg.is_quick_win(p[1])}
    for p in pages:
        used = last.get(p[0])
        if used and (d0 - datetime.date.fromisoformat(used)).days < REST_DAYS:
            continue
        ok.append((tier_of(p[0], prio), used or "0000-00-00", pages.index(p), p))
    ok.sort(key=lambda x: x[:3])
    return ok[0][3] if ok else None


# ---------- link text mix (RULEBOOK 9a, 24 Nov 2026): the visible text of our ONE link follows a 10-post cycle PER TARGET PAGE ----------
ANCHOR_CYCLE = ["brand", "plain", "address", "partial", "brand", "address", "plain", "partial", "brand", "exact"]     # 3 brand, 2 address, 2 plain, 2 partial, 1 exact; no type twice in a row
HOME_CYCLE = ["brand", "address"]                                                                                      # homepage: brand or address only
ANCHOR_KEYWORDS = {
    "follower-count-checker": "TikTok followers count", "money-calculator": "TikTok money calculator",
    "shadowban-checker": "TikTok shadowban checker", "hashtag-generator": "TikTok hashtag generator",
    "hashtag-counter": "TikTok hashtag counter", "coins-to-usd": "TikTok coins to USD",
    "engagement-rate-calculator": "TikTok engagement rate calculator", "best-time-to-post": "best time to post on TikTok",
    "username-generator": "TikTok username generator",
    "guide-hashtag-strategy": "TikTok hashtag strategy", "guide-trending-hashtags": "trending TikTok hashtags",
    "guide-creator-rewards": "TikTok Creator Rewards Program", "guide-shadowban": "TikTok shadowban",
    "guide-best-time": "best time to post on TikTok", "guide-how-much-pay": "how much does TikTok pay",
    "guide-how-many-views": "how many views is good on TikTok", "guide-how-often-post": "how often to post on TikTok",
    "guide-captions": "TikTok captions", "guide-analytics": "TikTok analytics", "guide-video-size-length": "TikTok video size and length",
    "guide-no-views": "why is my TikTok getting no views", "guide-views-going-down": "why are my TikTok views going down",
    "guide-hooks": "TikTok hooks", "guide-growth-tool": "TikTok growth tool", "analyze": "TikTok account analyzer",
    "homepage": "InayaFlow",
}
ANCHOR_STOP = {"a", "an", "the", "to", "on", "of", "for", "is", "in", "and", "how", "does", "often", "much", "many"}


def anchor_count(state, slug):
    """The per-target cursor: how many posts this robot's state already books for the target. A post is booked only when it is actually published
    (or recovered from a pending marker), so the cursor moves exactly then; dry runs, failures and refused POSTs never move it."""
    return sum(1 for e in state.get("posts", []) if e.get("slug") == slug)


def anchor_type(n, slug):
    return HOME_CYCLE[n % 2] if slug == "homepage" else ANCHOR_CYCLE[n % 10]


def anchor_keyword(slug, url=""):
    if slug in ANCHOR_KEYWORDS:
        return ANCHOR_KEYWORDS[slug]
    tail = urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"[-_]+", " ", tail).strip() or "InayaFlow"


def anchor_url_text(url):
    return re.sub(r"^https?://", "", url).rstrip("/")


def _kw_words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in ANCHOR_STOP}


def anchor_info(slug, url, n):
    """What the writer is told and what the check enforces for this post."""
    t = anchor_type(n, slug)
    kw = anchor_keyword(slug, url)
    addr = anchor_url_text(url)
    kws = kw.split()
    if t == "brand":
        desc, ex = "BRAND: the link text must contain the word InayaFlow", ["InayaFlow", "InayaFlow's free tool for this"]
    elif t == "address":
        desc, ex = "WEB ADDRESS: the link text must be exactly the web address %s (nothing else, no https://)" % addr, [addr]
    elif t == "plain":
        desc, ex = "PLAIN WORDS: generic link text with no brand name and none of the page's keyword words", ["this free checker", "a free tool for it"]
    elif t == "partial":
        desc, ex = ("PARTIAL KEYWORD: a natural phrase that uses SOME but not ALL of the words of the page keyword \"%s\", no brand name" % kw), ["a free %s" % " ".join(kws[-2:]), "this %s tool" % kws[-1]]
    else:
        desc, ex = "EXACT KEYWORD: the link text must be exactly \"%s\"" % kw, [kw]
    return {"type": t, "keyword": kw, "url_text": addr, "desc": desc, "examples": ex}


def anchor_prompt(info):
    return "LINK TEXT FOR THE ONE LINK TO THE TARGET PAGE: %s. Examples: %s. Use only this type; the code rejects any other." % (
        info["desc"], "; ".join('"%s"' % e for e in info["examples"]))


def anchor_problems(text, info):
    """-> list of problems (empty = the link text matches the chosen type)."""
    t, kw = info["type"], info["keyword"]
    low = text.strip().lower()
    kww = _kw_words(kw)
    tw = _kw_words(text)
    shared = len(kww & tw)
    brand = "inayaflow" in low
    if t == "brand":
        ok = brand
    elif t == "address":
        ok = low.rstrip("/") == info["url_text"].lower()
    elif t == "plain":
        ok = not brand and bool(tw or low) and shared * 2 < len(kww) and "." not in low.replace(" ", "")
    elif t == "partial":
        ok = not brand and shared >= 1 and not kww <= tw and "." not in low.replace(" ", "")
    else:
        ok = low == kw.lower()
    return [] if ok else ["link text '%s' is not the required %s type (%s)" % (text.strip()[:60], t, info["desc"][:80])]


# ---------- checks (offline) ----------

def words(text):
    return re.findall(r"[A-Za-z0-9']+", text)


def norm_num(s):
    return re.sub(r"[^\d.]", "", s).strip(".")


def seven_word_overlap(article, reference, n=7):  # n: shared-run length in words (default 7)
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


def shingles(text, n=7):
    """Set of short hashes of every n-word sequence (links and URLs stripped): what the state keeps of an article."""
    import hashlib
    t = re.sub(r"https?://\S+", " ", re.sub(r"```.*?```", " ", text, flags=re.S))
    w = [x.lower() for x in re.findall(r"[A-Za-z0-9']+", t)]
    return {hashlib.md5(" ".join(w[i:i + n]).encode()).hexdigest()[:10] for i in range(len(w) - n + 1)}


def check_article(art, target_url, page_text, source_texts, first_person_ok=False, past_shingles=None, page_full=None, anchor=None):
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
    scan = re.sub(r"\[[^\]]*\]\(" + re.escape(target_url) + r"\)", " LINK ", body) if anchor else body      # the address as LINK TEXT is allowed; bare unlinked text never
    if re.search(r"inayaflow\.ai", re.sub(r"\]\(https?://[^)\s]+\)", "]", scan)):
        p.append("bare inayaflow.ai text outside a link")
    if anchor:
        txt = [t for t, u in links if u == target_url]
        if len(txt) == 1:
            p += anchor_problems(txt[0], anchor)
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
    if not first_person_ok and re.search(r"\b(I|I'm|I've|I'll|I'd|my|me|mine|myself)\b", re.sub(r"```.*?```", " ", body, flags=re.S)):
        p.append("first person singular (use 'we')")
    own = page_full if page_full else page_text            # OUR page, whole (the prompt only shows the first 6000 characters): numbers about our own tool are checked against it
    corpus = " ".join(list(source_texts.values()) + [own])
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
    ov = seven_word_overlap(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body), own)
    if ov:
        p.append("7-word overlap with the inayaflow.ai page: '%s' (rewrite that sentence in your own words)" % ov)
    # promotional density (DEV terms s.11: "not designed primarily for the purposes of promotion or creating backlinks"):
    paras = [x for x in re.split(r"\n\s*\n", re.sub(r"```.*?```", " ", body, flags=re.S)) if x.strip() and not x.lstrip().startswith("#")]
    at = [i for i, x in enumerate(paras) if target_url in x]
    if at and (at[0] == 0 or at[-1] == len(paras) - 1):
        p.append("our link is in the first or the last paragraph (reads as an ad)")
    if len(re.findall(r"inayaflow", re.sub(r"\]\(https?://[^)\s]+\)", "]", body), re.I)) > 3:
        p.append("the InayaFlow name appears more than 3 times")
    if past_shingles:
        mine = shingles(body)
        shared = len(mine & set(past_shingles))
        if shared >= 8 or (mine and shared / len(mine) >= 0.03):
            p.append("too similar to our earlier DEV articles (%d shared 7-word runs)" % shared)
    return p[:12]


# ---------- Anthropic (raw HTTP, metered) ----------

class CapReached(RuntimeError):
    pass


class Meter:
    def __init__(self, cap=None):
        self.cap = DAY_CAP if cap is None else cap
        self.cost = 0.0
        self.calls = 0
        self.searches = 0
        self.hook = None          # called after every change of cost: run() sets it to "save the day's spend to the state file"

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


def anthropic(model, system, user, meter, post=None, tools=None, max_tokens=6000, messages=None, extra=None):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and post is None:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    reserve = (WRITER_RESERVE + QA_RESERVE) if model == WRITER else QA_RESERVE      # a writer call must leave room for the QA call after it
    if meter.cost + reserve > meter.cap + 1e-9:
        raise CapReached("daily cost cap: %.3f spent + %.2f reserve > %.2f" % (meter.cost, reserve, meter.cap))
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": messages or [{"role": "user", "content": user}]}
    if tools:
        body["tools"] = tools
    if extra:                                     # e.g. {"output_config": {...}} (platform poster, 26 Nov): effort and a JSON schema for the reply
        body.update(extra)
    headers = {"x-api-key": key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}
    # BOOK the call's full reserve and SAVE it BEFORE the request goes out: a run that is killed or times out during the call still has it on record.
    own = WRITER_RESERVE if model == WRITER else QA_RESERVE
    meter.cost += own
    if meter.hook:
        meter.hook()
    try:
        data = (post or _post_json)("https://api.anthropic.com/v1/messages", body, headers)
    except BaseException:
        # a timeout / network error / HTTP error: the request MAY have run and been billed: the reserve stays booked (already saved)
        meter.calls += 1
        raise
    meter.cost -= own                                                  # the real, metered cost replaces the reserve
    meter.add(model, data.get("usage"))
    if meter.hook:
        meter.hook()
    return data


def harvest_fetched(content, out):
    """Texts the WRITER's own web_fetch really returned (tool output, not model words): {url: text}. Defensive about the block shape."""
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "web_fetch_tool_result":
            r = b.get("content") if isinstance(b.get("content"), dict) else {}
            doc = r.get("content") if isinstance(r.get("content"), dict) else {}
            src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
            text = src.get("data") or src.get("text") or ""
            url = r.get("url") or ""
            if url and isinstance(text, str) and text.strip():
                out[url] = text


def run_writer(system, user, meter, post=None, fetched=None):
    """Sonnet with web search + fetch. Handles pause_turn. Returns the final text. Stops when the cost cap is near.
    fetched (a dict, optional) is filled with {url: text} of every page the writer's own fetch tool returned."""
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES},
             {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": MAX_FETCHES, "max_content_tokens": FETCH_TOKENS}]
    messages = [{"role": "user", "content": user}]
    text = ""
    for _ in range(6):
        data = anthropic(WRITER, system, None, meter, post=post, tools=tools, max_tokens=WRITER_MAX_TOKENS, messages=messages,
                         extra={"output_config": {"effort": WRITER_EFFORT}})
        content = data.get("content") or []
        if fetched is not None:
            harvest_fetched(content, fetched)
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text") or text
        if data.get("stop_reason") == "refusal":
            raise RuntimeError("refusal")
        if data.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": content})
            continue
        if data.get("stop_reason") == "max_tokens" or not text.strip():
            print("dev_poster: writer reply %s" % no_text_note(data))
        return text
    raise RuntimeError("writer did not finish")


def no_text_note(data):
    """One log line for a reply that was cut or had no text: stop reason + token counts only (never the content)."""
    u = data.get("usage") or {}
    return "cut or empty (stop=%s, in=%s, out=%s tokens)" % (data.get("stop_reason"), u.get("input_tokens"), u.get("output_tokens"))


WRITER_SYSTEM = """You write one article for DEV (dev.to) for the company InayaFlow. Voice: "we" (never I, me, my, mine, myself). Plain, friendly, specific English.
RULES (all are checked by code afterwards; a break means nothing is published):
- 700 to 900 words in Markdown: a HARD limit, the checker stops anything over 1050 or under 600 words (code counts them), so aim for about 800 and stop there. A NEW angle: a practical question a creator or developer really has. Do not summarise or copy the InayaFlow page; add your own examples and reasoning.
- Real numbers only: use web_search / web_fetch to find them in official or primary sources (research, platform docs, newsrooms). EVERY paragraph that contains a number, percent or dollar amount must contain a Markdown link to the page where that number is printed, and the number must appear on that page exactly as you write it. No invented, rounded-up or remembered statistics. If you cannot source a number, leave it out.
- CITE ONLY WHAT YOU READ: a page may be cited only if you fetched it with web_fetch in THIS session and the text that came back visibly contains the number as you write it (never cite a page you only saw in search results). Pages on tiktok.com/support, support.tiktok.com and tiktok.com/creator-academy usually come back empty or with only a title: do NOT cite them and do not use a number whose only source is such a page. Our checker re-reads every cited page: a number it cannot find there stops the article.
- At least 2 different external source links in the body, as Markdown links [text](url), each to a page you opened: an article with fewer than 2 is thrown away. Put exactly one link to the target InayaFlow page in the middle of the article, never in the first or last paragraph.
- About OUR page: paraphrase it in your own words and never copy 7 or more words in a row from the PAGE TEXT (the checker compares). Facts about our tool (limits, hours, days, what it needs) may be stated ONLY if they are printed in the PAGE TEXT; the paragraph that links our page may use numbers from the PAGE TEXT without another source link.
- Never use the words I, I'm, I've, I'll, I'd, me, my, mine or myself anywhere: not in questions, quotes or rhetorical lines ("am I shadowbanned?" is forbidden: write "is an account shadowbanned?" or "are your views down?").
- Exactly ONE link to the target InayaFlow page (given below), placed where it truly helps the reader, written as [LINK TEXT](URL); the type of LINK TEXT is set per article in the user message and checked by code. No other inayaflow.ai link except at most one more if truly useful. Never write the web address inayaflow.ai as plain text outside that link (as the link text itself it is allowed when the type says so). Do not describe features of our page that are not in the PAGE TEXT below.
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
{"verdict":"PASS" or "FAIL","reasons":["\"exact sentence or phrase quoted from the article\" | why it is unsupported"]}
Write every reason in that form: the exact words from the article in double quotes, then a bar, then the reason (short).
FAIL if ANY of: a number, percent, date or factual claim is not supported by the linked page text given; a claim sounds invented or is attributed to a source that does not say it;
it names another creator, brand-new product claim or @handle; it promises results the tool cannot give; the InayaFlow link is pushy or off-topic; it reads as an advert instead of a useful article; it makes a legal, medical or financial claim without a source.
PASS only when every checkable claim is supported."""


def qa_json(raw):
    """First parseable JSON object in a model reply: skips code fences, leading/trailing prose and stray braces."""
    raw = re.sub(r"```(?:json)?", " ", raw or "")
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", raw):
        try:
            d, _ = dec.raw_decode(raw[m.start():])
        except ValueError:
            continue
        if isinstance(d, dict):
            return d
    return None


def qa_lenient(raw):
    """Last resort for a QA reply that is not clean JSON (cut off at the token limit, or prose). FAIL WINS: the word FAIL anywhere in the reply
    (a quoted "verdict":"FAIL" or a bare FAIL) makes it FAIL, even when a PASS appears first. PASS only when it is the sole verdict
    (a quoted "verdict":"PASS" or a reply that starts with PASS). Anything else -> None (the article then fails, as before)."""
    raw = raw or ""
    tail = raw.split('"reasons"', 1)[1] if '"reasons"' in raw else ""
    rs = [x[:160] for x in re.findall(r'"([^"]{6,})"', tail)]
    if re.search(r"(?i)\bFAIL\b", raw):
        return {"verdict": "FAIL", "reasons": rs or [raw.strip()[:160] or "QA reply was cut off"]}
    if re.search(r'"verdict"\s*:\s*"PASS"', raw, re.I) or re.match(r"(?i)\s*PASS\b", raw):
        return {"verdict": "PASS", "reasons": []}
    return None


def qa_gate(art, target_url, source_texts, page_text, meter, post=None):
    srcs = "\n\n".join("SOURCE %s:\n%s" % (u, (t or "")[:3500]) for u, t in source_texts.items())
    user = "TARGET PAGE %s (our own page):\n%s\n\n%s\n\nARTICLE TITLE: %s\n\n%s" % (target_url, page_text[:6000], srcs, art["title"], art["body"])
    j = None
    raw = ""
    for _try in (1, 2):                              # an unreadable QA reply gets ONE more ask (with twice the room) before it fails
        data = anthropic(QA_MODEL, QA_SYSTEM, user[:60000], meter, post=post, max_tokens=QA_MAX_TOKENS * _try,
                         extra={"output_config": {"effort": QA_EFFORT}})
        raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        j = qa_json(raw)
        if j and j.get("verdict") in ("PASS", "FAIL"):
            break
        print("dev_poster: QA reply unreadable, %s, %s" % (no_text_note(data), "asking once more" if _try == 1 else "giving up"))
    if not (j and j.get("verdict") in ("PASS", "FAIL")):
        j = qa_lenient(raw) or j
    if not j or j.get("verdict") not in ("PASS", "FAIL"):
        return False, ["QA answer not understood"]
    return j["verdict"] == "PASS", [str(r)[:400] for r in (j.get("reasons") or [])][:5]


# ---------- DEV ----------

# ---------- repair (26 Nov): fix ONLY the offending sentences instead of throwing the whole draft away ----------
# WHY: 23-26 Nov every daily draft read fine but failed one or two offline checks (an 'I', an example number with no source link, a 7-word run
# copied from our page). A full second writer try costs ~$0.45 and the day cap is $0.60, so it never happened and nothing was published.
# One cheap Haiku rewrite (no tools, ~$0.003, inside QA_RESERVE) of only the named sentences, then EVERY check runs again from scratch.
REPAIRABLE = ("7-word overlap", "first person singular", "banned phrase", "number '")
REPAIR_ROUNDS = 2
MAX_REPAIR_SENTENCES = 12
REPAIR_MAX_TOKENS = int(os.environ.get("DEV_REPAIR_MAX_TOKENS", "4000"))
REPAIR_EFFORT = os.environ.get("DEV_REPAIR_EFFORT", "low")
FIRST_PERSON_RE = re.compile(r"\b(I|I'm|I've|I'll|I'd|my|me|mine|myself)\b")
QUOTE_RE = re.compile(r'"[^"\n]*"|“[^”]*”')
REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rewrites"], "properties": {
    "rewrites": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "text"],
                                            "properties": {"n": {"type": "integer"}, "text": {"type": "string"}}}}}}
REPAIR_SYSTEM = ("You are a copy editor. Rewrite ONLY the numbered sentences. Each line says in [brackets] what is wrong with that sentence; fix exactly that:\n"
                 "- a shared run of words: change the structure and vocabulary so that run (and any 4 or more of its words in a row) no longer appears; keep the meaning;\n"
                 "- I / me / my: speak as 'we' / 'our' for the company (a question like 'am I shadowbanned?' becomes 'is an account shadowbanned?');\n"
                 "- a banned phrase: say the same thing without those words;\n- a figure with no source: drop the figure and make the point without any number.\n"
                 "Never add a number, a name, a feature or a claim. Keep every other number as it is. Keep every Markdown link [text](url) EXACTLY as it is "
                 "(same text, same url); a list item keeps its '- ' or '1. ' start; one sentence stays one sentence. "
                 'Answer ONLY the JSON object {"rewrites":[{"n":1,"text":"new sentence"}]} with one entry per numbered line.')


def repairable(problems):
    """True when EVERY problem is one a sentence rewrite can fix."""
    return bool(problems) and all(any(k in x for k in REPAIRABLE) for x in problems)


def sentence_spans(body):
    """[(start, end)] of every sentence (headings, blank lines and code fences skipped). A sentence never crosses a line."""
    spans, pos, fence = [], 0, False
    for line in body.split("\n"):
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and line.strip() and not line.lstrip().startswith("#"):
            start = 0
            for m in re.finditer(r"(?<=[.!?])\s+", line):
                if m.start() > start:
                    spans.append((pos + start, pos + m.start()))
                start = m.end()
            if len(line) > start:
                spans.append((pos + start, pos + len(line)))
        pos += len(line) + 1
    return spans


def _toks(t):
    return [w.lower() for w in re.findall(r"[A-Za-z0-9']+", re.sub(r"https?://\S+", " ", t))]


def offending_sentences(body, page_full, problems=()):
    """-> [(span index, reason)] for sentences with a shared run from our page, an I/me/my, a banned phrase or a figure the gate named."""
    spans = sentence_spans(body)
    sents = [body[a:b] for a, b in spans]
    plain = [re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", x) for x in sents]
    bad = {}
    r = _toks(page_full or "")
    grams = {" ".join(r[i:i + 7]) for i in range(len(r) - 6)}
    for i, x in enumerate(plain):
        t = _toks(x)
        run = next((" ".join(t[k:k + 7]) for k in range(len(t) - 6) if " ".join(t[k:k + 7]) in grams), None)
        if run:
            bad.setdefault(i, "copies 7 or more words in a row from our own page, the shared run is '%s'" % run)
    for i in range(len(plain) - 1):                    # a run that straddles two neighbouring sentences: rewrite both
        t = _toks(plain[i] + " " + plain[i + 1])
        if any(" ".join(t[k:k + 7]) in grams for k in range(len(t) - 6)):
            bad.setdefault(i, "copies 7 or more words in a row from our own page (with the next sentence)")
            bad.setdefault(i + 1, "copies 7 or more words in a row from our own page (with the previous sentence)")
    for i, x in enumerate(sents):
        if FIRST_PERSON_RE.search(QUOTE_RE.sub(" ", x)):
            bad.setdefault(i, "uses I / me / my (must speak as 'we' / 'our')")
        bb = [b for b in BANNED if b in x.lower()]
        if bb:
            bad.setdefault(i, "uses the banned phrase '%s': say it without those words" % bb[0])
    for pr in problems or ():
        m = re.match(r"number '([^']+)'", pr)
        if m:
            for i, x in enumerate(plain):
                if m.group(1) in x:
                    bad.setdefault(i, "contains the figure '%s' that has no source: drop the figure and make the point without any number" % m.group(1))
    return sorted(bad.items())


def _rewrites(raw):
    j = qa_json(raw)
    if not isinstance(j, dict) or not isinstance(j.get("rewrites"), list):
        return None
    out = {}
    for r in j["rewrites"]:
        if not (isinstance(r, dict) and isinstance(r.get("text"), str) and str(r.get("n", "")).strip().isdigit()):
            return None
        out[int(str(r["n"]).strip())] = r["text"]
    return out


def _keeps_links(old, new):
    return all(("[%s](%s)" % (t, u)) in new for t, u in LINK_RE.findall(old))


def _splice(body, spans, picks):
    new = body
    for i in sorted(picks, reverse=True):
        a, b = spans[i]
        rep, old = picks[i].strip(), body[a:b]
        if not _keeps_links(old, rep):
            continue                                   # a rewrite that drops or changes a link is NOT applied
        m = re.match(r"\s*([-*]|\d+[.)])\s", old)
        if rep and m and not re.match(r"([-*]|\d+[.)])\s", rep):
            rep = m.group(0) + rep
        new = new[:a] + rep + new[b:]
    return new


def ask_rewrites(system, user, meter, post=None):
    """One Haiku call (no tools, JSON schema, room to think) -> {sentence number: new text} or None. An unreadable reply is asked ONCE more with twice the room."""
    for attempt in (1, 2):
        data = anthropic(QA_MODEL, system, user, meter, post=post, max_tokens=REPAIR_MAX_TOKENS * attempt,
                         extra={"output_config": {"effort": REPAIR_EFFORT, "format": {"type": "json_schema", "schema": REWRITE_SCHEMA}}})
        rw = _rewrites("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"))
        if rw:
            return rw
        print("dev_poster: repair reply unreadable, %s" % no_text_note(data))
    return None


def repair_article(art, page_full, meter, post=None, problems=()):
    """ONE cheap Haiku rewrite (no tools) of ONLY the offending sentences -> repaired art dict, or None when nothing could be repaired."""
    body = art["body"]
    spans = sentence_spans(body)
    bad = offending_sentences(body, page_full, problems)
    if not bad or len(bad) > MAX_REPAIR_SENTENCES:
        return None
    lines = "\n".join("%d. [%s] %s" % (n + 1, why, body[spans[i][0]:spans[i][1]]) for n, (i, why) in enumerate(bad))
    rw = ask_rewrites(REPAIR_SYSTEM, "SENTENCES TO REWRITE:\n" + lines, meter, post)
    if not rw:
        return None
    picks = {}
    for n, (i, _) in enumerate(bad):
        rep = rw.get(n + 1)
        if not isinstance(rep, str) or not rep.strip() or "\n" in rep.strip():
            return None
        picks[i] = rep
    return dict(art, body=_splice(body, spans, picks))


QA_REPAIR_SYSTEM = ("You are a copy editor. A fact-checker found unsupported claims in an article; each is listed with the checker's exact quote and reason. "
                    "For each sentence that carries such a claim give a replacement that drops the unsupported claim and says something general and true that needs no source, "
                    "or an empty string to delete the sentence. Never add numbers, names, statistics or claims about what our tool does. "
                    "Keep every Markdown link [text](url) that sits in a sentence you keep or replace EXACTLY as it is. "
                    'Answer ONLY the JSON object {"rewrites":[{"n":4,"text":"new sentence"},{"n":9,"text":""}]} with at most 6 entries.')
QA_REPAIR_ROUNDS = 2


def _quoted(reason):
    m = re.match(r'\s*["“](.+?)["”]\s*\|', reason or "")
    return m.group(1) if m else ""


def quote_to_sentence(quote, sents):
    """Index of the sentence the checker quoted (most of the quote's words appear in it), or None."""
    q = set(_toks(quote))
    if len(q) < 3:
        return None
    best, bi = 0.0, None
    for i, x in enumerate(sents):
        sc = len(q & set(_toks(x))) / len(q)
        if sc > best:
            best, bi = sc, i
    return bi if best >= 0.6 else None


def qa_repair_article(art, reasons, meter, post=None):
    """ONE cheap rewrite (Haiku, no tools) of ONLY the sentences the fact-check QUOTED: general true wording, or delete. -> art dict or None."""
    body = art["body"]
    spans = sentence_spans(body)
    if not spans or not reasons:
        return None
    sents = [body[a:b] for a, b in spans]
    flagged = {}
    for r in reasons[:6]:
        i = quote_to_sentence(_quoted(r), sents)
        if i is not None:
            flagged.setdefault(i, r)
    numbered = "\n".join("%d. %s" % (i + 1, x) for i, x in enumerate(sents))
    hint = ("\n\nTHE CHECKER QUOTED THESE SENTENCES (change only these): " + ", ".join(str(i + 1) for i in sorted(flagged))) if flagged else ""
    rw = ask_rewrites(QA_REPAIR_SYSTEM, "FACT-CHECKER REASONS:\n- " + "\n- ".join(reasons[:6]) + hint + "\n\nSENTENCES:\n" + numbered[:12000], meter, post)
    if not rw or len(rw) > 6:
        return None
    picks = {}
    for k, v in rw.items():
        if not (1 <= k <= len(spans)) or "\n" in v.strip():
            return None
        if flagged and (k - 1) not in flagged:
            continue                                  # a sentence the checker did not quote stays exactly as it is
        picks[k - 1] = v
    if not picks:
        return None
    new = _splice(body, spans, picks)
    new = re.sub(r"(?m)^[ \t]*([-*]|\d+[.)])[ \t]*$\n?", "", new)          # a deleted list item leaves no empty bullet
    new = re.sub(r"[ \t]{2,}", " ", new)
    new = re.sub(r"\n{3,}", "\n\n", new).strip()
    return dict(art, body=new) if new != body else None


TRIM_SYSTEM = ("You are an editor shortening an article that is too long. You get its numbered sentences and the number of words to cut. "
               "Choose sentences to DELETE: repeated points, filler, extra examples, the weakest detail. Never choose a sentence that contains a Markdown link, a heading, or the only sentence of a section. "
               "Delete enough sentences to cut at least the requested number of words, but no more than that plus 60. "
               'Answer ONLY the JSON object {"rewrites":[{"n":4,"text":""},{"n":9,"text":""}]}: every entry has an empty text (= delete).')


def article_words(body):
    return len(words(re.sub(r"```.*?```", " ", body, flags=re.S)))


def trim_article(art, meter, post=None):
    """ONE cheap Haiku call that names whole sentences to delete when the article is over MAX_WORDS. Never touches a sentence with a link. -> art or None."""
    body = art["body"]
    excess = article_words(body) - MAX_WORDS
    if excess <= 0:
        return None
    spans = sentence_spans(body)
    sents = [body[a:b] for a, b in spans]
    numbered = "\n".join("%d. %s" % (i + 1, x) for i, x in enumerate(sents))
    rw = ask_rewrites(TRIM_SYSTEM, "CUT AT LEAST %d WORDS.\n\nSENTENCES:\n%s" % (excess + 20, numbered[:30000]), meter, post)
    if not rw:
        return None
    picks = {}
    for k, v in rw.items():
        if not (1 <= k <= len(spans)) or v.strip() or LINK_RE.search(sents[k - 1]):
            continue
        picks[k - 1] = ""
    if not picks:
        return None
    new = _splice(body, spans, picks)
    new = re.sub(r"(?m)^[ \t]*([-*]|\d+[.)])[ \t]*$\n?", "", new)
    new = re.sub(r"[ \t]{2,}", " ", new)
    new = re.sub(r"\n{3,}", "\n\n", new).strip()
    if article_words(new) < MIN_WORDS:
        return None
    return dict(art, body=new)


def publish(art, api_key, post=None):
    """POST https://dev.to/api/articles. canonical_url is NOT sent. Returns the response dict."""
    body = {"article": {"title": art["title"], "body_markdown": art["body"], "published": True,
                        "description": art.get("description", ""), "tags": art["tags"]}}
    headers = {"api-key": api_key, "accept": "application/vnd.forem.api-v1+json", "content-type": "application/json", "user-agent": UA}
    return (post or _post_json)(API, body, headers)


def list_published(api_key, get=None):
    """GET https://dev.to/api/articles/me/published (our own published articles, newest first). Raises on any failure (the caller fails closed)."""
    headers = {"api-key": api_key, "accept": "application/vnd.forem.api-v1+json", "user-agent": UA}
    if get:
        return get(API + "/me/published?per_page=30", headers)
    req = urllib.request.Request(API + "/me/published?per_page=30", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if not isinstance(data, list):
        raise RuntimeError("unexpected answer")
    return data


def published_today(listing, today):
    return any(str(a.get("published_at") or a.get("published_timestamp") or a.get("created_at") or "")[:10] == today for a in listing)


def reconcile_pending(state, listing, today):
    """A 'pending' marker means a POST was started and we do not know if it worked. Returns None / 'recorded' / 'cleared' / 'unknown'."""
    pend = state.get("pending")
    if not pend:
        return None
    hit = next((a for a in listing if a.get("title") == pend.get("title")), None)
    if hit:
        state["posts"].append({"date": str(hit.get("published_at") or pend["date"])[:10], "slug": pend.get("slug"), "title": pend.get("title"),
                               "url": hit.get("url") or "", "id": hit.get("id"), "target": pend.get("target"), "rel": "unknown (recovered)"})
        state.pop("pending", None)
        return "recorded"
    if pend.get("date") != today:
        state.pop("pending", None)
        return "cleared"
    return "unknown"


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


def load_state():
    """The state dict, or None = do not run. A missing / unparsable / wrong-shaped file is only accepted as 'empty' on the very first run, i.e. when the
    workflow saw that the private state branch does not exist yet (DEV_FIRST_RUN=true)."""
    first = os.environ.get("DEV_FIRST_RUN", "") == "true"
    if not os.path.exists(STATE_PATH):
        return {"posts": [], "attempts": {}} if first else None
    try:
        d = json.load(open(STATE_PATH, encoding="utf8"))
    except Exception:
        return None
    if not isinstance(d, dict) or not isinstance(d.get("posts", []), list):
        return None
    d.setdefault("attempts", {})
    return d


def live_switch_on():
    return os.environ.get("DEV_POSTER_LIVE", "").strip().lower() == "true"


def run(post_ai=None, post_dev=None, getter=None, sleeper=time.sleep, lister=None):
    getter = getter or c.http_get
    today = utc_today()
    dry = DRY or not live_switch_on()          # publishing needs BOTH: a non-dry run AND the repository switch (scheduled and manual runs alike)
    if os.environ.get("DEV_STATE_OK", "") != "true":
        print("dev_poster: the private state could not be restored, nothing runs (fail closed)")
        return "NO_STATE"
    state = load_state()
    if state is None:
        print("dev_poster: the state file is missing or unreadable although the private branch exists, nothing runs (fail closed)")
        return "NO_STATE"
    state.setdefault("posts", [])
    spent_before = float((state.get("spent") or {}).get(today, 0.0) or 0.0)
    key = os.environ.get("DEV_API_KEY")
    listing = None
    if not dry:
        if not key:
            print("dev_poster: no DEV_API_KEY, stop")
            return "NO_KEY"
        try:
            listing = list_published(key, get=lister)               # what does DEV itself say we published? (fail closed if it cannot answer)
        except Exception as e:
            print("dev_poster: cannot ask DEV what we published (%s), stop" % type(e).__name__)
            return "DEV_CHECK_FAILED"
        rec = reconcile_pending(state, listing, today)
        if rec:
            print("dev_poster: pending marker %s" % rec)
            write_json(STATE_PATH, state)
        if rec == "unknown":
            print("dev_poster: an earlier POST today may or may not have worked, stop (the Director checks DEV)")
            return "PENDING_UNKNOWN"
        if published_today(listing, today):
            print("dev_poster: DEV says we already published today, nothing to do")
            return "ALREADY_DONE"
    if any(e.get("date") == today and e.get("url") for e in state["posts"]):
        print("dev_poster: already published today, nothing to do")
        return "ALREADY_DONE"
    tries_today = state.get("attempts", {}).get(today, 0)
    if tries_today >= 2 and not dry:
        print("dev_poster: 2 failed attempts today, stop (the Director looks at the drafts)")
        return "STOPPED"
    dry_today = (state.get("dry") or {}).get(today, 0)
    if dry and dry_today >= MAX_DRY_PER_DAY:
        print("dev_poster: %d dry runs today already, stop" % dry_today)
        return "DRY_LIMIT"
    remaining = round(DAY_CAP - spent_before, 4)
    if remaining < WRITER_RESERVE + QA_RESERVE:
        print("dev_poster: the day's $%.2f is used up ($%.3f spent), stop" % (DAY_CAP, spent_before))
        return "DAY_CAP"
    meter = Meter(cap=remaining)

    def save():
        sp = state.setdefault("spent", {})
        sp[today] = round(spent_before + meter.cost, 4)
        for k in sorted(sp)[:-7]:
            del sp[k]
        write_json(STATE_PATH, state)

    meter.hook = save                      # the day's spend is written to the state file around EVERY model call

    page = pick_page(state, today)
    if not page:
        print("dev_poster: every page is resting, nothing to write")
        return "RESTING"
    slug, url, kind, what, topic = page
    st, _, html = getter(url)
    page_text = c.html_text(html, 6000) if st == 200 else ""
    page_full = c.html_text(html, 40000) if st == 200 else ""
    if st != 200 or len(page_text) < 200:
        print("dev_poster: target page not readable (HTTP %s), stop" % st)
        log("| %s | %s | STOP page not readable | | | $0 | HTTP %s |" % (today, slug, st))
        return "PAGE_DOWN"
    anchor = anchor_info(slug, url, anchor_count(state, slug))
    past = [e.get("title", "") for e in state["posts"][-PAST_ARTICLES_KEPT:] if e.get("title")]
    past_sh = set()
    for lst in (state.get("shingles") or [])[-PAST_ARTICLES_KEPT:]:
        past_sh.update(lst)
    user = ("TARGET PAGE: %s\nIT IS: %s (%s)\nTOPIC AREA: %s\nPAGE TEXT (the only facts you may state about our page):\n%s\n\n"
            "ARTICLES WE ALREADY PUBLISHED (do not repeat these angles):\n%s\n\n%s\n\nWrite today's article now.") % (
        url, what, kind, topic, page_text, "\n".join("- " + t for t in past) or "(none yet)", anchor_prompt(anchor))
    if dry:                                # counted and saved BEFORE any model call: a killed run cannot lose them
        state.setdefault("dry", {})[today] = dry_today + 1
    else:
        state.setdefault("attempts", {})[today] = tries_today + 1
    save()
    problems, art, source_texts, unreadable = ["no draft"], None, {}, []
    for attempt in (1, 2):
        if attempt == 2 and meter.cost > RETRY_BELOW + 1e-9:
            break
        extra = "" if attempt == 1 else "\n\nYOUR LAST DRAFT FAILED THESE CHECKS, fix them: " + "; ".join(problems)
        if attempt == 2 and unreadable:
            extra += ("\nThese pages could NOT be read by our checker (empty or only a title): do not cite them again and drop every number whose only source is one of them: "
                      + " | ".join(unreadable[:6]))
        fetched = {}
        try:
            art = parse_writer(run_writer(WRITER_SYSTEM, user + extra, meter, post=post_ai, fetched=fetched))
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
            ours = c.html_text(h2, 20000) if s2 == 200 else ""
            theirs = fetched.get(u, "")[:20000]                    # what the writer's own fetch tool returned for that page
            source_texts[u] = theirs if len(theirs) > len(ours) else ours
        unreadable = [u for u in ext if len(source_texts.get(u, "")) < MIN_SOURCE_CHARS]
        problems = check_article(art, url, page_text, source_texts, past_shingles=past_sh, page_full=page_full, anchor=anchor)
        if any(x.startswith("words ") for x in problems) and article_words(art["body"]) > MAX_WORDS:
            try:                                    # too long: ONE cheap pass deletes whole low-value sentences (no second writer try)
                cut = trim_article(art, meter, post=post_ai)
            except CapReached:
                cut = None
            except Exception as e:
                print("dev_poster: trim skipped (%s)" % type(e).__name__)
                cut = None
            if cut:
                p2 = check_article(cut, url, page_text, source_texts, past_shingles=past_sh, page_full=page_full, anchor=anchor)
                print("dev_poster: trim words %d -> %d, problems %d -> %d" % (article_words(art["body"]), article_words(cut["body"]), len(problems), len(p2)))
                art, problems = cut, p2
        rounds = REPAIR_ROUNDS
        while problems and repairable(problems) and rounds > 0:
            rounds -= 1
            try:
                fixed = repair_article(art, page_full, meter, post=post_ai, problems=problems)
            except CapReached:
                break
            except Exception as e:
                print("dev_poster: repair skipped (%s)" % type(e).__name__)
                break
            if not fixed or fixed["body"] == art["body"]:
                break
            p2 = check_article(fixed, url, page_text, source_texts, past_shingles=past_sh, page_full=page_full, anchor=anchor)
            print("dev_poster: repair problems %d -> %d" % (len(problems), len(p2)))
            art, problems = fixed, p2
        sl, _, _ = getter(url)
        if sl != 200:
            problems.append("target link not live (HTTP %s)" % sl)
        if not problems:
            break
    ok = not problems
    reasons = problems
    if ok:
        qa_rounds = 0
        while True:
            try:
                ok, reasons = qa_gate(art, url, source_texts, page_text, meter, post=post_ai)
            except Exception as e:
                ok, reasons = False, ["QA error %s" % type(e).__name__]
                break
            if ok or qa_rounds >= QA_REPAIR_ROUNDS or reasons == ["QA answer not understood"]:
                break
            if meter.cost + 3 * QA_RESERVE > meter.cap + 1e-9:        # a rewrite, the offline checks and one more QA must still fit the day
                break
            qa_rounds += 1                          # the fact-check named unsupported claims: ONE rewrite of just those sentences, then every gate again
            try:
                fixed = qa_repair_article(art, reasons, meter, post=post_ai)
            except Exception as e:
                print("dev_poster: QA repair skipped (%s)" % type(e).__name__)
                break
            if not fixed:
                break
            p2 = check_article(fixed, url, page_text, source_texts, past_shingles=past_sh, page_full=page_full, anchor=anchor)
            print("dev_poster: QA repair problems 0 -> %d" % len(p2))
            if p2:
                break                               # the rewrite broke an offline gate: keep the original FAIL reasons
            art = fixed
    cost = "$%.3f" % meter.cost
    if not ok:
        if art:
            os.makedirs(DRAFT_DIR, exist_ok=True)
            open(os.path.join(DRAFT_DIR, "%s_%s.md" % (today, slug)), "w", encoding="utf8").write(
                "# FAILED DRAFT (not published)\n\nReasons: %s\n\nTitle: %s\n\n%s\n" % ("; ".join(reasons), art["title"], art["body"]))
        log("| %s | %s | FAIL, not published | | | %s | %s |" % (today, slug, cost, "; ".join(reasons)[:300]))
        save()
        print("dev_poster: FAIL page=%s problems=%d cost=%s" % (slug, len(reasons), cost))
        return "FAIL"
    if dry:
        os.makedirs(DRAFT_DIR, exist_ok=True)
        open(os.path.join(DRAFT_DIR, "%s_%s_DRYRUN.md" % (today, slug)), "w", encoding="utf8").write(
            "# DRY RUN (not published)\n\nTitle: %s\nTags: %s\n\n%s\n" % (art["title"], ",".join(art["tags"]), art["body"]))
        log("| %s | %s | DRY RUN passed all checks, not published | | | %s | |" % (today, slug, cost))
        save()
        print("dev_poster: DRY RUN ok page=%s cost=%s (publish endpoint NOT called)" % (slug, cost))
        return "DRY_OK"
    state["pending"] = {"date": today, "slug": slug, "title": art["title"], "target": url}      # written BEFORE the POST: a crash leaves this behind
    save()
    try:
        res = publish(art, key, post=post_dev)
    except urllib.error.HTTPError as e:
        state.pop("pending", None)                      # DEV answered with a refusal: nothing was created
        log("| %s | %s | DEV refused HTTP %s | | | %s | %s |" % (today, slug, e.code, cost, "429 = rate limit; retried next run" if e.code == 429 else ""))
        save()
        print("dev_poster: DEV answered HTTP %s" % e.code)
        return "DEV_ERROR"
    except Exception as e:                              # timeout or network error: the article MAY exist; the pending marker stays
        log("| %s | %s | DEV error %s (pending marker kept) | | | %s | |" % (today, slug, type(e).__name__, cost))
        save()
        print("dev_poster: DEV error %s, pending marker kept" % type(e).__name__)
        return "DEV_UNKNOWN"
    art_url = res.get("url") or ""
    state.pop("pending", None)
    state["posts"].append({"date": today, "slug": slug, "title": art["title"], "url": art_url, "id": res.get("id"), "target": url, "rel": "unknown (not read yet)"})
    state.setdefault("shingles", []).append(sorted(shingles(art["body"])))
    state["shingles"] = state["shingles"][-PAST_ARTICLES_KEPT:]
    save()                                              # the article is recorded BEFORE the slow live-page read below
    sleeper(20)
    s3, _, live = getter(art_url) if art_url else (0, "", "")
    rel = link_rel(live, url) if s3 == 200 else "unknown (page HTTP %s)" % s3
    state["posts"][-1]["rel"] = rel
    log("| %s | %s | PUBLISHED | %s | %s | %s | |" % (today, slug, art_url, rel, cost))
    save()
    print("dev_poster: PUBLISHED page=%s link=%s cost=%s" % (slug, rel, cost))
    return "PUBLISHED"


if __name__ == "__main__":
    run()
