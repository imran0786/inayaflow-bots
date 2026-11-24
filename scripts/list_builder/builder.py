#!/usr/bin/env python3
"""LIST BUILDER (24 Nov 2026): adds about 150-200 NEW candidate places a day to data/directories_candidates.json.

Imran 24 Nov 2026: "We should have at least 2,000, 3,000, going up to 5,000 list of platforms, directories, tools, guides, blog forms,
anything, everything ... variety of places ... I want to know how much list has increased today."

Flow per run: rotate through 12 categories (a cursor kept in data/list_builder_state.json) -> Claude Sonnet + web search lists real places
for one category + angle -> code filters spam, de-duplicates by registrable domain against EVERY list we hold -> every URL is opened
(GET only, HTTP 200-399 or dropped) -> rows are added with status TODO (the directory agent may try them), TODO outreach (for outreach,
never form submission) or HOLD paid. Writes the one-line summary "list-builder: +N new (total T; by category ...)" to the state file.

HARD LIMITS: $3.00 a UTC day over ALL Sonnet runs (env LIST_BUILDER_DAY_USD_CAP; Imran 24 Nov: "Yes, raise it to $3 a day"; the cost of a call is booked
BEFORE it goes out and the state is saved, so a killed run still has it on record); no call starts unless spent + its reserve fits under the cap.

DEEPSEEK TWIN (24 Nov 2026, Imran: "take one helper as well from DeepSeek and tell it to do the same duty ... let's see who do the job better"): the SAME
code runs with env LIST_BUILDER_PROVIDER=deepseek (workflow list-builder-deepseek.yml). Same category rotation (shared cursor), same prompt text (SYSTEM +
user_prompt; DeepSeek only gets one extra note that it has no web search), same spam filter, same de-duplication against every list we hold (and against
rows Sonnet added), same live-URL check, same data/directories_candidates.json and row format. HONEST LIMIT: DeepSeek has NO web search. It is asked for
real places it KNOWS, with exact URLs; it can invent or misremember URLs. Only the code's live-URL check (HTTP 200-399), the spam filter and the
de-duplication keep rows real and new; the "dead" counter in the comparison shows how many guesses failed. Its money cap is SEPARATE:
LIST_BUILDER_DEEPSEEK_DAY_USD_CAP (default 1.00), NOT part of the $16.98 a day that the LISTED robots together may spend (README).
SEPARATE SHEETS (Imran 24 Nov: "a different separate sheet so that we can analyze the performance of each helper or robot"): Sonnet writes only
data/directories_candidates.json, data/list_builder_state.json, data/needs_signup_sonnet.md, data/list_builder_report_sonnet.md; DeepSeek writes only the
*_deepseek twins of those. Each provider READS both candidates files, so nothing is ever repeated across the two; no state file is shared.
data/list_builder_compare.md is the side-by-side summary built from both states.
Every new row carries found_by (sonnet|deepseek), proof (the directory agent's follow_proof verdict FOLLOWED / NOFOLLOW_ONLY / NOINDEX / UNKNOWN, or
UNCHECKED when the per-run fetch or time limit was used up) and needs_signup. data/needs_signup.md lists rows that need an account (proof FOLLOWED or
UNKNOWN or UNCHECKED, up to 120 proof checks per run) so Imran can do the sign-ups at the end (one sheet per provider).
Sends nothing, signs up nowhere, fills no form, pays nothing. PUBLIC repo: prints counts, category keys and cost only (never a key, never
a model answer, never an error body).
"""
import concurrent.futures
import datetime
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts", "reports"))
import common as c  # noqa: E402  (http_get, html_text)

DATA = os.environ.get("LIST_BUILDER_DATA", os.path.join(ROOT, "data"))
CAND_PATH = os.path.join(DATA, "directories_candidates.json")
STATE_PATH = os.path.join(DATA, "list_builder_state.json")
SIGNUP_PATH = os.path.join(DATA, "needs_signup_sonnet.md")
REPORT_PATH = os.path.join(DATA, "list_builder_report_sonnet.md")
# SEPARATE FILES PER PROVIDER (Imran 24 Nov: "a different separate sheet so that we can analyze the performance of each helper or robot"):
# DeepSeek writes ONLY to the *_deepseek files; Sonnet ONLY to the files above. Both READ both candidates files (dedupe across the two).
DS_CAND_PATH = os.path.join(DATA, "directories_candidates_deepseek.json")
DS_STATE_PATH = os.path.join(DATA, "list_builder_state_deepseek.json")
DS_SIGNUP_PATH = os.path.join(DATA, "needs_signup_deepseek.md")
DS_REPORT_PATH = os.path.join(DATA, "list_builder_report_deepseek.md")
COMPARE_PATH = os.path.join(DATA, "list_builder_compare.md")        # side-by-side summary built from both states


