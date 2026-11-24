#!/usr/bin/env python3
"""LIST BUILDER (24 Nov 2026): adds about 150-200 NEW candidate places a day to data/directories_candidates.json.

Imran 24 Nov 2026: "We should have at least 2,000, 3,000, going up to 5,000 list of platforms, directories, tools, guides, blog forms,
anything, everything ... variety of places ... I want to know how much list has increased today."

Flow per run: rotate through 12 categories (a cursor kept in data/list_builder_state.json) -> Claude Sonnet + web search lists real places
for one category + angle -> code filters spam, de-duplicates by registrable domain against EVERY list we hold -> every URL is opened
(GET only, HTTP 200-399 or dropped) -> rows are added with status TODO (the directory agent may try them), TODO outreach (for outreach,
never form submission) or HOLD paid. Writes the one-line summary "list-builder: +N new (total T; by category ...)" to the state file.

HARD LIMITS: $3.00 a UTC day over ALL runs (env LIST_BUILDER_DAY_USD_CAP; the cost of a call is booked BEFORE it goes out and the state
is saved, so a killed run still has it on record); no call starts unless spent + its reserve fits under the cap.
Sends nothing, signs up nowhere, fills no form, pays nothing. PUBLIC repo: prints counts, category keys and cost only (never a key, never
a model answer, never an error body).
"""
import concurrent.futures
import datetime
import json
import os
import re
import sys
import urllib.request
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts", "reports"))
import common as c  # noqa: E402  (http_get, html_text)

DATA = os.environ.get("LIST_BUILDER_DATA", os.path.join(ROOT, "data"))
CAND_PATH = os.path.join(DATA, "directories_candidates.json")
STATE_PATH = os.path.join(DATA, "list_builder_state.json")
DRY = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
MODEL = os.environ.get("LIST_BUILDER_MODEL", "claude-sonnet-5-5")
DAY_CAP = float(os.environ.get("LIST_BUILDER_DAY_USD_CAP", "3.00"))   # HARD cap per UTC day over all runs (Imran's ~$15/day: 24 Nov, hunter 6.00 -> 3.00, list builder 1.50 -> 3.00)
RESERVE = 0.20          # most one request can cost: <= ~45k tokens in x $2/M + 4000 out x $10/M + 4 searches x $0.01 = ~$0.17
MAX_TOKENS = 4000
MAX_SEARCHES = int(os.environ.get("LIST_BUILDER_MAX_SEARCHES", "4"))
TARGET_NEW = int(os.environ.get("LIST_BUILDER_TARGET", "450"))        # stop for the day once this many rows were added
MAX_NEW_PER_DAY = int(os.environ.get("LIST_BUILDER_MAX_NEW", "550"))  # never add more than this in one UTC day
MAX_CALLS_PER_RUN = int(os.environ.get("LIST_BUILDER_MAX_CALLS", "12"))
REJECTED_KEPT = 6000    # domains that failed a check, remembered so the same place is not paid for twice
PRICES = {"claude-sonnet-5-5": (2.00, 10.00, 0.20)}     # USD per million tokens: fresh in, out, cache read (same table as dev_poster)
SEARCH_PRICE = 0.01
UA_NOTE = "inayaflow-list-builder"

