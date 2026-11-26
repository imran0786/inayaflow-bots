#!/usr/bin/env python3
"""PLATFORM POSTER (24 Nov 2026): ONE short, unique article a day to EACH of WordPress.com, Blogger and Notion, each linking (plain <a href>, no rel)
to ONE inayaflow.ai page, rotating over the 9 tool pages, the hub guides and the homepage.

Modelled on scripts/dev_poster/poster.py and REUSES its cost meter, model call, 7-word overlap and link_rel code.
26 Nov (r3, 0 articles had ever passed): Haiku now gets an explicit effort and room to think (its thinking had eaten max_tokens 600-900, so facts came back
empty and QA "not understood"); facts, QA and rewrites answer a JSON schema parsed strictly; the writer (Sonnet) has NO web tools by default and may state
only the FACTS of our page (numbers only from them) plus a banned-claims list; links are normalised in code (one target link); banned phrases, unsourced
figures and invented mode/plan names are fixed by a sentence rewrite; the QA's quoted sentences are rewritten one by one. PLATFORM_WRITER_WEB=true = old research writer.
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
SITE_OVERLAP_WORDS = 10      # off-site articles may restate our site's plain facts; only a 10-word run counts as a copy
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
    """{platform: target}. Priority tiers first (data/push_priority.json, edited by the Director): a platform takes the highest tier that still has a page it has never linked to,
    least-recently-used inside the tier (that platform's own history first, then any platform), so each platform cycles through every tier-1 page before repeating; once it has
    covered every page it falls back to (tier, own LRU). No two platforms share a target the same day. Missing/bad priority file = tier-less (old behaviour)."""
    prio = D.load_priority([(t[0],) for t in targets])
    last, anyp, cnt = {}, {}, {}
    for e in state.get("posts", []):
        if e.get("platform") and e.get("slug") and e.get("date"):
            last[(e["platform"], e["slug"])] = e["date"]
            anyp[e["slug"]] = max(anyp.get(e["slug"], ""), e["date"])
            cnt[(e["platform"], e["slug"])] = cnt.get((e["platform"], e["slug"]), 0) + 1
    chosen, out = set(), {}
    for pl in platforms:
        if prio:
            cand = sorted((1 if cnt.get((pl, t[0])) else 0, D.tier_of(t[0], prio), last.get((pl, t[0]), "0000-00-00"), anyp.get(t[0], "0000-00-00"), i, t)
                          for i, t in enumerate(targets) if t[0] not in chosen)
            out[pl] = cand[0][5]
        else:
            cand = sorted((anyp.get(t[0], "0000-00-00"), last.get((pl, t[0]), "0000-00-00"), i, t) for i, t in enumerate(targets) if t[0] not in chosen)
            out[pl] = cand[0][3]
        chosen.add(out[pl][0])
    return out


# ---------- offline gate ----------
BANNED = D.BANNED + ["genie", "a handful of"]
NUM_RE = re.compile(r"\d[\d,\.]*\d|\d")
NAME_RE = re.compile(r"\b(?:[A-Z][\w'-]*\s){1,2}(?:Mode|Plan|Tier)\b")      # "Starter Mode", "Pro Plan": product names we never invent


def nums_in(text):
    return {D.norm_num(m) for m in NUM_RE.findall(text or "")} - {""}


def check_post(art, target_url, platform, page_full, source_texts, site_corpus="", others=(), past_shingles=None, anchor=None, facts=""):
    """-> list of problems (empty = passes). art = dict(title, tags, body, sources). others = [(platform, plain body)] already written today.
    facts = the FACTS text the writer was given (taken from our target page): a number printed there may be used without another source link."""
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
    scan = re.sub(r"\[[^\]]*\]\(" + re.escape(target_url) + r"\)", " LINK ", body) if anchor else body      # the address as LINK TEXT is allowed; bare unlinked text never
    if re.search(r"inayaflow\.ai", re.sub(r"\]\(https?://[^)\s]+\)", "]", scan), re.I):
        p.append("bare inayaflow.ai text outside a link")
    if anchor:
        txt = [t for t, u in links if u == target_url]
        if len(txt) == 1:
            p += D.anchor_problems(txt[0], anchor)
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
    unquoted = re.sub(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d', " ", body)   # a quoted reader question is not us speaking
    if re.search(r"\b(I|I'm|I've|I'll|I'd|my|me|mine|myself)\b", unquoted):
        p.append("first person singular (use 'we')")
    corpus = " ".join(list(source_texts.values()) + [page_full])
    nums = nums_in(corpus)
    fact_lines = [l.lower() for l in (facts or "").split("\n") if l.strip()]
    for para in re.split(r"\n\s*\n", body):
        if para.lstrip().startswith("#"):
            continue
        for m in D.STAT_RE.finditer(para):
            n = D.norm_num(m.group(0))
            unit = (m.group(1) or m.group(2) or "").lower()[:4]
            if not n or any(n in nums_in(l) and unit in l for l in fact_lines):     # number AND unit printed in one FACT (from our target page, which the article links)
                continue
            if not [u for _, u in LINK_RE.findall(para) if "inayaflow.ai" not in u]:
                p.append("number '%s' in a paragraph with no source link" % m.group(0).strip()[:30])
            elif n not in nums:
                p.append("number '%s' not found in the fetched sources" % m.group(0).strip()[:30])
    page_low = (page_full or "").lower() + " " + (facts or "").lower()
    for m in NAME_RE.finditer(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)):
        if m.group(0).lower() not in page_low:
            p.append("names a mode/plan/tier that is not on our page: '%s'" % m.group(0))
            break
    plain = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    ov = D.seven_word_overlap(plain, site_corpus or page_full, SITE_OVERLAP_WORDS)
    if ov:
        p.append("%d-word overlap with our site: '%s'" % (SITE_OVERLAP_WORDS, ov))
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


# ---------- link normalising in code BEFORE the gate (26 Nov): one target link, no stray links ----------
def _paragraph_spans(body):
    out, pos = [], 0
    for chunk in re.split(r"(\n\s*\n)", body):
        if chunk.strip() and not re.fullmatch(r"\n\s*\n", chunk) and not chunk.lstrip().startswith("#"):
            out.append((pos, pos + len(chunk)))
        pos += len(chunk)
    return out


def normalize_links(art, target_url, web=None):
    """Keeps exactly ONE link to the target (the first one that is not in the first or last paragraph, else the first) and turns every other
    inayaflow.ai link, and every external link the writer could not have opened (no web research, or not in its source list), into plain text.
    Only ever REMOVES links: it never adds a claim, a number or a link. A body with no target link is left as it is (the gate then fails it)."""
    web = WRITER_WEB if web is None else web
    body = art.get("body", "")
    paras = _paragraph_spans(body)
    srcs = {u.rstrip("/") for u in (art.get("sources") or [])}

    def para_of(i):
        return next((k for k, (a, b) in enumerate(paras) if a <= i < b), -1)
    ms = list(LINK_RE.finditer(body))
    tgt = [m for m in ms if m.group(2).rstrip("/") == target_url.rstrip("/")]
    keep = None
    if tgt:
        inner = [m for m in tgt if 0 < para_of(m.start()) < len(paras) - 1] if len(paras) > 3 else tgt
        keep = (inner or tgt)[0]
    out, pos = [], 0
    for m in ms:
        u = m.group(2)
        drop = (m is not keep and u in [t.group(2) for t in tgt]) or ("inayaflow.ai" in u and m is not keep) or \
               ("inayaflow.ai" not in u and (not web or u.rstrip("/") not in srcs))
        if drop:
            text = m.group(1)
            if "inayaflow.ai" in text.lower() or re.match(r"https?://", text):
                text = "the same free tool" if "inayaflow.ai" in text.lower() else "that page"
            out.append(body[pos:m.start()] + text)
            pos = m.end()
    out.append(body[pos:])
    new = "".join(out)
    return art if new == body else dict(art, body=new)


# ---------- model calls that must answer JSON (26 Nov) ----------
# WHY every 24-25 Nov draft died: claude-haiku-5-5 THINKS by default and its thinking counts toward max_tokens. With max_tokens 600-900 the reply often
# stopped (stop_reason max_tokens) before any text: "facts round 1 kept 0 dropped 0", "QA answer not understood", reasons cut mid-word.
# Now: effort is set explicitly (low), max_tokens leaves room for thinking, the reply is constrained by a JSON schema (output_config.format),
# parsed strictly (json.loads), and an unreadable reply is asked ONCE more with a stricter prompt and twice the room.
HAIKU_OUT = int(os.environ.get("PLATFORM_HAIKU_MAX_TOKENS", "4000"))     # Haiku: 4000 out x $0.50/M = $0.002, far inside QA_RESERVE
HAIKU_EFFORT = os.environ.get("PLATFORM_HAIKU_EFFORT", "low")
QA_EFFORT = os.environ.get("PLATFORM_QA_EFFORT", "medium")
JSON_ONLY = "\nAnswer with the JSON object only: no words before or after it, no code fence."


def _text(data):
    return "".join(b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text")


def strict_json(raw):
    """The reply as a JSON object: the whole reply (or the whole fenced block) must parse. None otherwise."""
    t = (raw or "").strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    if m:
        t = m.group(1)
    try:
        d = json.loads(t)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def ask_json(system, user, schema, meter, parse, post=None, max_tokens=None, effort=None, retry_system=None, label="json"):
    """-> (parsed value or None, raw text). parse(raw) returns the value or None (unreadable). Two asks at most."""
    max_tokens = max_tokens or HAIKU_OUT
    use_format, raw = True, ""
    for attempt in (1, 2):
        oc = {"effort": effort or HAIKU_EFFORT}
        if use_format:
            oc["format"] = {"type": "json_schema", "schema": schema}
        sysm = system if attempt == 1 else (retry_system or system + JSON_ONLY)
        try:
            data = D.anthropic(D.QA_MODEL, sysm, user, meter, post=post, max_tokens=max_tokens * attempt, extra={"output_config": oc})
        except urllib.error.HTTPError as e:
            if e.code == 400 and use_format and attempt == 1:     # the API refused the schema: ask once more for plain JSON, still parsed strictly
                print("platform_poster: %s schema refused (HTTP 400), asking for plain JSON" % label)
                use_format = False
                continue
            raise
        raw = _text(data)
        val = parse(raw)
        if val is not None:
            return val, raw
        print("platform_poster: %s reply unreadable (stop=%s, %s output tokens), %s" % (
            label, data.get("stop_reason"), (data.get("usage") or {}).get("output_tokens"), "asking once more" if attempt == 1 else "giving up"))
    return None, raw


def _rewrites(raw):
    """{sentence number: new text} from {"rewrites":[{"n":4,"text":"..."}]} (or the older {"4":"..."} form). None = unreadable."""
    j = strict_json(raw) or D.qa_json(raw)
    if not isinstance(j, dict):
        return None
    out = {}
    if isinstance(j.get("rewrites"), list):
        for r in j["rewrites"]:
            if not (isinstance(r, dict) and isinstance(r.get("text"), str) and str(r.get("n", "")).strip().isdigit()):
                return None
            out[int(str(r["n"]).strip())] = r["text"]
        return out
    for k, v in j.items():
        if not (str(k).isdigit() and isinstance(v, str)):
            return None
        out[int(k)] = v
    return out


REWRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["rewrites"], "properties": {
    "rewrites": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["n", "text"],
                                            "properties": {"n": {"type": "integer"}, "text": {"type": "string"}}}}}}


# ---------- repair (24 Nov, widened 26 Nov): fix ONLY the offending sentences instead of throwing the draft away ----------
REPAIRABLE = ("-word overlap with our site", "-word overlap with today's", "first person singular", "banned phrase", "number '", "names a mode/plan/tier")
FIRST_PERSON_RE = re.compile(r"\b(I|I'm|I've|I'll|I'd|my|me|mine|myself)\b")
QUOTE_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”')
MAX_REPAIR_SENTENCES = 12
REPAIR_ROUNDS = 2          # sentence-level rewrites (overlap / I-me-my / banned words / unsourced figures / invented names) per platform per day
QA_REPAIR_ROUNDS = 2       # rewrites of sentences the fact-check named as unsupported


def repairable(problems):
    """True when EVERY problem is one a sentence rewrite can fix: a copy-overlap, 'I/me/my', a banned phrase, an unsourced figure or an invented name."""
    return bool(problems) and all(any(k in x for k in REPAIRABLE) for x in problems)


def sentence_spans(body):
    """[(start, end)] of every sentence in the body (headings and blank lines skipped). A sentence never crosses a line."""
    spans, pos = [], 0
    for line in body.split("\n"):
        if line.strip() and not line.lstrip().startswith("#"):
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


def overlap_grams(text, reference, n):
    """Every n-word run that text shares with reference (lower-case, space-joined)."""
    r = _toks(reference)
    grams = {" ".join(r[i:i + n]) for i in range(len(r) - n + 1)}
    a = _toks(text)
    return {" ".join(a[i:i + n]) for i in range(len(a) - n + 1)} & grams


def offending_sentences(body, site_corpus, others, problems=()):
    """-> [(span_index, reason)]: sentences that carry a site / other-article overlap run, a first-person word, a banned phrase,
    a figure the gate named as unsourced, or a mode/plan name the gate named as not on our page."""
    spans = sentence_spans(body)
    sents = [body[a:b] for a, b in spans]
    plain = [re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", x) for x in sents]
    bad = {}
    refs = [(SITE_OVERLAP_WORDS, site_corpus, "copies %d or more words in a row from our own site, the shared run is" % SITE_OVERLAP_WORDS)]
    refs += [(7, o, "copies 7 or more words in a row from another article of ours, the shared run is") for _, o in others]
    for n, ref, why0 in refs:
        if not ref:
            continue
        for g in sorted(overlap_grams(" ".join(plain), ref, n)):
            why = "%s '%s'" % (why0, g)
            hit = [i for i, x in enumerate(plain) if g in " ".join(_toks(x))]
            if not hit:                       # the run straddles two neighbouring sentences: rewrite both
                for i in range(len(plain) - 1):
                    if g in " ".join(_toks(plain[i] + " " + plain[i + 1])):
                        hit = [i, i + 1]
                        break
            for i in hit:
                bad.setdefault(i, why)
    for i, x in enumerate(sents):
        if FIRST_PERSON_RE.search(QUOTE_RE.sub(" ", x)):
            bad.setdefault(i, "uses I / me / my (must speak as 'we' / 'our')")
        low = x.lower()
        bb = [b for b in BANNED if b in low]
        if bb:
            bad.setdefault(i, "uses the banned phrase '%s': say it without those words" % bb[0])
    for pr in problems or ():
        m = re.match(r"number '([^']+)'", pr)
        if m:
            for i, x in enumerate(plain):
                if m.group(1) in x:
                    bad.setdefault(i, "contains the figure '%s' that has no source: drop the figure and make the point without any number" % m.group(1))
        m = re.match(r"names a mode/plan/tier that is not on our page: '([^']+)'", pr)
        if m:
            for i, x in enumerate(plain):
                if m.group(1) in x:
                    bad.setdefault(i, "names '%s', which is not on our page: drop the name and describe it in general words" % m.group(1))
    return sorted(bad.items())


REPAIR_SYSTEM = ("You are a copy editor. Rewrite ONLY the numbered sentences. Each line says in [brackets] what is wrong with that sentence; fix exactly that:\n"
                 "- a shared run of words: change the structure and vocabulary so that run (and any 4 or more of its words in a row) no longer appears; keep the meaning;\n"
                 "- I / me / my: speak as 'we' / 'our' for the company;\n- a banned phrase: say the same thing without those words;\n"
                 "- a figure with no source: drop the figure and make the point without any number;\n- a name not on our page: drop it and use general words.\n"
                 "Never add a number, a name, a feature or a claim. Keep every other number as it is. Keep every Markdown link [text](url) EXACTLY as it is "
                 "(same text, same url); a list item keeps its '- ' or '1. ' start; one sentence stays one sentence. "
                 'Answer ONLY the JSON object {"rewrites":[{"n":1,"text":"new sentence"}]} with one entry per numbered line.')


def _keeps_links(old, new):
    """A rewrite must carry every Markdown link of the sentence it replaces, unchanged (a deleted sentence carries none: only allowed without links)."""
    return all(("[%s](%s)" % (t, u)) in new for t, u in LINK_RE.findall(old))


def _splice(body, spans, picks):
    """picks = {span_index: new text}. A list item keeps its marker; an empty text deletes the sentence.
    A rewrite that would drop or change a link of its sentence is NOT applied (that sentence stays as it was)."""
    new = body
    for i in sorted(picks, reverse=True):
        a, b = spans[i]
        rep, old = picks[i].strip(), body[a:b]
        if not _keeps_links(old, rep):
            continue
        m = re.match(r"\s*([-*]|\d+[.)])\s", old)
        if rep and m and not re.match(r"([-*]|\d+[.)])\s", rep):
            rep = m.group(0) + rep
        new = new[:a] + rep + new[b:]
    return new


def repair_article(art, site_corpus, others, meter, post=None, problems=()):
    """ONE cheap rewrite (Haiku, no tools) of ONLY the offending sentences. -> repaired art dict, or None when nothing could be repaired."""
    body = art["body"]
    spans = sentence_spans(body)
    bad = offending_sentences(body, site_corpus, others, problems)
    if not bad or len(bad) > MAX_REPAIR_SENTENCES:
        return None
    lines = "\n".join("%d. [%s] %s" % (n + 1, why, body[spans[i][0]:spans[i][1]]) for n, (i, why) in enumerate(bad))
    rw, _ = ask_json(REPAIR_SYSTEM, "SENTENCES TO REWRITE:\n" + lines, REWRITE_SCHEMA, meter, _rewrites, post=post, label="repair")
    if not rw:
        return None
    picks = {}
    for n, (i, _) in enumerate(bad):
        rep = rw.get(n + 1)
        if not isinstance(rep, str) or not rep.strip() or "\n" in rep.strip():
            return None
        picks[i] = rep
    return dict(art, body=_splice(body, spans, picks))


# ---------- facts for the writer (25 Nov, JSON 26 Nov): short notes in other words, so the writer has nothing to copy ----------
FACTS_SYSTEM = ("You make short notes for a writer from the text of one web page (our own page). Write 6 to 10 facts, each one plain sentence under 20 words. "
                "State only what the page says: what the tool or guide is for, what it needs, what it shows, its limits, and numbers exactly as printed. "
                "Use YOUR OWN words: never reuse a run of 5 or more consecutive words from the page, and never copy its headings or list items. "
                "Add nothing the page does not say; no advice, no opinions. "
                'Answer ONLY the JSON object {"facts":["...","..."]}.')
FACTS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facts"],
                "properties": {"facts": {"type": "array", "items": {"type": "string"}}}}
FACT_GRAM = 7              # a fact may share short phrases with the page (the article gate is 10 words); only a 7-word run is a copy
MIN_FACTS = 3


def _fact_candidates(raw):
    """Candidate fact strings from the reply: the JSON list, or (older/plain replies) the bullet lines. None when there is nothing."""
    j = strict_json(raw) or D.qa_json(raw)
    if isinstance(j, dict) and isinstance(j.get("facts"), list):
        c = [x for x in j["facts"] if isinstance(x, str)]
        return c or None
    c = [m.group(1) for m in (re.match(r"\s*(?:[-*\u2022]|\d+[.)])\s+(.+)", l) for l in (raw or "").split("\n")) if m]
    return c or None


def fact_bullets(raw, page_full, rejected=None):
    """Valid facts: 3-30 words, no 7-word run shared with the page, and every number in them printed on the page. Rejected ones go to `rejected`.
    raw = a model reply (JSON or bullet lines) or a list of candidate strings."""
    cands = raw if isinstance(raw, list) else (_fact_candidates(raw) or [])
    page_nums = nums_in(page_full)
    out = []
    for t in cands:
        t = re.sub(r"\*\*|__", "", t).strip()
        n = len(_toks(t))
        if n < 3 or n > 30:
            continue
        if overlap_grams(t, page_full, FACT_GRAM) or not nums_in(t) <= page_nums:
            if rejected is not None:
                rejected.append(t)
        else:
            out.append(t)
    return out[:10]


def distill_facts(page_full, meter, post=None):
    """-> fact lines for the writer, or '' when they could not be made (the caller then falls back to a SHORT slice of the page text).
    One retry when too few facts survive the copy / number filter: the rejected ones are named so the model words them differently."""
    user = "PAGE TEXT:\n" + page_full[:8000]
    good = []
    for rnd in (1, 2):
        cands, _ = ask_json(FACTS_SYSTEM, user, FACTS_SCHEMA, meter, _fact_candidates, post=post, label="facts")
        rejected = []
        for b in fact_bullets(cands or [], page_full, rejected):
            if b not in good:
                good.append(b)
        print("platform_poster: facts round %d got %d kept %d dropped %d" % (rnd, len(cands or []), len(good), len(rejected)))
        if len(good) >= MIN_FACTS or not rejected:
            break
        user = ("PAGE TEXT:\n" + page_full[:8000] + "\n\nThese earlier facts were too close to the page wording or used a number the page does not print. "
                "Write 6 to 10 NEW facts about the same things with completely different wording and sentence shapes:\n" + "\n".join("- " + x for x in rejected[:8]))
    return "\n".join("- " + x for x in good[:10]) if len(good) >= MIN_FACTS else ""


# ---------- fact-check (25 Nov, JSON 26 Nov): schema reply, strict parse, strict retry; the older VERDICT form is still read ----------
PQA_SYSTEM = """You are a strict fact-check editor. You get our FACTS list, our own page text, the text of any pages the article links to, and the article.
Check every claim in the article. FAIL a sentence when ANY of these is true:
- it gives a number, percent, date, study result or statistic that is not in the FACTS, our page text or a linked page;
- it says what OUR tool shows, does, needs, costs or limits, or names a mode, plan, tier, button or feature of it, and the FACTS / our page text do not say that;
- it says where something appears in the TikTok app, what TikTok does or decides, or what another app or tool does, and neither the FACTS, our page text nor a linked page says it;
- it compares things or promises a result as fact ("beats", "works better", "more reach", "guarantees") without a linked page that says so;
- it names another creator, an @handle or a brand-new product claim; it gives legal, medical or financial claims without a source;
- the InayaFlow link is pushy or off-topic, or the article reads as an advert instead of a useful article.
Do NOT fail plain how-to advice and reasoning that makes no factual claim (for example how a median works, or "compare a video with your usual result").
PASS only when every checkable claim is supported.
Reply with ONLY the JSON object {"verdict":"PASS" or "FAIL","problems":[{"quote":"<the exact sentence from the article>","why":"<15 words at most>"}]}
(problems is empty for PASS; at most 5 problems for FAIL, one per sentence)."""
PQA_STRICT = ("Your reply must start with { and be ONLY the JSON object {\"verdict\":\"PASS\"|\"FAIL\",\"problems\":[{\"quote\":\"...\",\"why\":\"...\"}]}. "
              "Nothing else.\n\n" + PQA_SYSTEM)
QA_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "problems"], "properties": {
    "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
    "problems": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["quote", "why"],
                                            "properties": {"quote": {"type": "string"}, "why": {"type": "string"}}}}}}
VERDICT_RE = re.compile(r"VERDICT\W{0,6}(PASS|FAIL)", re.I)


def parse_qa(raw):
    """-> (verdict 'PASS'|'FAIL'|None, [reasons]). JSON first ({"verdict","problems":[{"quote","why"}]}); a PASS that still lists problems is a FAIL.
    Older forms still read (VERDICT lines, {"verdict","reasons"}); there FAIL wins: the word FAIL anywhere makes it FAIL."""
    raw = raw or ""
    j = strict_json(raw)
    if isinstance(j, dict) and str(j.get("verdict", "")).upper() in ("PASS", "FAIL") and isinstance(j.get("problems"), list):
        reasons = []
        for pr in j.get("problems") or []:
            if isinstance(pr, dict) and (pr.get("quote") or pr.get("why")):
                reasons.append(('"%s" | %s' % (str(pr.get("quote", "")).strip(), str(pr.get("why", "")).strip()))[:600])
            elif isinstance(pr, str) and pr.strip():
                reasons.append(pr.strip()[:600])
        if str(j["verdict"]).upper() == "PASS" and not reasons:
            return "PASS", []
        return "FAIL", (reasons or ["QA said FAIL without a reason"])[:6]
    reasons = [re.sub(r"\s+", " ", m).strip()[:600] for m in re.findall(r"(?ims)^\W*REASON\W*[:\-]\s*(.+?)(?=^\W*(?:REASON|VERDICT)\b|\Z)", raw)]
    j = D.qa_json(raw)
    jv = j.get("verdict") if isinstance(j, dict) else None
    if isinstance(j, dict) and not reasons:
        reasons = [str(x)[:300] for x in (j.get("reasons") or j.get("problems") or [])]
    verdicts = {v.upper() for v in VERDICT_RE.findall(raw)} | ({jv.upper()} if isinstance(jv, str) else set())
    if "FAIL" in verdicts or re.search(r"\bFAIL\b", raw):
        if not reasons:
            reasons = (D.qa_lenient(raw) or {}).get("reasons") or ["QA said FAIL without a reason"]
        return "FAIL", reasons[:6]
    if "PASS" in verdicts or re.match(r"(?i)\s*PASS\b", raw):
        return "PASS", []
    return None, []


def qa_check(art, target_url, source_texts, page_text, meter, post=None, facts=""):
    """-> (ok, reasons). Two asks at most: the normal prompt, then a strict short one. Both unreadable -> 'QA answer not understood'."""
    srcs = "\n\n".join("SOURCE %s:\n%s" % (u, (t or "")[:3500]) for u, t in source_texts.items())
    user = "FACTS (from our page %s):\n%s\n\nOUR PAGE %s (our own page text):\n%s\n\n%s\n\nARTICLE TITLE: %s\n\n%s" % (
        target_url, facts or "(none)", target_url, page_text[:4000], srcs, art["title"], art["body"])

    def parse(raw):
        v, r = parse_qa(raw)
        return (v, r) if v else None
    val, _ = ask_json(PQA_SYSTEM, user[:60000], QA_SCHEMA, meter, parse, post=post, max_tokens=max(HAIKU_OUT, 6000), effort=QA_EFFORT,
                      retry_system=PQA_STRICT, label="QA")
    if not val:
        return False, ["QA answer not understood"]
    return val[0] == "PASS", val[1]


def _quoted(reason):
    m = re.match(r'\s*["\u201c](.+?)["\u201d]\s*\|', reason or "")
    return m.group(1) if m else ""


def quote_to_sentence(quote, sents):
    """Index of the sentence the checker quoted (most of the quote's words appear in it), or None."""
    q = set(_toks(quote))
    if len(q) < 3:
        return None
    best, bi = 0.0, None
    for i, s in enumerate(sents):
        sc = len(q & set(_toks(s))) / len(q)
        if sc > best:
            best, bi = sc, i
    return bi if best >= 0.6 else None


QA_REPAIR_SYSTEM = ("You are a copy editor. A fact-checker found unsupported claims in an article; each is listed with the checker's exact quote and reason. "
                    "For each sentence that carries such a claim give a replacement that drops the unsupported claim and says something general and true that needs no source, "
                    "or an empty string to delete the sentence. Never add numbers, names, statistics or claims about what our tool does. "
                    "Keep every Markdown link [text](url) that sits in a sentence you keep or replace EXACTLY as it is. "
                    'Answer ONLY the JSON object {"rewrites":[{"n":4,"text":"new sentence"},{"n":9,"text":""}]} with at most 6 entries.')


def qa_repair_article(art, reasons, meter, post=None):
    """ONE cheap rewrite (Haiku, no tools) of ONLY the sentences the fact-check QUOTED: reword to a general, true statement or delete. -> art dict or None.
    When the quotes can be matched to sentences, only those sentences may change; otherwise the editor picks them from the full numbered list."""
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
    numbered = "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(sents))
    hint = ("\n\nTHE CHECKER QUOTED THESE SENTENCES (change only these): " + ", ".join(str(i + 1) for i in sorted(flagged))) if flagged else ""
    rw, _ = ask_json(QA_REPAIR_SYSTEM, "FACT-CHECKER REASONS:\n- " + "\n- ".join(reasons[:6]) + hint + "\n\nSENTENCES:\n" + numbered[:12000],
                     REWRITE_SCHEMA, meter, _rewrites, post=post, label="QA repair")
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


# ---------- writer (26 Nov): by default NO web research; the writer gets ONLY our page's FACTS and a banned-claims list ----------
WRITER_WEB = os.environ.get("PLATFORM_WRITER_WEB", "false").strip().lower() == "true"    # "true" = the older research writer (web search + fetch)
WRITER_OUT = int(os.environ.get("PLATFORM_WRITER_MAX_TOKENS", "8000"))                   # Sonnet thinks too: leave room (8000 x $10/M = $0.08 < WRITER_RESERVE)
WRITER_EFFORT = os.environ.get("PLATFORM_WRITER_EFFORT", "medium")
D.WRITER_MAX_TOKENS = max(D.WRITER_MAX_TOKENS, 6000)       # the research writer (PLATFORM_WRITER_WEB=true) also thinks: 2500 could cut the article off


def write_draft(platform, user, meter, post=None, fetched=None):
    if WRITER_WEB:
        return D.run_writer(writer_system(platform), user, meter, post=post, fetched=fetched)
    data = D.anthropic(D.WRITER, writer_system(platform), user, meter, post=post, max_tokens=WRITER_OUT, extra={"output_config": {"effort": WRITER_EFFORT}})
    if data.get("stop_reason") == "refusal":
        raise RuntimeError("refusal")
    if data.get("stop_reason") == "max_tokens":
        print("platform_poster: writer reply cut at max_tokens")
    return _text(data)


def revise_system(platform):
    return writer_system(platform) + "\nYou are now REVISING your own earlier draft. Do NOT search or fetch anything. Keep the same sources and links, change only what the failed checks name, and answer in the same OUTPUT FORMAT with the full article."


def run_reviser(platform, user, art, problems, meter, post=None):
    draft = "TITLE: %s\nSOURCES: %s\n===BODY===\n%s" % (art["title"], " | ".join(art.get("sources") or []) or "none", art["body"])
    msg = user + "\n\nYOUR DRAFT:\n" + draft + "\n\nIT FAILED THESE CHECKS, fix them (quoted phrases must not appear again; remove or neutralise any unsupported claim): " + "; ".join(problems)
    data = D.anthropic(D.WRITER, revise_system(platform), msg, meter, post=post, max_tokens=WRITER_OUT, extra={"output_config": {"effort": WRITER_EFFORT}})
    return _text(data)


BANNED_CLAIMS = """BANNED CLAIMS (a fact-checker and code reject them; when in doubt leave the sentence out):
- any name of a mode, plan, tier, button, screen or feature of our tool that is not written in the FACTS (for example "Starter Mode", "Pro plan");
- vague amounts about our tool ("a handful of", "a few videos", "several", "enough") when a FACT gives the exact number: use the FACT's number, or say nothing;
- any comparison or promise stated as fact: "beats", "works better", "more reach", "boosts", "outperforms", "guarantees", "always", "never fails";
- where something appears in the TikTok app (menus, notices, "below your display name"), what TikTok's system does or decides, or what another app or tool
  (TikTok Studio and others) does, unless a FACT says exactly that;
- statistics, percentages, study results, prices or dates that are not in the FACTS;
- what our tool "tells", "shows", "recommends" or "detects" unless a FACT says so."""


def writer_system(platform):
    s = SPECS[platform]
    if WRITER_WEB:
        sources = ("- Real numbers only: a number from the FACTS may be used as it is; any other number must be found with web_search / web_fetch in an official or primary page "
                   "you OPENED, and its paragraph needs a Markdown link to that page where the number is printed exactly. If you cannot source a number leave it out.")
        out_sources = "SOURCES: <url1> | <url2>   (or the word none)"
    else:
        sources = ("- You have NO web access and no outside sources: do not link any page except the one target link, and use NO number, percent, price, date or "
                   "statistic unless it is printed in the FACTS. Write useful, general how-to advice and reasoning instead.")
        out_sources = "SOURCES: none"
    return ("""You write one short article for the company InayaFlow, to be posted on %s. Voice: "we" (never I, me, my, mine, myself). Plain, friendly, specific English. The product is called InayaFlow (never "Genie").
FORMAT OF THE ARTICLE: %s
RULES (all checked by code afterwards; a break means nothing is published):
- %d to %d words of Markdown (## headings, short paragraphs, "- " lists, [text](url) links, **bold**). Your OWN angle and wording: add examples and reasoning of your own.
- Short, original sentences (mostly under 20 words). Never copy wording from our site: code rejects any run of 10 words shared with an InayaFlow page.
%s
- Facts about our own page and tool may come ONLY from the FACTS list in the user message (short notes in other words). Do not add features, limits, outputs or numbers of our tool that are not in it; for anything else about the tool stay general or say nothing. Never write that the tool "tells", "shows" or "recommends" something unless a FACT says so.
- Exactly ONE link to the target InayaFlow page, as [LINK TEXT](URL), placed where it truly helps (in a middle paragraph, never the first or last paragraph); the type of LINK TEXT is set per article in the user message and checked by code. Never write the web address inayaflow.ai as plain text outside that link (as the link text itself it is allowed when the type says so). Name InayaFlow at most twice.
- Write plain how-to advice. No comparisons or performance claims ("X beats Y", "works better", "more reach") unless a source you opened says so and you link it. Never name a mode, plan, tier, limit or quantity of our tool unless a FACT states it exactly (no "a handful of", no invented product names). When unsure, leave the sentence out.
%s
- No @usernames, no other creators' names, no hype, no "sign up now". Be honest: our tools are free helpers, not magic.
- Never use these words or phrases (code rejects them, also inside other words): %s.
- Title: 80 characters or fewer, no clickbait.
OUTPUT FORMAT, exactly, ONCE, nothing before or after (no notes, no corrections, no second copy):
TITLE: <title>
%s
===BODY===
<markdown body, no title line>""") % (platform.capitalize(), s["style"], s["words"][0], s["words"][1], sources, BANNED_CLAIMS,
                                       ", ".join('"%s"' % b for b in BANNED), out_sources)


def parse_writer(text):
    """TITLE / SOURCES / ===BODY=== read line by line from the FIRST block. When the model wrote a second copy or a note after another delimiter
    (25 Nov: the body became a 34-word 'Oops, here is the clean version' note), the LONGEST body segment is the article."""
    text = (text or "").replace("\r\n", "\n")
    mt = re.search(r"(?m)^\W{0,4}TITLE:[ \t]*(.+)$", text)
    if not mt:
        return None
    ms = re.search(r"(?m)^\W{0,4}SOURCES:[ \t]*(.*)$", text[mt.end():])
    mb = re.search(r"(?m)^[ \t]*={3,}\s*BODY\s*={3,}[ \t]*$", text[mt.end():])
    if not mb or (ms and ms.start() > mb.start()):
        return None
    src_line = ms.group(1) if ms else ""
    rest = text[mt.end() + mb.end():]
    segs = re.split(r"(?m)^[ \t]*={3,}\s*[A-Z ]*={3,}[ \t]*$", rest)
    cleaned = []
    for sgm in segs:
        sgm = re.sub(r"(?m)^\W{0,4}(TITLE|SOURCES):.*$", "", sgm).strip()
        cleaned.append(sgm)
    body = max(cleaned, key=lambda x: len(D.words(x))) if cleaned else ""
    title = mt.group(1).strip().strip("*").strip()
    return {"title": title, "tags": [], "sources": [u.strip() for u in re.split(r"\s*\|\s*", src_line) if u.strip().startswith("http")], "body": body}


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
        done = any(e.get("date") == today and e.get("platform") == pl for e in state["posts"])      # any successful publish counts, even with an empty URL
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
        anchor = D.anchor_info(slug, url, D.anchor_count(state, slug))       # one cursor per target shared by the 3 platforms; counts published posts only
        facts = ""
        if meter.cost + D.QA_RESERVE + (len(todo) - idx) * (D.WRITER_RESERVE + D.QA_RESERVE) <= meter.cap + 1e-9:
            try:
                facts = distill_facts(page_full, meter, post=post_ai)          # short notes in other words: nothing for the writer to copy
            except D.CapReached:
                facts = ""
            except Exception as e:
                print("platform_poster: facts skipped (%s)" % type(e).__name__)
        if not facts:
            print("platform_poster: %s using the raw page text (no clean facts)" % pl)
        fact_text = facts or page_text[:2500]          # what the writer may say about our page; numbers printed here need no other source (the article links this page)
        user = ("TARGET PAGE: %s\nIT IS: %s (%s)\nTOPIC AREA: %s\nFACTS ABOUT OUR PAGE (source: %s; the only things you may say about it or its tool, and the only numbers "
                "you may use; use your own wording):\n%s\n\n"
                "ARTICLES WE ALREADY PUBLISHED ON OUR OWN BLOGS (do not repeat these angles):\n%s\n\n%s\n\nWrite today's %s article now.") % (
            url, what, kind, topic, url, fact_text, "\n".join("- " + t for t in past_titles[-25:]) or "(none yet)", D.anchor_prompt(anchor), pl.capitalize())
        if not dry:
            state.setdefault("attempts", {})[today + pl] = state.get("attempts", {}).get(today + pl, 0) + 1
        save()
        problems, art, source_texts = ["no draft"], None, {}
        left_after = len(todo) - idx - 1
        repairs_left = REPAIR_ROUNDS
        fetched = {}

        def evaluate(a):
            """-> (article after link normalising, problems, source texts)."""
            a = normalize_links(a, url)
            st = {}
            for u in sorted({u for _, u in LINK_RE.findall(a["body"]) if "inayaflow.ai" not in u})[:6]:
                s2, _, h2 = getter(u)
                ours = c.html_text(h2, 20000) if s2 == 200 else ""
                theirs = fetched.get(u, "")[:20000]
                st[u] = theirs if len(theirs) > len(ours) else ours
            pr = check_post(a, url, pl, page_full, st, site_corpus, todays, past_sh, anchor=anchor, facts=fact_text)
            if any(len(t) < D.MIN_SOURCE_CHARS for t in st.values()):
                pr.append("a cited source page is unreadable")
            return a, pr, st
        for attempt in (1, 2):
            repair_room = D.QA_RESERVE if repairs_left > 0 else 0.0      # a possible repair call is reserved too, so it can never push the run over the cap
            if attempt == 2 and meter.cost + repair_room + (left_after + 1) * (D.WRITER_RESERVE + D.QA_RESERVE) > meter.cap + 1e-9:
                break
            extra = "" if attempt == 1 else "\n\nYOUR LAST DRAFT FAILED THESE CHECKS, fix them (the quoted phrases must not appear again): " + "; ".join(problems)
            prev = art if (attempt == 2 and art) else None
            if not prev:
                fetched = {}
            try:
                if prev:
                    art = parse_writer(run_reviser(pl, user, prev, problems, meter, post=post_ai))
                else:
                    art = parse_writer(write_draft(pl, user + extra, meter, post=post_ai, fetched=fetched))
            except Exception as e:
                problems = ["writer error %s" % type(e).__name__]
                break
            if not art:
                problems = ["writer output not in the required format"]
                continue
            art, problems, source_texts = evaluate(art)
            while problems and repairable(problems) and repairs_left > 0:
                repairs_left -= 1                     # up to REPAIR_ROUNDS targeted rewrites per platform per day; each result is checked again from scratch
                fixed = None
                # the repair must leave room for the QA gate after it and for the platforms still to come
                if meter.cost + 2 * D.QA_RESERVE + left_after * (D.WRITER_RESERVE + D.QA_RESERVE) <= meter.cap + 1e-9:
                    try:
                        fixed = repair_article(art, site_corpus, todays, meter, post=post_ai, problems=problems)
                    except D.CapReached:
                        problems = problems + ["daily cost cap reached during repair"]
                        repairs_left = -1
                        break                          # same as the writer's cap path: stop spending, this draft is FAILED
                    except Exception as e:
                        print("platform_poster: repair skipped (%s)" % type(e).__name__)
                if not fixed:
                    break
                fixed, p2, st2 = evaluate(fixed)
                print("platform_poster: repair %s problems %d -> %d" % (pl, len(problems), len(p2)))
                new_kinds = [x for x in p2 if x not in problems and not repairable([x])]      # a rewrite may not swap a problem for a worse one (e.g. a lost link)
                if len(p2) <= len(problems) and not new_kinds:
                    art, problems, source_texts = fixed, p2, st2
                else:
                    break
            if repairs_left < 0:
                break
            if not problems:
                break
        ok, reasons = not problems, problems
        if ok:
            qa_rounds = 0
            while True:
                try:
                    ok, reasons = qa_check(art, url, source_texts, page_text, meter, post=post_ai, facts=fact_text)
                except Exception as e:
                    ok, reasons = False, ["QA error %s" % type(e).__name__]
                    break
                if ok or qa_rounds >= QA_REPAIR_ROUNDS or reasons == ["QA answer not understood"]:
                    break
                if meter.cost + 3 * D.QA_RESERVE + left_after * (D.WRITER_RESERVE + D.QA_RESERVE) > meter.cap + 1e-9:
                    break
                qa_rounds += 1                       # the fact-check named unsupported claims: ONE rewrite of just those sentences, then the same gates again
                try:
                    fixed = qa_repair_article(art, reasons, meter, post=post_ai)
                except Exception as e:
                    print("platform_poster: QA repair skipped (%s)" % type(e).__name__)
                    break
                if not fixed:
                    break
                fixed, p2, st2 = evaluate(fixed)
                print("platform_poster: QA repair %s problems 0 -> %d" % (pl, len(p2)))
                if p2:
                    break                              # the rewrite broke another gate: keep the original FAIL reasons
                art, source_texts = fixed, st2
        cost = "$%.3f" % meter.cost
        if not ok:
            if art:
                os.makedirs(DRAFT_DIR, exist_ok=True)
                with open(os.path.join(DRAFT_DIR, "%s_%s_%s_FAILED.md" % (today, pl, slug)), "w", encoding="utf8") as f:
                    f.write("# FAILED DRAFT (not published)\n\nReasons: %s\n\nTitle: %s\n\n%s\n" % ("; ".join(reasons), art["title"], art["body"]))
            log("| %s | %s | %s | FAIL, not published | | | %s | %s |" % (today, pl, slug, cost, "; ".join(reasons).replace("|", "/").replace("\n", " ")[:2500]))
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
        state["posts"].append({"date": today, "platform": pl, "slug": slug, "title": art["title"], "url": res.get("url") or "", "id": res.get("id"), "target": url, "rel": "unknown (not read yet)"}
                              | ({} if res.get("url") else {"url_missing": True}))
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


def main():
    try:
        run()
    except Exception as e:                  # one short line, no message text (it could carry a URL or a token): type + where
        tb = e.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        print("platform_poster: CRASH %s at %s:%s" % (type(e).__name__, os.path.basename(tb.tb_frame.f_code.co_filename), tb.tb_lineno))
        sys.exit(1)


if __name__ == "__main__":
    main()