def paths(provider):
    """The provider's OWN files (cand, state, signup, report) and the OTHER provider's candidates / state (read only). Resolved at call time."""
    if provider == "deepseek":
        return {"cand": DS_CAND_PATH, "other": CAND_PATH, "state": DS_STATE_PATH, "other_state": STATE_PATH, "signup": DS_SIGNUP_PATH, "report": DS_REPORT_PATH}
    return {"cand": CAND_PATH, "other": DS_CAND_PATH, "state": STATE_PATH, "other_state": DS_STATE_PATH, "signup": SIGNUP_PATH, "report": REPORT_PATH}
def _env_float(name, default):
    """Float from the environment; an empty or garbled value falls back to the default (code QA 1a)."""
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


PROVIDER = os.environ.get("LIST_BUILDER_PROVIDER", "claude").strip().lower()
if PROVIDER not in ("claude", "deepseek"):
    PROVIDER = "claude"
FOUND_BY = {"claude": "sonnet", "deepseek": "deepseek"}
DRY = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
MODEL = os.environ.get("LIST_BUILDER_MODEL", "claude-sonnet-5-5")
DAY_CAP = _env_float("LIST_BUILDER_DAY_USD_CAP", "3.00")   # HARD cap per UTC day over all Sonnet runs (24 Nov: 1.00 -> 3.00, Imran "raise it to $3 a day"; listed robots now $16.98 a day)
DEEPSEEK_DAY_CAP = _env_float("LIST_BUILDER_DEEPSEEK_DAY_USD_CAP", "1.00")   # SEPARATE cap of the DeepSeek twin; not part of the $16.98 of the listed robots
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_BASE = os.environ.get("DEEPSEEK_BASE", "https://api.deepseek.com")
# DeepSeek price per million tokens, READ 24 Nov 2026 from the official page https://api-docs.deepseek.com/quick_start/pricing
# (deepseek-flash: peak $0.30 in (cache miss) / $0.006 cache hit / $1.20 out; off-peak is half). Peak prices are used for every call (over-estimate, safe).
DS_PRICE_IN, DS_PRICE_CACHE, DS_PRICE_OUT = 0.30, 0.006, 1.20
DS_RESERVE = 0.02       # most one DeepSeek request can cost: ~12k tokens in x $0.30/M + 4000 out x $1.20/M = ~$0.009; 0.02 is a safe booking
PROOF_MAX_FETCHES = int(os.environ.get("LIST_BUILDER_PROOF_FETCHES", "120"))    # follow_proof checks per run (each makes up to 4 page fetches)
PROOF_SECONDS = int(os.environ.get("LIST_BUILDER_PROOF_SECONDS", "480"))       # time limit for all proof checks of one run
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


SIGNUP_RE = re.compile(r"sign[\s-]*up|register|create\s+(an?\s+)?account|log[\s-]*in\s+to\s+(submit|post|list)|sign\s*in\s+to\s+(submit|post|list)|login\s+required", re.I)
SUBMIT_RE = re.compile(r"submit|add\s+(your|a)\b|list\s+your|suggest|post\s+your", re.I)


def needs_signup_flag(item, page):
    """True when the model says an account is needed, or the live page shows sign-up / register / login wording together with a submit idea."""
    if str(item.get("signup", "")).strip().lower().startswith(("yes", "true")):
        return True
    p = (page or "")[:60000]
    return bool(SIGNUP_RE.search(p)) and bool(SUBMIT_RE.search(p))


def build_row(item, cat_key, kind, angle, today, http, final, page, provider="claude"):
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
            "checked": today, "status": status, "type": str(item.get("type", ""))[:30],
            "found_by": FOUND_BY.get(provider, provider), "proof": "UNCHECKED", "needs_signup": needs_signup_flag(item, page)}