# key, kind ("directory" = status TODO, the directory agent may try it; "outreach" = status TODO outreach), what to find, angles (rotate)
CATEGORIES = [
    ("ai_saas_directories", "directory", "directories of AI tools and SaaS products where a free listing / submission is possible",
     ["AI tool directories", "AI writing and content tool lists", "AI video and social media tool lists", "AI agent and automation directories", "no-code and productivity tool directories", "AI newsletter and tool-of-the-day sites", "chrome extension and app directories", "AI chatbot and assistant directories"]),
    ("launch_sites", "directory", "product launch sites and 'submit your startup / product' pages with a free option",
     ["product launch platforms", "startup launch lists", "indie hacker and maker launch boards", "beta tester and early-adopter sites", "new tool announcement sites", "app of the day style sites"]),
    ("tool_roundups", "outreach", "web pages that are roundups or 'best tools' lists on social media, TikTok, creator or marketing topics and say they accept tool suggestions or have a contact",
     ["best TikTok tools roundups", "best social media management tools lists", "best creator economy tools lists", "best AI tools for content creators lists", "best analytics tools for creators lists", "best free tools for influencers lists", "best video editing and short-form tools lists", "best social media scheduling tools lists", "best video analytics tools lists", "best influencer marketing tools lists"]),
    ("github_awesome_lists", "outreach", "GitHub 'awesome' lists and curated repositories (README link lists) on social media, creator tools, marketing, AI tools or startups that accept pull requests or suggestions",
     ["awesome social media tools", "awesome creator tools", "awesome marketing tools", "awesome AI tools lists", "awesome indie and startup resources", "awesome free tools and no-code lists", "awesome SaaS and growth lists"]),
    ("saas_review_platforms", "directory", "SaaS and software review / listing platforms where a vendor can claim or create a free profile",
     ["software review sites with free vendor profiles", "alternatives-to sites", "SaaS comparison sites", "B2B software marketplaces with free listings", "app marketplaces and plugin stores with free submission", "tool comparison and 'versus' sites"]),
    ("guest_post_pages", "outreach", "'write for us' / guest post / contributor pages of blogs in the social media, TikTok, creator, content marketing, video or digital marketing niche that publish free guest posts",
     ["social media marketing blogs write for us", "content creator and influencer blogs write for us", "video marketing and short-form video blogs write for us", "digital marketing blogs that accept contributors", "small business and startup blogs that accept guest posts", "marketing newsletters that accept contributions", "influencer marketing blogs write for us", "creator economy blogs and newsletters that accept contributors", "TikTok growth blogs write for us"]),
    ("resource_pages", "outreach", "resource pages, 'useful links' pages and curated link pages on social media, creator or marketing topics that list outside tools or guides and show a contact",
     ["social media resource pages", "creator resources pages", "marketing resource and links pages", "free tools resource pages", "TikTok resources pages", "small business tools resource pages"]),
    ("communities_profiles", "directory", "communities, forums and platforms where a member can create a free profile or post with a followed or visible link (indie, maker, creator, marketing, AI communities)",
     ["maker and indie communities", "creator communities and forums", "marketing and growth communities", "AI and no-code communities", "startup founder communities", "developer and design communities with profiles", "TikTok creator communities and forums", "social media manager communities", "influencer marketing communities"]),
    ("podcasts_creators", "outreach", "podcasts and shows for creators, social media managers or marketers that book guests or accept topic pitches (booking or contact page printed)",
     ["creator economy podcasts", "social media marketing podcasts", "short-form video podcasts", "AI for creators podcasts", "startup and indie founder podcasts", "small business marketing podcasts"]),
    ("journalist_platforms", "outreach", "journalist request, expert-source and media-matching platforms and newsletters where a company can answer reporter queries for free (or with a free tier)",
     ["journalist request platforms", "expert source databases with a free tier", "PR query newsletters", "podcast guest matching platforms", "startup press and media listing sites", "local and niche trade press contact lists"]),
    ("edu_library_guides", "outreach", "university, college, school and public-library resource guides (LibGuides, 'research guides', 'digital media' or 'social media' pages) that list outside web tools and have a staff contact",
     ["library guides on social media", "university digital media resource pages", "college media and communication guides", "school digital literacy resource lists", "career and creator skills resource guides", "libguides on video and content creation"]),
    ("startup_directories_by_country", "directory", "startup, tool and business directories of a specific country or region that accept free submissions",
     ["USA", "United Kingdom", "Canada", "Australia", "India", "Germany", "France", "Spain", "Netherlands", "Singapore", "United Arab Emirates", "Saudi Arabia", "Brazil", "Nigeria", "South Africa", "Ireland",
      "Sweden", "Poland", "Italy", "Turkey", "Indonesia", "Philippines", "Mexico", "New Zealand", "Israel", "Pakistan", "Kenya", "Portugal"]),
]
CAT_KEYS = [x[0] for x in CATEGORIES]

