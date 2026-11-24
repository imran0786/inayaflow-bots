#!/usr/bin/env python3
"""PLATFORM POSTER (24 Nov 2026): ONE short, unique article a day to EACH of WordPress.com, Blogger and Notion, each linking (plain <a href>, no rel)
to ONE inayaflow.ai page, rotating over the 9 tool pages, the hub guides and the homepage.

Modelled on scripts/dev_poster/poster.py and REUSES its writer (Claude Sonnet + web search/fetch), cost meter, QA (Haiku), 7-word overlap and link_rel code.
HARD LIMITS: daily USD cap over ALL runs (PLATFORM_DAY_USD_CAP, checked before each model call, kept in the state file); at most 2 dry runs a day;
one article per platform per day; nothing runs unless the private state was restored (PLATFORM_STATE_OK); NOTHING is published unless the run is not
dry AND the repository switch PLATFORM_POSTER_LIVE is "true". Public log = platform, page slug, status words, cost. Never keys, never article text.
Auth (secrets from the environment only, never printed): WordPress.com password grant -> bearer; Blogger refresh token -> access token; Notion token.
"""
import html, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
import importlib.util  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "scripts", "reports"))
import common as c  # noqa: E402
_spec = importlib.util.spec_from_file_location("dev_poster_mod", os.path.join(ROOT, "scripts", "dev_poster", "poster.py"))
D = importlib.util.module_from_spec(_spec)      # the DEV poster (same file name as this one, so it is loaded under its own name): writer, meter, QA, overlap, link_rel
_spec.loader.exec_module(D)

OPS = os.environ.get("PLATFORM_OPS_DIR", os.path.join(ROOT, "ops"))
STATE_PATH = os.path.join(OPS, "platform_poster_state.json")
LOG_PATH = os.path.join(OPS, "platform_poster_log.md")
DRAFT_DIR = os.path.join(OPS, "platform_drafts")
DRY = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
DAY_CAP = float(os.environ.get("PLATFORM_DAY_USD_CAP", "0.90"))
MAX_DRY_PER_DAY = 2
PAST_KEPT = 30
NOTION_PARENT = os.environ.get("NOTION_PARENT_PAGE_ID", "420a26c43c8d80e1bb0bc861e6304200")
NOTION_SITE = "https://breezy-split-a72.notion.site/"
NOTION_VERSION = os.environ.get("NOTION_VERSION", "2026-03-11")      # newest concrete version in Notion's docs (24 Nov); override with the repo variable if it misbehaves
WP_SITE = os.environ.get("WPCOM_SITE", "inayaflow.wordpress.com")
BLOG_URL = os.environ.get("BLOGGER_URL", "https://inayaflow.blogspot.com/")
UA = "inayaflow-platform-poster/1.0 (+https://inayaflow.ai)"
D.MAX_SEARCHES, D.MAX_FETCHES, D.WRITER_MAX_TOKENS = 2, 2, 2500        # short articles: cheaper than the DEV poster
PLATFORMS = ["wordpress", "blogger", "notion"]

# (slug, url, kind, what it is, topic): tools first, then hub guides, then the homepage
TARGETS = [p for p in D.PAGES if p[2] == "tool"] + [p for p in D.PAGES if p[2] == "guide"] + [
    ("homepage", "https://inayaflow.ai/", "home", "InayaFlow, free TikTok tools and guides for creators", "growing on TikTok with free tools")]

SPECS = {
    "wordpress": dict(words=(350, 650), style="a practical how-to for a creator: a clear problem, numbered or step-by-step advice, one worked example. Friendly blog tone."),
    "blogger": dict(words=(300, 600), style="a short question-and-answer explainer or checklist: start from one question people really ask, answer it plainly, end with a quick checklist."),
    "notion": dict(words=(220, 500), style="a compact reference note: one-paragraph summary, 'key points' as a bulleted list, then two short sections. Tight and scannable."),
}
LINK_RE = D.LINK_RE