def summary_line(added, total, by_cat):
    parts = ", ".join("%s %d" % (k, by_cat[k]) for k in sorted(by_cat, key=lambda k: (-by_cat[k], k)) if by_cat[k])
    return "list-builder: +%d new (total %d; by category %s)" % (added, total, parts or "none")


# ---------- state ----------

def read_json(path, default):
    try:
        return json.load(open(path, encoding="utf8"))
    except Exception:
        return default


def write_text(path, text):
    """Atomic like write_json: temp file in the same folder, then rename (a killed run never leaves a half-written file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, data, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False, **kw)
        f.write("\n")
    os.replace(tmp, path)


def load_state(path=None):
    st = read_json(path or STATE_PATH, {})
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


def deepseek_cost(usage):
    """USD of one DeepSeek chat call from its usage block (prompt_tokens, prompt_cache_hit_tokens, completion_tokens) at the peak prices above."""
    u = usage or {}
    pt, hit, out = (int(u.get(k, 0) or 0) for k in ("prompt_tokens", "prompt_cache_hit_tokens", "completion_tokens"))
    hit = min(hit, pt)
    return ((pt - hit) * DS_PRICE_IN + hit * DS_PRICE_CACHE + out * DS_PRICE_OUT) / 1e6


def call_cost(usage):
    u = usage or {}
    pin, pout, pcr = PRICES[MODEL]
    fresh, cr, cw, out = (int(u.get(k, 0) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
    s = int(((u.get("server_tool_use") or {}).get("web_search_requests", 0)) or 0)
    return (fresh * pin + cr * pcr + cw * pin * 1.25 + out * pout) / 1e6 + s * SEARCH_PRICE


class Meter:
    """The day's spend, kept in the provider's OWN state file (state["spent"]; Sonnet and DeepSeek have separate files and separate caps).
    book() reserves BEFORE the request; settle() replaces the reserve by the real cost."""

    def __init__(self, state, today, cap=None, save=None, provider="claude"):
        self.state, self.today, self.provider = state, today, provider
        ds = provider == "deepseek"
        self.key = "spent"
        self.reserve = DS_RESERVE if ds else RESERVE
        self.cost_fn = deepseek_cost if ds else call_cost
        self.cap = (DEEPSEEK_DAY_CAP if ds else DAY_CAP) if cap is None else cap
        self.save = save or (lambda: None)
        self.calls = 0
        self.state.setdefault(self.key, {})

    @property
    def spent(self):
        return float(self.state[self.key].get(self.today, 0.0) or 0.0)

    def room_for_call(self):
        return self.spent + self.reserve <= self.cap + 1e-9

    def book(self):
        if not self.room_for_call():
            raise CapReached("daily cost cap: %.3f spent + %.2f reserve > %.2f" % (self.spent, self.reserve, self.cap))
        self.state[self.key][self.today] = round(self.spent + self.reserve, 4)
        self.calls += 1
        self.save()

    def settle(self, usage):
        # an answer WITHOUT a usage block keeps the booked reserve as its cost (never 0: a silent reply must not beat the cap)
        real = self.cost_fn(usage) if isinstance(usage, dict) and usage else self.reserve
        self.state[self.key][self.today] = round(max(0.0, self.spent - self.reserve + real), 4)
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


def ask_deepseek(system, user, meter, post=None):
    """DeepSeek chat (no web search, no tools). One request, metered and capped like a Sonnet call. Returns the answer text."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key and post is None:
        raise RuntimeError("no DEEPSEEK_API_KEY")
    headers = {"Authorization": "Bearer " + (key or ""), "content-type": "application/json"}
    meter.book()                                                       # reserve booked and SAVED before the request goes out
    data = (post or _post_json)(DEEPSEEK_BASE + "/chat/completions",
                                {"model": DEEPSEEK_MODEL, "max_tokens": MAX_TOKENS,
                                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, headers)
    meter.settle(data.get("usage"))
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    return str(msg.get("content") or "")


SYSTEM = """You build a list of real web places where a free tool for TikTok creators (InayaFlow, https://inayaflow.ai) can get a listing, a mention or a link. You research with web search and answer with JSON lines only.
RULES:
- Only real, live sites that you saw in search results. Never invent a site or a URL. Give the page where a person submits / suggests / contacts (the submit, suggest, write-for-us, contact or list page); if you only saw the site's home page, give the home page.
- Prefer lesser-known, long-tail places over the famous giants. Skip anything that sells backlinks, runs link farms or PBNs, is casino / betting / adult / loans / crypto-signals, or is a pure 'buy a sponsored post' site. Skip sites that need a payment to be listed unless you mark them free = "no".
- Never list inayaflow.ai. No duplicates. No social profile pages of single people.
- "free" is "yes" only if you SAW that listing / submission is free, "no" if you saw it is paid only, otherwise "unknown".
OUTPUT: first line "LIST DONE", then ONLY JSON lines (no markdown fence, no comment), then a last line "COUNT n". One object per line:
{"name": "<site name>", "url": "<https url>", "type": "<directory|launch_site|roundup|awesome_list|review_platform|guest_post|resource_page|community|podcast|journalist|edu_guide|country_directory>", "free": "yes|no|unknown", "signup": "yes|no|unknown (yes = an account is needed to submit)", "note": "<12 words max: what it is>"}
Give up to 40 places. If you find fewer, give fewer; never pad."""


DEEPSEEK_NOTE = """

NOTE FOR THIS RUN: you have NO web search and cannot open pages. Ignore every 'search' / 'saw in search results' wording above and list ONLY real, existing places you KNOW from your training, with the exact URL you are sure of (if you are not sure of the exact submit page, give the site's home page). Never invent a site or a URL: our code opens every URL and drops dead ones. Use free = "unknown" unless you are sure."""


def build_prompts(provider, cat_key, what, angle, avoid):
    """(system, user) for a provider. The text is the SAME for both; DeepSeek only gets DEEPSEEK_NOTE appended (it has no web search)."""
    return SYSTEM + (DEEPSEEK_NOTE if provider == "deepseek" else ""), user_prompt(cat_key, what, angle, avoid)


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


def load_follow_proof():
    """The directory agent's follow_proof (scripts/directory_agent/agent.py, merged 24 Nov), REUSED, not copied. None when it cannot be loaded."""
    try:
        import io
        import contextlib
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):     # nothing the module prints can reach the public log
            return _load_follow_proof()
    except Exception:
        return None