# registrable domains that host many unrelated places. PATH hosts: the dedupe key is host + first two path parts (github.com/owner/repo);
# SUBDOMAIN hosts: every subdomain is its own site, the key is the full host (mysite.blogspot.com)
PATH_HOSTS = {"github.com", "gitlab.com", "bitbucket.org", "medium.com", "reddit.com", "producthunt.com", "linkedin.com", "facebook.com", "quora.com",
              "google.com", "x.com", "twitter.com", "youtube.com", "t.me", "discord.com", "discord.gg", "slack.com", "dev.to", "notion.so", "instagram.com", "tiktok.com"}
SUBDOMAIN_HOSTS = {"substack.com", "wordpress.com", "blogspot.com", "notion.site", "github.io", "tumblr.com", "wixsite.com", "weebly.com", "hashnode.dev",
                   "beehiiv.com", "ghost.io", "netlify.app", "vercel.app", "pages.dev", "gitbook.io", "carrd.co", "webflow.io"}
TWO_PART = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "edu.au", "co.nz", "co.in", "co.za", "com.br", "com.mx", "com.sg", "com.tr", "com.ng",
            "co.ke", "com.pk", "com.ph", "co.id", "com.sa", "com.ar", "co.jp", "com.cn", "com.hk", "com.my", "org.in", "ac.in", "edu.in", "com.eg", "co.il", "ac.nz", "org.nz", "ac.za"}

SPAM_RE = re.compile(
    r"buy[\s-]*(cheap\s+)?(backlinks?|links|guest\s*posts?|dofollow|traffic|followers|likes|views)|backlinks?\s+(seller|service|package|store|shop|for\s+sale|marketplace)|"
    r"(link|backlink)\s*(building\s+)?(service|packages?)|links?\s+for\s+sale|\bpbn\b|private\s+blog\s+network|casino|betting|\bbet\d*\b|poker|\bslots?\b|gambl|lottery|"
    r"porn|\bxxx\b|\badult\b|\bescort|\bsex(y|ual)?\b|viagra|cialis|payday|\bloans?\b|crypto\s*(signal|casino)|essay\s+writ|cheap\s+seo|seo\s+(reseller|packages?)|"
    r"sponsored\s+posts?\s+(from|for)\s+[$£€]|paid\s+guest\s+posts?|guest\s+post\s+(service|for\s+sale)|\bsell\s+(your\s+)?links|casino|\bvpn\s+coupon|replica|\bcbd\b|kratom|\bdating\b", re.I)
SPAM_TLDS = (".casino", ".bet", ".poker", ".porn", ".xxx", ".sex", ".adult", ".loan", ".win", ".bid", ".top", ".click", ".gq", ".tk", ".ml", ".cf", ".ga")
PAID_RE = re.compile(r"paid[\s-]*(only|listing|submission|placement)|submission\s+fee|listing\s+fee|\bsponsored\s+(listing|post)s?\s+(only|start)|(pay|costs?|from|only|starts?\s+at)\s*(us)?[$£€]\s?\d+\s*(to|for)\s*(list|submit|get\s+listed|publish)|no\s+free\s+(listing|submission)", re.I)
CAPTCHA_RE = re.compile(r"recaptcha|hcaptcha|cf-turnstile|challenges\.cloudflare|captcha", re.I)


# ---------- helpers (pure, tested offline) ----------