# ---------- state ----------
def load_state():
    first = os.environ.get("PLATFORM_FIRST_RUN", "") == "true"
    if not os.path.exists(STATE_PATH):
        return {"posts": [], "attempts": {}} if first else None
    d = D.read_json(STATE_PATH, None)
    if not isinstance(d, dict) or not isinstance(d.get("posts", []), list):
        return None
    d.setdefault("posts", [])
    d.setdefault("attempts", {})
    return d


def live_switch_on():
    return os.environ.get("PLATFORM_POSTER_LIVE", "").strip().lower() == "true"


def log(line):
    os.makedirs(OPS, exist_ok=True)
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", encoding="utf8") as f:
        if new:
            f.write("# Platform poster log (private)\n\n| date (UTC) | platform | page | result | article URL | our link | cost | note |\n|---|---|---|---|---|---|---|---|\n")
        f.write(line.rstrip() + "\n")


def pick_targets(state, today, platforms=PLATFORMS, targets=TARGETS):
    """{platform: target}. The target used longest ago on ANY platform first (never used first, list order), then by that platform's own history; no two platforms share a target the same day."""
    last, anyp = {}, {}
    for e in state.get("posts", []):
        if e.get("platform") and e.get("slug") and e.get("date"):
            last[(e["platform"], e["slug"])] = e["date"]
            anyp[e["slug"]] = max(anyp.get(e["slug"], ""), e["date"])
    chosen, out = set(), {}
    for pl in platforms:
        cand = sorted((anyp.get(t[0], "0000-00-00"), last.get((pl, t[0]), "0000-00-00"), i, t) for i, t in enumerate(targets) if t[0] not in chosen)
        out[pl] = cand[0][3]
        chosen.add(cand[0][3][0])
    return out


# ---------- offline gate ----------
BANNED = D.BANNED + ["genie"]


def check_post(art, target_url, platform, page_full, source_texts, site_corpus="", others=(), past_shingles=None):
    """-> list of problems (empty = passes). art = dict(title, tags, body, sources). others = [(platform, plain body)] already written today."""
    p = []
    body, title = art.get("body", ""), art.get("title", "")
    lo, hi = SPECS[platform]["words"]
    wc = len(D.words(body))
    if not (lo <= wc <= hi):
        p.append("words %d outside %d-%d" % (wc, lo, hi))
    if not title or len(title) > 80:
        p.append("title missing or over 80 characters")
    links = LINK_RE.findall(body)
    ours = [u for _, u in links if "inayaflow.ai" in u]
    if len(ours) != 1 or ours[0].rstrip("/") != target_url.rstrip("/"):
        p.append("need exactly one inayaflow.ai link and it must be the target (%d found)" % len(ours))
    if re.search(r"inayaflow\.ai", re.sub(r"\]\(https?://[^)\s]+\)", "]", body), re.I):
        p.append("bare inayaflow.ai text outside a link")
    ext = [u for _, u in links if "inayaflow.ai" not in u]
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
    if re.search(r"(?<![\w/.])@[A-Za-z0-9_.]{2,}", body):
        p.append("contains an @handle")
    if re.search(r"\b(I|I'm|I've|I'll|I'd|my|me|mine|myself)\b", body):
        p.append("first person singular (use 'we')")
    corpus = " ".join(list(source_texts.values()) + [page_full])
    nums = {D.norm_num(m) for m in re.findall(r"\d[\d,\.]*\d|\d", corpus)}
    for para in re.split(r"\n\s*\n", body):
        if para.lstrip().startswith("#"):
            continue
        for m in D.STAT_RE.finditer(para):
            n = D.norm_num(m.group(0))
            if not n:
                continue
            if not [u for _, u in LINK_RE.findall(para) if "inayaflow.ai" not in u]:
                p.append("number '%s' in a paragraph with no source link" % m.group(0).strip()[:30])
            elif n not in nums:
                p.append("number '%s' not found in the fetched sources" % m.group(0).strip()[:30])
    plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    ov = D.seven_word_overlap(plain, site_corpus or page_full)
    if ov:
        p.append("7-word overlap with our site: '%s'" % ov)
    for name, other in others:
        ov = D.seven_word_overlap(plain, other)
        if ov:
            p.append("7-word overlap with today's %s article: '%s'" % (name, ov))
            break
    if past_shingles:
        mine = D.shingles(body)
        shared = len(mine & set(past_shingles))
        if shared >= 5 or (mine and shared / len(mine) >= 0.03):
            p.append("too similar to our earlier platform posts (%d shared 7-word runs)" % shared)
    paras = [x for x in re.split(r"\n\s*\n", body) if x.strip() and not x.lstrip().startswith("#")]
    at = [i for i, x in enumerate(paras) if target_url in x]
    if at and (at[0] == 0 or at[-1] == len(paras) - 1) and len(paras) > 3:
        p.append("our link is in the first or last paragraph (reads as an ad)")
    if len(re.findall(r"inayaflow", re.sub(r"\]\(https?://[^)\s]+\)", "]", body), re.I)) > 3:
        p.append("the InayaFlow name appears more than 3 times")
    return p[:12]