def _load_follow_proof():
    try:
        spec = importlib.util.spec_from_file_location("directory_agent_for_proof", os.path.join(ROOT, "scripts", "directory_agent", "agent.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.follow_proof
    except Exception:
        return None


COUNTER_KEYS = ("calls", "cost", "proposed", "spam", "dup", "dead", "added", "followed", "nofollow", "noindex", "unknown", "needs_signup")
PROOF_COUNTER = {"FOLLOWED": "followed", "NOFOLLOW_ONLY": "nofollow", "NOINDEX": "noindex", "UNKNOWN": "unknown"}


def pcounters(day, provider):
    """The provider's counters for the day (calls, cost, URLs proposed, dropped spam / dup / dead, new rows, proof verdicts, needs_signup rows)."""
    p = day.setdefault("providers", {}).setdefault(provider, {})
    for k in COUNTER_KEYS:
        p.setdefault(k, 0.0 if k == "cost" else 0)
    return p


def compare_line(days, extra=None):
    """'list-builder: sonnet +N new (F followed, $x) | deepseek +M new (G followed, $y)'. days = {provider: that provider's day record} (each from
    its own state file); extra = {provider: counters} to add on top (dry runs)."""
    parts = []
    for prov in ("claude", "deepseek"):
        p = dict(((days.get(prov) or {}).get("providers") or {}).get(prov) or {})
        if not p and prov == "deepseek":
            continue
        for k, v in ((extra or {}).get(prov) or {}).items():
            p[k] = p.get(k, 0) + v
        parts.append("%s +%d new (%d followed, $%.2f)" % (FOUND_BY[prov], p.get("added", 0), p.get("followed", 0), p.get("cost", 0.0)))
    return "list-builder: " + " | ".join(parts or ["sonnet +0 new (0 followed, $0.00)"])


def write_needs_signup(sites, path=None):
    """The provider's sign-up sheet: rows that need an account AND whose proof is FOLLOWED, UNKNOWN or UNCHECKED (not checked yet), newest first. Imran does the sign-ups at the end."""
    rows = [(i, s) for i, s in enumerate(sites) if s.get("needs_signup") is True and s.get("proof") in ("FOLLOWED", "UNKNOWN", "UNCHECKED")]
    rows.sort(key=lambda t: (str(t[1].get("added", "")), t[0]), reverse=True)
    out = ["# Sign-ups to do at the end (list builder)", "",
           "Places that need an account to list or post, with a followed link (FOLLOWED), not yet known (UNKNOWN) or not checked yet (UNCHECKED). Newest first. Rebuilt by the list builder on every live run.", "",
           "| Site | URL | Category | Found by | Proof | Added |", "|---|---|---|---|---|---|"]
    for _, s in rows[:600]:
        out.append("| %s | %s | %s | %s | %s | %s |" % (str(s.get("name") or s.get("domain"))[:60].replace("|", "/"), s.get("submit_url", ""), s.get("category", ""),
                                                    s.get("found_by", ""), "UNCHECKED (proof not checked yet)" if s.get("proof") == "UNCHECKED" else s.get("proof", ""), s.get("added", "")))
    out.append("")
    write_text(path or SIGNUP_PATH, "\n".join(out))
    return len(rows)


REPORT_COLS = ("calls", "cost", "proposed", "dead", "spam", "dup", "added", "followed", "nofollow", "noindex", "unknown", "needs_signup")


def write_report(provider, days, path=None):
    """data/list_builder_report_<provider>.md: ONE row per day for that provider alone (last 30 days), newest first. (Submitted / live per provider
    by the directory agent can be added later: the rows carry found_by.)"""
    name = "Sonnet (web search)" if provider == "claude" else "DeepSeek (no web search)"
    head = ["# List builder daily sheet: %s" % name, "",
            "One row per UTC day. calls, cost in USD, proposed URLs, dropped (dead / spam / duplicate), new rows added, proof verdicts of the new rows "
            "(followed / nofollow / noindex / unknown), rows that need a sign-up.", "",
            "| Date | " + " | ".join(REPORT_COLS) + " |", "|---|" + "---|" * len(REPORT_COLS)]
    rows = []
    for d in sorted(days, reverse=True)[:30]:
        p = ((days[d] or {}).get("providers") or {}).get(provider)
        if p:
            rows.append("| %s | %s |" % (d, " | ".join(("%.2f" % p.get(k, 0.0)) if k == "cost" else str(p.get(k, 0)) for k in REPORT_COLS)))
    write_text(path or REPORT_PATH, "\n".join(head + rows) + "\n")


COMPARE_HEAD = ["# List builder: Sonnet vs DeepSeek, per day (summary; the full sheets are list_builder_report_sonnet.md and list_builder_report_deepseek.md)", "",
                "Each cell: calls / $ / proposed URLs / dropped dead, spam, duplicate / new rows / FOLLOWED / needs sign-up.", "",
                "| Date | Sonnet (web search) | DeepSeek (no web search) |", "|---|---|---|"]


def _cell(p):
    if not p:
        return "-"
    return "calls %d / $%.2f / proposed %d / dead %d, spam %d, dup %d / new %d / followed %d / signup %d" % (
        p.get("calls", 0), p.get("cost", 0.0), p.get("proposed", 0), p.get("dead", 0), p.get("spam", 0), p.get("dup", 0),
        p.get("added", 0), p.get("followed", 0), p.get("needs_signup", 0))


def write_compare(days_by_provider):
    """data/list_builder_compare.md: side by side, one row per day, newest first, last 14 days. days_by_provider = {"claude": days, "deepseek": days},
    each read from that provider's own state file (this function reads, never writes, the other provider's state)."""
    cl, ds = days_by_provider.get("claude") or {}, days_by_provider.get("deepseek") or {}
    rows = []
    for d in sorted(set(cl) | set(ds), reverse=True)[:14]:
        a = ((cl.get(d) or {}).get("providers") or {}).get("claude")
        b2 = ((ds.get(d) or {}).get("providers") or {}).get("deepseek")
        if a or b2:
            rows.append("| %s | %s | %s |" % (d, _cell(a), _cell(b2)))
    write_text(COMPARE_PATH, "\n".join(COMPARE_HEAD + rows) + "\n")


def run_proofs(rows, proof, workers=8):
    """Set row['proof'] with the follow_proof verdict for up to PROOF_MAX_FETCHES rows within PROOF_SECONDS; the rest stay UNCHECKED."""
    if proof is None or not rows:
        return 0
    deadline = time.time() + PROOF_SECONDS
    todo = rows[:PROOF_MAX_FETCHES]

    def one(r):
        if time.time() > deadline:
            return None
        try:
            return proof(r.get("final_url") or r["submit_url"])[0]
        except Exception:
            return "UNKNOWN"
    import io
    import contextlib
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):           # follow_proof output (if any) is swallowed: public log
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            verdicts = list(ex.map(one, todo))
    n = 0
    for r, v in zip(todo, verdicts):
        if v in ("FOLLOWED", "NOFOLLOW_ONLY", "NOINDEX", "UNKNOWN"):
            r["proof"] = v
            n += 1
    return n


def run(post=None, getter=None, today=None, workers=8, provider=None, proof=None):
    getter = getter or c.http_get
    today = today or utc_today()
    provider = provider or PROVIDER
    ds = provider == "deepseek"
    tag = "list-builder-deepseek" if ds else "list-builder"
    if ds and post is None and not os.environ.get("DEEPSEEK_API_KEY"):
        print("list-builder-deepseek: no key, skipped")
        return {"status": "SKIPPED"}
    if proof is None:
        proof = load_follow_proof()
    P = paths(provider)
    state = load_state(P["state"])
    own = read_json(P["cand"], None)
    other = read_json(P["other"], None)
    if ds and not os.path.exists(P["cand"]):
        own = {"generated": "", "sites": []}                           # DeepSeek's own file is created by its first live run, and ONLY when it does not exist:
                                                                       # an existing but unreadable file fails closed below, so old rows are never dropped
    if not ds and not os.path.exists(P["other"]):
        other = {"sites": []}                                          # Sonnet: no DeepSeek file yet = nothing there to dedupe against
    elif not ds and not (isinstance(other, dict) and isinstance(other.get("sites"), list)):
        print("%s: the DeepSeek candidates file is unreadable, dedupe runs without it" % tag)   # count-only line, no content
        other = {"sites": []}
    if not (isinstance(own, dict) and isinstance(own.get("sites"), list) and isinstance(other, dict) and isinstance(other.get("sites"), list)):
        print("%s: a candidates file is missing or unreadable, nothing runs (fail closed)" % tag)
        return {"status": "NO_DATA"}
    all_sites = own["sites"] + other["sites"]                          # dedupe and the 'avoid' list use BOTH providers' rows
    day = state["days"].setdefault(today, {"added": 0, "by_category": {}, "runs": 0, "calls": 0})
    pc = pcounters(day, provider)
    meter = Meter(state, today, save=lambda: write_json(P["state"], state), provider=provider)
    extra = [p for p in os.environ.get("LIST_BUILDER_KNOWN_FILES", "").split(os.pathsep) if p]
    known = known_keys({"sites": all_sites}, extra) | set(state["rejected"])
    new_rows, rejected = [], []
    day["runs"] += 1
    stats = {"asked": 0, "raw": 0, "spam": 0, "dup": 0, "dead": 0, "added": 0}
    calls = 0
    while calls < MAX_CALLS_PER_RUN and pc["added"] + len(new_rows) < TARGET_NEW and pc["added"] + len(new_rows) < MAX_NEW_PER_DAY:
        if not meter.room_for_call():
            print("%s: daily cost cap reached ($%.2f of $%.2f)" % (tag, meter.spent, meter.cap))
            break
        cat_key, kind, what, angle = pick_category(state["cursor"])
        state["cursor"] += 1                                          # the cursor moves on even when the call fails: no category is retried forever
        calls += 1
        recent = [r["domain"] for r in all_sites[-400:] if r.get("category") == cat_key][-25:]
        system, user = build_prompts(provider, cat_key, what, angle, recent)
        try:
            text = (ask_deepseek if ds else ask_model)(system, user, meter, post=post)
        except CapReached:
            print("%s: daily cost cap reached ($%.2f of $%.2f)" % (tag, meter.spent, meter.cap))
            break
        except Exception as e:
            print("%s: %s call failed (%s)" % (tag, cat_key, type(e).__name__))   # class name only: never an error body
            write_json(P["state"], state)
            continue
        day["calls"] += 1
        pc["calls"] += 1
        items = parse_rows(text)
        stats["asked"] += 1
        stats["raw"] += len(items)
        pc["proposed"] += len(items)
        todo, seen_here = [], set()
        for it in items:
            url = norm_url(it.get("url"))
            if not url or "inayaflow.ai" in url:
                continue
            if is_spam(it.get("name"), url, it.get("note")):
                stats["spam"] += 1
                pc["spam"] += 1
                continue
            k = dedupe_key(url)
            if k in known or k in seen_here:
                stats["dup"] += 1
                pc["dup"] += 1
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
                pc["dead"] += 1
                rejected.append(k)
                continue
            http, final, page = res
            fkey = dedupe_key(norm_url(final) or it["_url"])
            if fkey != k and fkey in known:                             # a redirect to a place we already hold
                stats["dup"] += 1
                pc["dup"] += 1
                rejected.append(k)
                continue
            if is_spam(it.get("name"), final, c.html_text(page, 1500)):
                stats["spam"] += 1
                pc["spam"] += 1
                rejected.append(k)
                continue
            known.add(k)
            known.add(fkey)
            new_rows.append(build_row(it, cat_key, kind, angle, today, http, final, page, provider))
        print("%s: %s '%s' asked %d, kept so far %d, spent $%.3f" % (tag, cat_key, angle[:40], len(items), len(new_rows), meter.spent))
    # room left in the day for added rows
    room = max(0, MAX_NEW_PER_DAY - pc["added"])
    new_rows = new_rows[:room]
    checked = run_proofs(new_rows, proof, workers=workers)          # follow_proof on the new rows (cap PROOF_MAX_FETCHES, time limit PROOF_SECONDS)
    by_cat = {}
    for r in new_rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    stats["added"] = len(new_rows)
    verdicts = {k: sum(1 for r in new_rows if r["proof"] == v) for v, k in PROOF_COUNTER.items()}
    followed = verdicts["followed"]
    signups = sum(1 for r in new_rows if r["needs_signup"])
    pc["cost"] = round(meter.spent, 4)
    run_extra = None
    if not DRY:
        own["sites"].extend(new_rows)                               # ONLY the provider's own candidates file is written
        own["generated"] = max(str(own.get("generated", "")), today)
        write_json(P["cand"], own)
        day["added"] += len(new_rows)
        pc["added"] += len(new_rows)
        for k, v in verdicts.items():
            pc[k] += v
        pc["needs_signup"] += signups
        for k, v in by_cat.items():
            day["by_category"][k] = day["by_category"].get(k, 0) + v
        write_needs_signup(own["sites"], P["signup"])
    else:
        run_extra = {provider: {"added": len(new_rows), "followed": followed}}
    state["rejected"] = list(dict.fromkeys(state["rejected"] + rejected))
    total = len(all_sites) + (0 if DRY else len(new_rows))
    cat_line = summary_line(day["added"] if not DRY else len(new_rows), total, day["by_category"] if not DRY else by_cat)
    day["category_summary"] = cat_line
    other_state = read_json(P["other_state"], {})
    other_prov = "claude" if ds else "deepseek"
    days_by = {provider: day, other_prov: ((other_state.get("days") or {}).get(today) if isinstance(other_state, dict) else None) or {}}
    day["summary"] = compare_line(days_by, run_extra) + (" [dry run: nothing saved]" if DRY else "")
    day["total"] = total
    state["last_summary"] = day["summary"]
    trim_state(state, today)
    write_json(P["state"], state)
    if not DRY:
        write_report(provider, state["days"], P["report"])
        o_days = other_state.get("days") if isinstance(other_state, dict) else {}
        write_compare({provider: state["days"], other_prov: o_days or {}})
    print(day["summary"])
    print(cat_line)
    print("%s: this run %s; proof checked %d, FOLLOWED %d, needs sign-up %d; cost today $%.3f of $%.2f; calls %d" % (
        tag, ", ".join("%s %d" % (k, v) for k, v in stats.items()), checked, followed, signups, meter.spent, meter.cap, meter.calls))
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
    sys.exit(0 if r["status"] in ("DONE", "SKIPPED") else 1)