def utc_today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def reg_domain(host):
    """Registrable domain: last two labels, three for known two-part suffixes (co.uk ...). Lower case, no 'www.'."""
    h = (host or "").lower().strip(".").split(":")[0]
    parts = [p for p in h.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in TWO_PART:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def norm_url(u):
    """Clean https URL or None. Drops fragments and tracking parameters, rejects IP hosts and non-web schemes."""
    u = (u or "").strip().strip("<>\"'")
    if not u:
        return None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    host = (p.hostname or "").lower()
    if not host or "." not in host or re.fullmatch(r"[\d.]+", host) or host in ("localhost",) or len(u) > 300:
        return None
    q = "&".join(x for x in (p.query or "").split("&") if x and not re.match(r"(utm_|ref=|fbclid|gclid)", x, re.I))
    path = p.path or "/"
    return "https://%s%s%s" % (host, path, ("?" + q) if q else "")


def dedupe_key(url):
    """Registrable domain; for shared hosts (github.com ...) host + the first two path parts."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    rd = reg_domain(host)
    if rd in PATH_HOSTS:
        segs = [s for s in (p.path or "").split("/") if s][:2]
        return "/".join([host] + segs).lower()
    if rd in SUBDOMAIN_HOSTS:
        return host
    return rd


def is_spam(*texts):
    if SPAM_RE.search(" ".join(str(t or "") for t in texts)):
        return True
    for t in texts:
        if isinstance(t, str) and t.startswith("http") and (urlparse(t).hostname or "").endswith(SPAM_TLDS):
            return True
    return False


def known_keys(cand, extra_files=()):
    """Every dedupe key we already hold: candidates (domain, submit_url, final_url), data/directories.json, listings.json, own_backlinks.json,
    plus optional extra files (env LIST_BUILDER_KNOWN_FILES: JSON lists / dicts or one URL or domain per line)."""
    keys = set()

    def add(v):
        if not v or not isinstance(v, str):
            return
        v = v.strip()
        if not v:
            return
        u = norm_url(v)
        if u:
            k = dedupe_key(u)
            keys.add(k)
            if "/" not in k and k == reg_domain(urlparse(u).hostname or ""):
                keys.add(k)

    for s in cand.get("sites", []):
        for f in ("domain", "submit_url", "final_url"):
            add(s.get(f))
    for fn, fields in (("directories.json", ("submit_url", "url")), ("listings.json", ("url",)), ("own_backlinks.json", ("source", "target"))):
        try:
            d = json.load(open(os.path.join(DATA, fn), encoding="utf8"))
        except Exception:
            continue
        stack = [d]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                for f in fields:
                    if isinstance(x.get(f), str):
                        add(x[f])
                stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
            elif isinstance(x, list):
                stack.extend(x)
    try:   # shared skip list (24 Nov 2026): domains the directory agent must never try again are never re-listed either
        for dom in (json.load(open(os.path.join(DATA, "known_sites.json"), encoding="utf8")).get("domains") or {}):
            add(dom)
    except Exception:
        pass
    for fn in extra_files:
        try:
            raw = open(fn, encoding="utf8", errors="ignore").read()
        except Exception:
            continue
        try:
            stack = [json.loads(raw)]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    stack.extend(x.values())
                elif isinstance(x, list):
                    stack.extend(x)
                elif isinstance(x, str):
                    add(x)
        except Exception:
            for line in raw.splitlines():
                add(line.split()[0] if line.split() else "")
    keys.discard("")
    return keys


def parse_rows(text):
    """Model answer -> list of dicts: one JSON object per line (anything else is ignored)."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if isinstance(o, dict) and o.get("url"):
            out.append(o)
    return out


def pick_category(cursor):
    """(key, kind, what, angle) for call number `cursor`: categories rotate every call, the angle moves on after a full turn."""
    key, kind, what, angles = CATEGORIES[cursor % len(CATEGORIES)]
    return key, kind, what, angles[(cursor // len(CATEGORIES)) % len(angles)]


def build_row(item, cat_key, kind, angle, today, http, final, page):
    """One candidate row, same fields as the existing rows + category / added / source_query."""
    url = item["_url"]
    host = urlparse(url).hostname or ""
    key = item["_key"]
    free = str(item.get("free", "unknown")).lower()
    paid = free == "no" or bool(PAID_RE.search(str(item.get("note", "")))) or bool(PAID_RE.search(page[:20000]))
    captcha = bool(CAPTCHA_RE.search(page))
    if paid:
        status, usable, free_check = "HOLD paid", False, "paid"
    elif kind == "directory":
        status, usable, free_check = "TODO", True, "free_likely" if free == "yes" else "unclear"
    else:
        status, usable, free_check = "TODO outreach", False, "free_likely" if free == "yes" else "unclear"
    domain = key                       # registrable domain; host for subdomain sites (x.blogspot.com); host/owner/repo for github.com
    return {"name": str(item.get("name") or domain)[:80], "domain": domain, "submit_url": url,
            "source": "list-builder: %s (added %s)" % (cat_key, today), "category": cat_key, "added": today, "source_query": ("%s: %s" % (cat_key, angle))[:120],
            "their_category": cat_key, "their_auto_flag": "", "their_notes": str(item.get("note", ""))[:200], "their_status": None,
            "http": http, "final_url": final, "free_check": free_check, "captcha_words_on_page": captcha,
            "has_form_on_page": bool(re.search(r"<form\b", page, re.I)), "quality": "unchecked", "usable": usable, "followed": "unknown",
            "checked": today, "status": status, "type": str(item.get("type", ""))[:30]}


def summary_line(added, total, by_cat):
    parts = ", ".join("%s %d" % (k, by_cat[k]) for k in sorted(by_cat, key=lambda k: (-by_cat[k], k)) if by_cat[k])
    return "list-builder: +%d new (total %d; by category %s)" % (added, total, parts or "none")


# ---------- state ----------

def read_json(path, default):
    try:
        return json.load(open(path, encoding="utf8"))
    except Exception:
        return default


def write_json(path, data, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, **kw)
        f.write("\n")
    os.replace(tmp, path)


def load_state():
    st = read_json(STATE_PATH, {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault("cursor", 0)
    st.setdefault("spent", {})
    st.setdefault("days", {})
    st.setdefault("rejected", [])
    return st


def trim_state(st, today):
    """Keep the file small: 14 days of spend and day records, the newest REJECTED_KEPT rejected keys."""
    keep = sorted(st["days"])[-14:]
    st["days"] = {k: st["days"][k] for k in keep}
    st["spent"] = {k: st["spent"][k] for k in sorted(st["spent"])[-14:]}
    st["rejected"] = st["rejected"][-REJECTED_KEPT:]


# ---------- Anthropic (raw HTTP, metered) ----------

class CapReached(RuntimeError):
    pass


def _post_json(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.load(r)


def call_cost(usage):
    u = usage or {}
    pin, pout, pcr = PRICES[MODEL]
    fresh, cr, cw, out = (int(u.get(k, 0) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
    s = int(((u.get("server_tool_use") or {}).get("web_search_requests", 0)) or 0)
    return (fresh * pin + cr * pcr + cw * pin * 1.25 + out * pout) / 1e6 + s * SEARCH_PRICE


class Meter:
    """The day's spend, kept in the state file. book() reserves BEFORE the request; settle() replaces the reserve by the real cost."""

    def __init__(self, state, today, cap=None, save=None):
        self.state, self.today = state, today
        self.cap = DAY_CAP if cap is None else cap
        self.save = save or (lambda: None)
        self.calls = 0

    @property
    def spent(self):
        return float(self.state["spent"].get(self.today, 0.0) or 0.0)

    def room_for_call(self):
        return self.spent + RESERVE <= self.cap + 1e-9

    def book(self):
        if not self.room_for_call():
            raise CapReached("daily cost cap: %.3f spent + %.2f reserve > %.2f" % (self.spent, RESERVE, self.cap))
        self.state["spent"][self.today] = round(self.spent + RESERVE, 4)
        self.calls += 1
        self.save()

    def settle(self, usage):
        real = call_cost(usage)
        self.state["spent"][self.today] = round(max(0.0, self.spent - RESERVE + real), 4)
        self.save()


def ask_model(system, user, meter, post=None):
    """Sonnet with web search. Handles pause_turn (each continuation is metered and capped like a new call). Returns the final text."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and post is None:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    headers = {"x-api-key": key or "", "anthropic-version": "2023-06-01", "content-type": "application/json"}
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}]
    messages = [{"role": "user", "content": user}]
    text = ""
    for _ in range(3):
        meter.book()                                                   # reserve booked and SAVED before the request goes out
        data = (post or _post_json)("https://api.anthropic.com/v1/messages",
                                    {"model": MODEL, "max_tokens": MAX_TOKENS, "system": system, "messages": messages, "tools": tools}, headers)
        meter.settle(data.get("usage"))
        content = data.get("content") or []
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text") or text
        if data.get("stop_reason") == "refusal":
            raise RuntimeError("refusal")
        if data.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": content})
            continue
        return text
    return text


SYSTEM = """You build a list of real web places where a free tool for TikTok creators (InayaFlow, https://inayaflow.ai) can get a listing, a mention or a link. You research with web search and answer with JSON lines only.
RULES:
- Only real, live sites that you saw in search results. Never invent a site or a URL. Give the page where a person submits / suggests / contacts (the submit, suggest, write-for-us, contact or list page); if you only saw the site's home page, give the home page.
- Prefer lesser-known, long-tail places over the famous giants. Skip anything that sells backlinks, runs link farms or PBNs, is casino / betting / adult / loans / crypto-signals, or is a pure 'buy a sponsored post' site. Skip sites that need a payment to be listed unless you mark them free = "no".
- Never list inayaflow.ai. No duplicates. No social profile pages of single people.
- "free" is "yes" only if you SAW that listing / submission is free, "no" if you saw it is paid only, otherwise "unknown".
OUTPUT: first line "LIST DONE", then ONLY JSON lines (no markdown fence, no comment), then a last line "COUNT n". One object per line:
{"name": "<site name>", "url": "<https url>", "type": "<directory|launch_site|roundup|awesome_list|review_platform|guest_post|resource_page|community|podcast|journalist|edu_guide|country_directory>", "free": "yes|no|unknown", "note": "<12 words max: what it is>"}
Give up to 40 places. If you find fewer, give fewer; never pad."""


def user_prompt(cat_key, what, angle, avoid):
    return "CATEGORY: %s\nFIND: %s.\nANGLE FOR THIS RUN: %s.\nDo not list these (already on our list): %s\nUse your web searches on different phrasings of this angle, then answer in the format given." % (
        cat_key, what, angle, ", ".join(avoid) or "none")


# ---------- the run ----------

def check_url(url, getter):
    """(http, final_url, page_text) or None when it is not reachable (HTTP 200-399)."""
    st, final, page = getter(url, timeout=12, limit=200000)
    if 200 <= st < 400 and final:
        return st, final, page or ""
    return None


def run(post=None, getter=None, today=None, workers=8):
    getter = getter or c.http_get
    today = today or utc_today()
    state = load_state()
    cand = read_json(CAND_PATH, None)
    if not isinstance(cand, dict) or not isinstance(cand.get("sites"), list):
        print("list-builder: the candidates file is missing or unreadable, nothing runs (fail closed)")
        return {"status": "NO_DATA"}
    day = state["days"].setdefault(today, {"added": 0, "by_category": {}, "runs": 0, "calls": 0})
    meter = Meter(state, today, save=lambda: write_json(STATE_PATH, state))
    extra = [p for p in os.environ.get("LIST_BUILDER_KNOWN_FILES", "").split(os.pathsep) if p]
    known = known_keys(cand, extra) | set(state["rejected"])
    new_rows, rejected = [], []
    day["runs"] += 1
    stats = {"asked": 0, "raw": 0, "spam": 0, "dup": 0, "dead": 0, "added": 0}
    calls = 0
    while calls < MAX_CALLS_PER_RUN and day["added"] + len(new_rows) < TARGET_NEW and day["added"] + len(new_rows) < MAX_NEW_PER_DAY:
        if not meter.room_for_call():
            print("list-builder: daily cost cap reached ($%.2f of $%.2f)" % (meter.spent, meter.cap))
            break
        cat_key, kind, what, angle = pick_category(state["cursor"])
        state["cursor"] += 1                                          # the cursor moves on even when the call fails: no category is retried forever
        calls += 1
        recent = [r["domain"] for r in cand["sites"][-400:] if r.get("category") == cat_key][-25:]
        try:
            text = ask_model(SYSTEM, user_prompt(cat_key, what, angle, recent), meter, post=post)
        except CapReached:
            print("list-builder: daily cost cap reached ($%.2f of $%.2f)" % (meter.spent, meter.cap))
            break
        except Exception as e:
            print("list-builder: %s call failed (%s)" % (cat_key, type(e).__name__))   # class name only: never an error body
            write_json(STATE_PATH, state)
            continue
        day["calls"] += 1
        items = parse_rows(text)
        stats["asked"] += 1
        stats["raw"] += len(items)
        todo, seen_here = [], set()
        for it in items:
            url = norm_url(it.get("url"))
            if not url or "inayaflow.ai" in url:
                continue
            if is_spam(it.get("name"), url, it.get("note")):
                stats["spam"] += 1
                continue
            k = dedupe_key(url)
            if k in known or k in seen_here:
                stats["dup"] += 1
                continue
            seen_here.add(k)
            it["_url"], it["_key"] = url, k
            todo.append(it)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            got = list(ex.map(lambda it: check_url(it["_url"], getter), todo))
        for it, res in zip(todo, got):
            k = it["_key"]
            if res is None:
                stats["dead"] += 1
                rejected.append(k)
                continue
            http, final, page = res
            fkey = dedupe_key(norm_url(final) or it["_url"])
            if fkey != k and fkey in known:                             # a redirect to a place we already hold
                stats["dup"] += 1
                rejected.append(k)
                continue
            if is_spam(it.get("name"), final, c.html_text(page, 1500)):
                stats["spam"] += 1
                rejected.append(k)
                continue
            known.add(k)
            known.add(fkey)
            new_rows.append(build_row(it, cat_key, kind, angle, today, http, final, page))
        print("list-builder: %s '%s' asked %d, kept so far %d, spent $%.3f" % (cat_key, angle[:40], len(items), len(new_rows), meter.spent))
    # room left in the day for added rows
    room = max(0, MAX_NEW_PER_DAY - day["added"])
    new_rows = new_rows[:room]
    by_cat = {}
    for r in new_rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    stats["added"] = len(new_rows)
    if not DRY:
        cand["sites"].extend(new_rows)
        cand["generated"] = max(str(cand.get("generated", "")), today)
        write_json(CAND_PATH, cand)
        day["added"] += len(new_rows)
        for k, v in by_cat.items():
            day["by_category"][k] = day["by_category"].get(k, 0) + v
    state["rejected"] = list(dict.fromkeys(state["rejected"] + rejected))
    total = len(cand["sites"])
    line = summary_line(day["added"] if not DRY else len(new_rows), total, day["by_category"] if not DRY else by_cat)
    day["summary"], day["total"] = line + (" [dry run: nothing saved]" if DRY else ""), total
    state["last_summary"] = day["summary"]
    trim_state(state, today)
    write_json(STATE_PATH, state)
    print(day["summary"])
    print("list-builder: this run %s; cost today $%.3f of $%.2f; calls %d" % (
        ", ".join("%s %d" % (k, v) for k, v in stats.items()), meter.spent, meter.cap, meter.calls))
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        try:
            with open(out, "a", encoding="utf8") as f:
                f.write("%s\n" % day["summary"])
        except Exception:
            pass
    return {"status": "DONE", "rows": new_rows, "summary": day["summary"], "stats": stats}


if __name__ == "__main__":
    r = run()
    sys.exit(0 if r["status"] in ("DONE",) else 1)