# ---------- writer ----------
def writer_system(platform):
    s = SPECS[platform]
    return ("""You write one short article for the company InayaFlow, to be posted on %s. Voice: "we" (never I, me, my, mine, myself). Plain, friendly, specific English. The product is called InayaFlow (never "Genie").
FORMAT OF THE ARTICLE: %s
RULES (all checked by code afterwards; a break means nothing is published):
- %d to %d words of Markdown (## headings, short paragraphs, "- " lists, [text](url) links, **bold**). Your OWN angle and wording: do not copy the InayaFlow page; add examples and reasoning of your own.
- Real numbers only, found with web_search / web_fetch in official or primary pages you OPENED. Every paragraph with a number, percent or amount needs a Markdown link to that page, and the number must be printed there exactly as you write it. If you cannot source a number leave it out (an article with no numbers and no external link is fine). Pages on tiktok.com/support or creator-academy usually come back empty: do not cite them.
- Facts about our own page may come ONLY from the PAGE TEXT; never copy 7 or more words in a row from it.
- Exactly ONE link to the target InayaFlow page, as [descriptive words](URL), placed where it truly helps (not in the first or last paragraph). Never write the bare text inayaflow.ai.
- No @usernames, no other creators' names, no hype, no "sign up now". Be honest: our tools are free helpers, not magic.
- Banned: delve, game-changer, landscape, tapestry, dive into, unlock, elevate, supercharge, revolutionize, seamlessly, cutting-edge, in conclusion, it's important to note, ever-evolving, look no further, to the next level, harness, embark, realm of, testament, leverage, must-have.
- Title: 80 characters or fewer, no clickbait.
OUTPUT FORMAT, exactly, nothing before or after:
TITLE: <title>
SOURCES: <url1> | <url2>   (or the word none)
===BODY===
<markdown body, no title line>""") % (platform.capitalize(), s["style"], s["words"][0], s["words"][1])


def parse_writer(text):
    m = re.search(r"TITLE:\s*(.+)\nSOURCES:\s*(.*)\n===BODY===\n(.*)", text or "", re.S)
    if not m:
        return None
    return {"title": m.group(1).strip(), "tags": [],
            "sources": [u.strip() for u in re.split(r"\s*\|\s*", m.group(2)) if u.strip().startswith("http")], "body": m.group(3).strip()}


# ---------- markdown -> HTML / Notion blocks ----------
INLINE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)|\*\*([^*]+)\*\*")
LIST_START = re.compile(r"([-*]|\d+[.)])\s+")


def inline_html(t):
    out, pos = [], 0
    for m in INLINE.finditer(t):
        out.append(html.escape(t[pos:m.start()], quote=False))
        if m.group(1):
            out.append('<a href="%s">%s</a>' % (html.escape(m.group(2), quote=True), html.escape(m.group(1), quote=False)))
        else:
            out.append("<strong>%s</strong>" % html.escape(m.group(3), quote=False))
        pos = m.end()
    out.append(html.escape(t[pos:], quote=False))
    return "".join(out)


def md_blocks(md):
    """Markdown -> list of ('h2'|'h3'|'p'|'ul'|'ol', text or [items])."""
    out = []
    for chunk in re.split(r"\n\s*\n", md.strip()):
        lines = [l for l in chunk.split("\n") if l.strip()]
        i = 0
        while i < len(lines):
            l = lines[i].strip()
            m = re.match(r"(#{1,4})\s+(.*)", l)
            if m:
                out.append(("h2" if len(m.group(1)) <= 2 else "h3", m.group(2)))
                i += 1
                continue
            if LIST_START.match(l):
                kind = "ul" if re.match(r"[-*]\s+", l) else "ol"
                items = []
                while i < len(lines) and LIST_START.match(lines[i].strip()):
                    items.append(LIST_START.sub("", lines[i].strip(), count=1))
                    i += 1
                out.append((kind, items))
                continue
            para = [l]
            i += 1
            while i < len(lines) and not (re.match(r"#{1,4}\s", lines[i].strip()) or LIST_START.match(lines[i].strip())):
                para.append(lines[i].strip())
                i += 1
            out.append(("p", " ".join(para)))
    return out


def to_html(md):
    h = []
    for kind, v in md_blocks(md):
        if kind in ("h2", "h3"):
            h.append("<%s>%s</%s>" % (kind, inline_html(v), kind))
        elif kind == "p":
            h.append("<p>%s</p>" % inline_html(v))
        else:
            h.append("<%s>%s</%s>" % (kind, "".join("<li>%s</li>" % inline_html(x) for x in v), kind))
    return "\n".join(h)


def rich_text(t):
    out, pos = [], 0

    def txt(s, link=None, bold=False):
        for i in range(0, len(s), 1900):
            o = {"type": "text", "text": {"content": s[i:i + 1900]}}
            if link:
                o["text"]["link"] = {"url": link}
            if bold:
                o["annotations"] = {"bold": True}
            out.append(o)
    for m in INLINE.finditer(t):
        if m.start() > pos:
            txt(t[pos:m.start()])
        if m.group(1):
            txt(m.group(1), link=m.group(2))
        else:
            txt(m.group(3), bold=True)
        pos = m.end()
    if pos < len(t):
        txt(t[pos:])
    return out or [{"type": "text", "text": {"content": " "}}]


def to_notion_blocks(md):
    bl = []
    for kind, v in md_blocks(md):
        if kind in ("h2", "h3"):
            t = "heading_2" if kind == "h2" else "heading_3"
            bl.append({"object": "block", "type": t, t: {"rich_text": rich_text(v)}})
        elif kind == "p":
            bl.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(v)}})
        else:
            t = "bulleted_list_item" if kind == "ul" else "numbered_list_item"
            for x in v:
                bl.append({"object": "block", "type": t, t: {"rich_text": rich_text(x)}})
    return bl[:100]


# ---------- platform APIs (secrets only from the environment) ----------
def _req(url, body=None, headers=None, form=False):
    h = dict(headers or {})
    h.setdefault("User-Agent", UA)
    data = None
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded" if form else "application/json")
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=60) as r:
        return json.load(r)


def publish_wordpress(art, req=None):
    """WordPress.com: password grant (username + application password) -> bearer -> wp/v2 posts; on 4xx (route not open to the token) the older v1.1 route."""
    req = req or _req
    tok = req("https://public-api.wordpress.com/oauth2/token", {
        "grant_type": "password", "client_id": os.environ.get("WPCOM_CLIENT_ID", ""), "client_secret": os.environ.get("WPCOM_CLIENT_SECRET", ""),
        "username": os.environ.get("WPCOM_USERNAME", ""), "password": os.environ.get("WPCOM_APP_PASSWORD", "")}, form=True)["access_token"]
    h = {"Authorization": "Bearer " + tok}
    content = to_html(art["body"])
    try:
        r = req("https://public-api.wordpress.com/wp/v2/sites/%s/posts" % WP_SITE, {"title": art["title"], "content": content, "status": "publish"}, h)
        return {"url": r.get("link") or "", "id": r.get("id")}
    except urllib.error.HTTPError as e:
        if e.code not in (400, 401, 403, 404):      # anything else may have created the post: do not try a second route
            raise
    r = req("https://public-api.wordpress.com/rest/v1.1/sites/%s/posts/new" % WP_SITE, {"title": art["title"], "content": content, "status": "publish"}, h, form=True)
    return {"url": r.get("URL") or "", "id": r.get("ID")}


def publish_blogger(art, req=None):
    req = req or _req
    tok = req("https://oauth2.googleapis.com/token", {
        "grant_type": "refresh_token", "client_id": os.environ.get("BLOGGER_CLIENT_ID", ""), "client_secret": os.environ.get("BLOGGER_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("BLOGGER_REFRESH_TOKEN", "")}, form=True)["access_token"]
    h = {"Authorization": "Bearer " + tok}
    blog = req("https://www.googleapis.com/blogger/v3/blogs/byurl?url=" + urllib.parse.quote(BLOG_URL, safe=""), None, h)["id"]
    r = req("https://www.googleapis.com/blogger/v3/blogs/%s/posts" % blog, {"kind": "blogger#post", "title": art["title"], "content": to_html(art["body"])}, h)
    return {"url": r.get("url") or "", "id": r.get("id")}


def publish_notion(art, req=None):
    req = req or _req
    h = {"Authorization": "Bearer " + os.environ.get("NOTION_TOKEN", ""), "Notion-Version": NOTION_VERSION}
    r = req("https://api.notion.com/v1/pages", {
        "parent": {"page_id": NOTION_PARENT},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": art["title"][:200]}}]}},
        "children": to_notion_blocks(art["body"])}, h)
    pid = (r.get("id") or "").replace("-", "")
    return {"url": (NOTION_SITE + pid) if pid else "", "id": r.get("id")}


PUBLISHERS = {"wordpress": publish_wordpress, "blogger": publish_blogger, "notion": publish_notion}
KEYS = {"wordpress": ["WPCOM_CLIENT_ID", "WPCOM_CLIENT_SECRET", "WPCOM_APP_PASSWORD", "WPCOM_USERNAME"],
        "blogger": ["BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET", "BLOGGER_REFRESH_TOKEN"], "notion": ["NOTION_TOKEN"]}


def own_entry(url, target_url):
    """Same shape as data/own_backlinks.json: the link checker reads {source, target path}."""
    return {"source": url, "target": urllib.parse.urlparse(target_url).path or "/"}


# ---------- main ----------
def run(post_ai=None, publishers=None, getter=None, sleeper=time.sleep):
    getter = getter or c.http_get
    publishers = publishers or PUBLISHERS
    today = D.utc_today()
    dry = DRY or not live_switch_on()          # publishing needs BOTH a non-dry run AND the repository switch (scheduled and manual runs alike)
    if os.environ.get("PLATFORM_STATE_OK", "") != "true":
        print("platform_poster: the private state could not be restored, nothing runs (fail closed)")
        return "NO_STATE"
    state = load_state()
    if state is None:
        print("platform_poster: the state file is missing or unreadable, nothing runs (fail closed)")
        return "NO_STATE"
    spent_before = float((state.get("spent") or {}).get(today, 0.0) or 0.0)
    dry_today = (state.get("dry") or {}).get(today, 0)
    if dry and dry_today >= MAX_DRY_PER_DAY:
        print("platform_poster: %d dry runs today already, stop" % dry_today)
        return "DRY_LIMIT"
    todo = []
    for pl in PLATFORMS:
        done = any(e.get("date") == today and e.get("platform") == pl and e.get("url") for e in state["posts"])
        pend = (state.get("pending") or {}).get(pl)
        if pend and pend.get("date") != today:
            state["pending"].pop(pl)
            pend = None
        if done or (pend and not dry):
            continue
        if not dry and state.get("attempts", {}).get(today + pl, 0) >= 2:
            continue
        if not dry and not all(os.environ.get(k) for k in KEYS[pl]):
            print("platform_poster: keys for %s missing, skipped" % pl)
            continue
        todo.append(pl)
    if not todo:
        print("platform_poster: nothing to do today")
        return "ALREADY_DONE"
    remaining = round(DAY_CAP - spent_before, 4)
    if remaining < D.WRITER_RESERVE + D.QA_RESERVE:
        print("platform_poster: the day's $%.2f is used up ($%.3f spent), stop" % (DAY_CAP, spent_before))
        return "DAY_CAP"
    meter = D.Meter(cap=remaining)

    def save():
        sp = state.setdefault("spent", {})
        sp[today] = round(spent_before + meter.cost, 4)
        for k in sorted(sp)[:-7]:
            del sp[k]
        D.write_json(STATE_PATH, state)
    meter.hook = save
    if dry:
        state.setdefault("dry", {})[today] = dry_today + 1
    save()
    picks = pick_targets(state, today)
    chosen = {picks[p][0] for p in todo}
    site, pages_full = [], {}
    for t in TARGETS:                              # our own site text (guides + today's targets) for the 7-word overlap check
        if t[2] in ("guide", "home") or t[0] in chosen:
            st, _, h = getter(t[1])
            if st == 200:
                pages_full[t[0]] = c.html_text(h, 40000)
                site.append(pages_full[t[0]])
    site_corpus = "\n".join(site)
    past_sh = set()
    for lst in (state.get("shingles") or [])[-PAST_KEPT:]:
        past_sh.update(lst)
    past_titles = [e.get("title", "") for e in state["posts"][-PAST_KEPT:] if e.get("title")]
    todays, results = [], {}
    for idx, pl in enumerate(todo):
        slug, url, kind, what, topic = picks[pl]
        page_full = pages_full.get(slug, "")
        if len(page_full) < 200:
            print("platform_poster: %s target page not readable, skipped" % pl)
            log("| %s | %s | %s | STOP page not readable | | | $%.3f | |" % (today, pl, slug, meter.cost))
            results[pl] = "PAGE_DOWN"
            continue
        page_text = page_full[:6000]
        user = ("TARGET PAGE: %s\nIT IS: %s (%s)\nTOPIC AREA: %s\nPAGE TEXT (the only facts you may state about our page):\n%s\n\n"
                "ARTICLES WE ALREADY PUBLISHED ON OUR OWN BLOGS (do not repeat these angles):\n%s\n\nWrite today's %s article now.") % (
            url, what, kind, topic, page_text, "\n".join("- " + t for t in past_titles[-25:]) or "(none yet)", pl.capitalize())
        if not dry:
            state.setdefault("attempts", {})[today + pl] = state.get("attempts", {}).get(today + pl, 0) + 1
        save()
        problems, art, source_texts = ["no draft"], None, {}
        left_after = len(todo) - idx - 1
        for attempt in (1, 2):
            if attempt == 2 and meter.cost + (left_after + 1) * (D.WRITER_RESERVE + D.QA_RESERVE) > meter.cap + 1e-9:
                break
            extra = "" if attempt == 1 else "\n\nYOUR LAST DRAFT FAILED THESE CHECKS, fix them: " + "; ".join(problems)
            fetched = {}
            try:
                art = parse_writer(D.run_writer(writer_system(pl), user + extra, meter, post=post_ai, fetched=fetched))
            except Exception as e:
                problems = ["writer error %s" % type(e).__name__]
                break
            if not art:
                problems = ["writer output not in the required format"]
                continue
            source_texts = {}
            for u in sorted({u for _, u in LINK_RE.findall(art["body"]) if "inayaflow.ai" not in u})[:6]:
                s2, _, h2 = getter(u)
                ours = c.html_text(h2, 20000) if s2 == 200 else ""
                theirs = fetched.get(u, "")[:20000]
                source_texts[u] = theirs if len(theirs) > len(ours) else ours
            problems = check_post(art, url, pl, page_full, source_texts, site_corpus, todays, past_sh)
            if any(len(t) < D.MIN_SOURCE_CHARS for t in source_texts.values()):
                problems.append("a cited source page is unreadable")
            if not problems:
                break
        ok, reasons = not problems, problems
        if ok:
            try:
                ok, reasons = D.qa_gate(art, url, source_texts, page_text, meter, post=post_ai)
            except Exception as e:
                ok, reasons = False, ["QA error %s" % type(e).__name__]
        cost = "$%.3f" % meter.cost
        if not ok:
            if art:
                os.makedirs(DRAFT_DIR, exist_ok=True)
                with open(os.path.join(DRAFT_DIR, "%s_%s_%s_FAILED.md" % (today, pl, slug)), "w", encoding="utf8") as f:
                    f.write("# FAILED DRAFT (not published)\n\nReasons: %s\n\nTitle: %s\n\n%s\n" % ("; ".join(reasons), art["title"], art["body"]))
            log("| %s | %s | %s | FAIL, not published | | | %s | %s |" % (today, pl, slug, cost, "; ".join(reasons)[:300]))
            save()
            print("platform_poster: FAIL %s page=%s problems=%d cost=%s" % (pl, slug, len(reasons), cost))
            results[pl] = "FAIL"
            continue
        todays.append((pl, re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", art["body"])))
        if dry:
            os.makedirs(DRAFT_DIR, exist_ok=True)
            with open(os.path.join(DRAFT_DIR, "%s_%s_%s_DRYRUN.md" % (today, pl, slug)), "w", encoding="utf8") as f:
                f.write("# DRY RUN (not published)\n\nPlatform: %s\nTarget: %s\nTitle: %s\n\n%s\n" % (pl, url, art["title"], art["body"]))
            log("| %s | %s | %s | DRY RUN passed all checks, not published | | | %s | |" % (today, pl, slug, cost))
            save()
            print("platform_poster: DRY RUN ok %s page=%s cost=%s (nothing published)" % (pl, slug, cost))
            results[pl] = "DRY_OK"
            continue
        state.setdefault("pending", {})[pl] = {"date": today, "slug": slug, "title": art["title"], "target": url}
        save()                                      # written BEFORE the POST: a crash leaves it behind
        try:
            res = publishers[pl](art)
        except urllib.error.HTTPError as e:
            state["pending"].pop(pl, None)
            log("| %s | %s | %s | %s refused HTTP %s | | | %s | |" % (today, pl, slug, pl, e.code, cost))
            save()
            print("platform_poster: %s answered HTTP %s" % (pl, e.code))
            results[pl] = "ERROR"
            continue
        except Exception as e:                      # timeout / network: the post MAY exist; the pending marker stays for the Director
            log("| %s | %s | %s | %s error %s (pending marker kept) | | | %s | |" % (today, pl, slug, pl, type(e).__name__, cost))
            save()
            print("platform_poster: %s error %s, pending kept" % (pl, type(e).__name__))
            results[pl] = "UNKNOWN"
            continue
        state["pending"].pop(pl, None)
        state["posts"].append({"date": today, "platform": pl, "slug": slug, "title": art["title"], "url": res.get("url") or "", "id": res.get("id"), "target": url, "rel": "unknown (not read yet)"})
        state.setdefault("shingles", []).append(sorted(D.shingles(art["body"])))
        state["shingles"] = state["shingles"][-PAST_KEPT:]
        if res.get("url"):
            state.setdefault("own_backlinks_new", []).append(own_entry(res["url"], url))
        save()
        if pl == "notion":
            rel = "unknown (Notion pages are script-rendered)"
        else:
            sleeper(10)
            s3, _, live = getter(res["url"]) if res.get("url") else (0, "", "")
            rel = D.link_rel(live, url) if s3 == 200 else "unknown (page HTTP %s)" % s3
        state["posts"][-1]["rel"] = rel
        log("| %s | %s | %s | PUBLISHED | %s | %s | %s | |" % (today, pl, slug, res.get("url"), rel, cost))
        save()
        print("platform_poster: PUBLISHED %s page=%s link=%s cost=%s" % (pl, slug, rel, cost))
        results[pl] = "PUBLISHED"
    save()
    return results


if __name__ == "__main__":
    run()
