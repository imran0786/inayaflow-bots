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
BANNED = D.BANNED + ["genie"]


def check_post(art, target_url, platform, page_full, source_texts, site_corpus="", others=(), past_shingles=None, anchor=None):
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


# ---------- repair (24 Nov): fix ONLY the offending sentences instead of throwing the draft away ----------
REPAIRABLE = ("-word overlap with our site", "-word overlap with today's", "first person singular")
FIRST_PERSON_RE = re.compile(r"\b(I|I'm|I've|I'll|I'd|my|me|mine|myself)\b")
QUOTE_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”')
MAX_REPAIR_SENTENCES = 12
REPAIR_ROUNDS = 2          # sentence-level rewrites (overlap / I-me-my) per platform per day
QA_REPAIR_ROUNDS = 2       # rewrites of sentences the fact-check named as unsupported


def repairable(problems):
    """True when EVERY problem is a copy-overlap or an 'I/me/my' problem (the only kinds a sentence rewrite can fix)."""
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


def offending_sentences(body, site_corpus, others):
    """-> [(span_index, reason)]: sentences that carry a site / other-article overlap run or a first-person word."""
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
    return sorted(bad.items())


def repair_article(art, site_corpus, others, meter, post=None):
    """ONE cheap rewrite (Haiku, no tools) of ONLY the offending sentences. -> repaired art dict, or None when nothing could be repaired."""
    body = art["body"]
    spans = sentence_spans(body)
    bad = offending_sentences(body, site_corpus, others)
    if not bad or len(bad) > MAX_REPAIR_SENTENCES:
        return None
    lines = "\n".join("%d. [%s] %s" % (n + 1, why, body[spans[i][0]:spans[i][1]]) for n, (i, why) in enumerate(bad))
    system = ("You are a copy editor. Rewrite ONLY the numbered sentences. Each line names the exact shared phrase that must disappear: change the sentence's structure and vocabulary "
              "so that phrase (and any run of 4 or more of its words) no longer appears. Keep the exact meaning and every number, but use completely different wording so no run of 5 or more "
              "words is shared with the original sentence. Speak as 'we' / 'our' for the company (never I, me, my, mine, myself). Keep every Markdown link [text](url) EXACTLY as it is "
              "(same text, same url), a list item keeps its '- ' or '1. ' start, and one sentence stays one sentence. "
              'Answer ONLY a JSON object {"1":"new sentence","2":"new sentence"} with one entry per number.')
    data = D.anthropic(D.QA_MODEL, system, "SENTENCES TO REWRITE:\n" + lines, meter, post=post, max_tokens=900)
    raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    j = D.qa_json(raw)
    if not isinstance(j, dict):
        return None
    new = body
    for n, (i, _) in reversed(list(enumerate(bad))):
        rep = j.get(str(n + 1))
        old = body[spans[i][0]:spans[i][1]]
        if not isinstance(rep, str) or not rep.strip() or "\n" in rep.strip():
            return None
        rep = rep.strip()
        m = re.match(r"\s*([-*]|\d+[.)])\s", old)
        if m and not re.match(r"([-*]|\d+[.)])\s", rep):
            rep = m.group(0) + rep
        new = new[:spans[i][0]] + rep + new[spans[i][1]:]
    return dict(art, body=new)


# ---------- facts for the writer (25 Nov): short bullets in other words, so the writer has nothing to copy ----------
FACTS_SYSTEM = ("You make short notes for a writer from the text of a web page. Write 6 to 10 bullets, one per line, each starting with '- ' and under 16 words. "
                "State only what the page says (what the tool or guide is for, what it needs, what it shows, limits and numbers exactly as printed). "
                "Use YOUR OWN words: never reuse a run of 4 or more consecutive words from the page, and never copy its headings or list items. "
                "Add nothing the page does not say. Plain text, nothing before or after the bullets.")
FACT_GRAM = 7              # a bullet may share short phrases with the page (the article gate is 10 words); only a 7-word run is a copy
MIN_FACTS = 3


def fact_bullets(raw, page_full, rejected=None):
    """Valid bullets from the model reply: list lines of 3+ words that share no 7-word run with the page itself. Rejected bullets are appended to `rejected`."""
    out = []
    for l in (raw or "").split("\n"):
        m = re.match(r"\s*(?:[-*\u2022]|\d+[.)])\s+(.+)", l)
        if not m:
            continue
        t = re.sub(r"\*\*|__", "", m.group(1)).strip()
        if len(_toks(t)) < 3:
            continue
        if overlap_grams(t, page_full, FACT_GRAM):
            if rejected is not None:
                rejected.append(t)
        else:
            out.append(t)
    return out[:10]


def distill_facts(page_full, meter, post=None):
    """-> bullet text for the writer, or '' when it could not be made (the caller then falls back to a SHORT slice of the page text).
    One retry when too few bullets survive the copy filter: the rejected bullets are named so the model words them differently."""
    user = "PAGE TEXT:\n" + page_full[:6000]
    good = []
    for rnd in (1, 2):
        data = D.anthropic(D.QA_MODEL, FACTS_SYSTEM, user, meter, post=post, max_tokens=600)
        raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        rejected = []
        for b in fact_bullets(raw, page_full, rejected):
            if b not in good:
                good.append(b)
        print("platform_poster: facts round %d kept %d dropped %d" % (rnd, len(good), len(rejected)))
        if len(good) >= MIN_FACTS or not rejected:
            break
        user = ("PAGE TEXT:\n" + page_full[:6000] + "\n\nThese earlier bullets were too close to the page. Write 6 to 10 NEW bullets about the same things with completely different "
                "wording and sentence shapes:\n" + "\n".join("- " + x for x in rejected[:8]))
    return "\n".join("- " + x for x in good[:10]) if len(good) >= MIN_FACTS else ""


# ---------- fact-check (25 Nov): verdict FIRST, tolerant parse, strict retry, never 'not understood' on the first try ----------
PQA_SYSTEM = """You are a strict fact-check editor. You get an article, our own page text and the text of the pages the article links to.
Reply in EXACTLY this format and nothing else (no preamble, no JSON, no markdown):
VERDICT: PASS
or
VERDICT: FAIL
REASON: "<the exact sentence or phrase from the article, 25 words at most>" | <why, 15 words at most>
(one REASON line per problem, at most 4)
FAIL if ANY of: a number, percent, date or factual claim is not supported by the linked page text given; a claim about what OUR tool shows, does, needs or limits is not in our page text; a claim sounds invented or is attributed to a source that does not say it;
it names another creator, brand-new product claim or @handle; it promises results the tool cannot give; the InayaFlow link is pushy or off-topic; it reads as an advert instead of a useful article; it makes a legal, medical or financial claim without a source.
PASS only when every checkable claim is supported."""
PQA_STRICT = ("Your reply must start with the exact line 'VERDICT: PASS' or 'VERDICT: FAIL'. For FAIL add up to 4 lines 'REASON: \"<exact article sentence>\" | <why>'. "
              "Nothing else.\n\n" + PQA_SYSTEM)
VERDICT_RE = re.compile(r"VERDICT\W{0,6}(PASS|FAIL)", re.I)


def parse_qa(raw):
    """-> (verdict 'PASS'|'FAIL'|None, [reasons]). FAIL wins: the word FAIL anywhere in the reply, or a FAIL verdict anywhere, makes it FAIL."""
    raw = raw or ""
    reasons = [re.sub(r"\s+", " ", m).strip()[:600] for m in re.findall(r"(?ims)^\W*REASON\W*[:\-]\s*(.+?)(?=^\W*(?:REASON|VERDICT)\b|\Z)", raw)]
    j = D.qa_json(raw)
    jv = j.get("verdict") if isinstance(j, dict) else None
    if isinstance(j, dict) and not reasons:
        reasons = [str(x)[:300] for x in (j.get("reasons") or [])]
    verdicts = {v.upper() for v in VERDICT_RE.findall(raw)} | ({jv.upper()} if isinstance(jv, str) else set())
    if "FAIL" in verdicts or re.search(r"\bFAIL\b", raw):
        if not reasons:
            reasons = (D.qa_lenient(raw) or {}).get("reasons") or ["QA said FAIL without a reason"]
        return "FAIL", reasons[:6]
    if "PASS" in verdicts or re.match(r"(?i)\s*PASS\b", raw):
        return "PASS", []
    return None, []


def qa_check(art, target_url, source_texts, page_text, meter, post=None):
    """-> (ok, reasons). Two asks at most: the normal prompt, then a strict short one. Both unreadable -> 'QA answer not understood'."""
    srcs = "\n\n".join("SOURCE %s:\n%s" % (u, (t or "")[:3500]) for u, t in source_texts.items())
    user = "OUR PAGE %s (our own page text):\n%s\n\n%s\n\nARTICLE TITLE: %s\n\n%s" % (target_url, page_text[:2500], srcs, art["title"], art["body"])
    for system, mt in ((PQA_SYSTEM, 900), (PQA_STRICT, 400)):
        data = D.anthropic(D.QA_MODEL, system, user[:60000], meter, post=post, max_tokens=mt)
        raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        verdict, reasons = parse_qa(raw)
        if verdict:
            return verdict == "PASS", reasons
    return False, ["QA answer not understood"]


def qa_repair_article(art, reasons, meter, post=None):
    """ONE cheap rewrite (Haiku, no tools) of ONLY the sentences the fact-check named as unsupported: reword to a general, true statement or delete. -> art dict or None."""
    body = art["body"]
    spans = sentence_spans(body)
    if not spans or not reasons:
        return None
    numbered = "\n".join("%d. %s" % (i + 1, body[a:b]) for i, (a, b) in enumerate(spans))
    system = ("You are a copy editor. A fact-checker found unsupported claims in an article. From the numbered sentences pick ONLY the ones that carry those claims. "
              "For each, give a replacement that drops the unsupported claim and says something general and true that needs no source, or an empty string to delete the sentence. "
              "Never add numbers, names, statistics or claims about what our tool does. Keep every Markdown link [text](url) that sits in a sentence you keep or replace EXACTLY as it is. "
              'Answer ONLY a JSON object like {"4":"new sentence","9":""} with at most 6 entries.')
    data = D.anthropic(D.QA_MODEL, system, "FACT-CHECKER REASONS:\n- " + "\n- ".join(reasons[:6]) + "\n\nSENTENCES:\n" + numbered[:12000], meter, post=post, max_tokens=700)
    j = D.qa_json("".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"))
    if not isinstance(j, dict) or not j or len(j) > 6:
        return None
    new = body
    for k in sorted(j, key=lambda x: -int(x) if str(x).isdigit() else 0):
        if not str(k).isdigit() or not (1 <= int(k) <= len(spans)) or not isinstance(j[k], str) or "\n" in j[k].strip():
            return None
        a, b = spans[int(k) - 1]
        rep = j[k].strip()
        old = body[a:b]
        m = re.match(r"\s*([-*]|\d+[.)])\s", old)
        if rep and m and not re.match(r"([-*]|\d+[.)])\s", rep):
            rep = m.group(0) + rep
        new = new[:a] + rep + new[b:]
    new = re.sub(r"(?m)^[ \t]*([-*]|\d+[.)])[ \t]*$\n?", "", new)          # a deleted list item leaves no empty bullet
    new = re.sub(r"[ \t]{2,}", " ", new)
    new = re.sub(r"\n{3,}", "\n\n", new).strip()
    return dict(art, body=new) if new != body else None


# ---------- reviser (25 Nov r2): attempt 2 fixes the draft it already has, without web tools (a second research run cost up to $0.38) ----------
def revise_system(platform):
    return writer_system(platform) + "\nYou are now REVISING your own earlier draft. Do NOT search or fetch anything. Keep the same sources and links, change only what the failed checks name, and answer in the same OUTPUT FORMAT with the full article."


def run_reviser(platform, user, art, problems, meter, post=None):
    draft = "TITLE: %s\nSOURCES: %s\n===BODY===\n%s" % (art["title"], " | ".join(art.get("sources") or []) or "none", art["body"])
    msg = user + "\n\nYOUR DRAFT:\n" + draft + "\n\nIT FAILED THESE CHECKS, fix them (quoted phrases must not appear again; remove or neutralise any unsupported claim): " + "; ".join(problems)
    data = D.anthropic(D.WRITER, revise_system(platform), msg, meter, post=post, max_tokens=D.WRITER_MAX_TOKENS)
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


# ---------- writer ----------
def writer_system(platform):
    s = SPECS[platform]
    return ("""You write one short article for the company InayaFlow, to be posted on %s. Voice: "we" (never I, me, my, mine, myself). Plain, friendly, specific English. The product is called InayaFlow (never "Genie").
FORMAT OF THE ARTICLE: %s
RULES (all checked by code afterwards; a break means nothing is published):
- %d to %d words of Markdown (## headings, short paragraphs, "- " lists, [text](url) links, **bold**). Your OWN angle and wording: do not copy the InayaFlow page; add examples and reasoning of your own.
- Real numbers only, found with web_search / web_fetch in official or primary pages you OPENED. Every paragraph with a number, percent or amount needs a Markdown link to that page, and the number must be printed there exactly as you write it. If you cannot source a number leave it out (an article with no numbers and no external link is fine). Pages on tiktok.com/support or creator-academy usually come back empty: do not cite them.
- Facts about our own page and tool may come ONLY from the FACTS list in the user message (short notes in other words). Do not add features, limits, outputs or numbers of our tool that are not in it; for anything else about the tool stay general or say nothing. Never write that the tool "tells", "shows" or "recommends" something unless a FACT says so.
- Exactly ONE link to the target InayaFlow page, as [LINK TEXT](URL), placed where it truly helps (not in the first or last paragraph); the type of LINK TEXT is set per article in the user message and checked by code. Never write the web address inayaflow.ai as plain text outside that link (as the link text itself it is allowed when the type says so).
- Say everything in your own words: never copy 10 or more words in a row from any InayaFlow page (code rejects a shared 10-word run; avoid stock lists like 'followers, following, total likes and video count': give your own example instead). Write as 'we' for InayaFlow; never 'I', 'me' or 'my'.
- Write plain how-to advice. No comparisons or performance claims ("X beats Y", "works better", "more reach") unless a source you opened says so and you link it. Never name a mode, plan, tier, limit or quantity of our tool unless a FACT states it exactly (no "a handful of", no invented product names). When unsure, leave the sentence out.
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
        user = ("TARGET PAGE: %s\nIT IS: %s (%s)\nTOPIC AREA: %s\nFACTS ABOUT OUR PAGE (the only things you may say about it or its tool; use your own wording):\n%s\n\n"
                "ARTICLES WE ALREADY PUBLISHED ON OUR OWN BLOGS (do not repeat these angles):\n%s\n\n%s\n\nWrite today's %s article now.") % (
            url, what, kind, topic, facts or page_text[:2500], "\n".join("- " + t for t in past_titles[-25:]) or "(none yet)", D.anchor_prompt(anchor), pl.capitalize())
        if not dry:
            state.setdefault("attempts", {})[today + pl] = state.get("attempts", {}).get(today + pl, 0) + 1
        save()
        problems, art, source_texts = ["no draft"], None, {}
        left_after = len(todo) - idx - 1
        repairs_left = REPAIR_ROUNDS
        fetched = {}

        def evaluate(a):
            st = {}
            for u in sorted({u for _, u in LINK_RE.findall(a["body"]) if "inayaflow.ai" not in u})[:6]:
                s2, _, h2 = getter(u)
                ours = c.html_text(h2, 20000) if s2 == 200 else ""
                theirs = fetched.get(u, "")[:20000]
                st[u] = theirs if len(theirs) > len(ours) else ours
            pr = check_post(a, url, pl, page_full, st, site_corpus, todays, past_sh, anchor=anchor)
            if any(len(t) < D.MIN_SOURCE_CHARS for t in st.values()):
                pr.append("a cited source page is unreadable")
            return pr, st
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
                    art = parse_writer(D.run_writer(writer_system(pl), user + extra, meter, post=post_ai, fetched=fetched))
            except Exception as e:
                problems = ["writer error %s" % type(e).__name__]
                break
            if not art:
                problems = ["writer output not in the required format"]
                continue
            problems, source_texts = evaluate(art)
            while problems and repairable(problems) and repairs_left > 0:
                repairs_left -= 1                     # up to REPAIR_ROUNDS targeted rewrites per platform per day; each result is checked again from scratch
                fixed = None
                # the repair must leave room for the QA gate after it and for the platforms still to come
                if meter.cost + 2 * D.QA_RESERVE + left_after * (D.WRITER_RESERVE + D.QA_RESERVE) <= meter.cap + 1e-9:
                    try:
                        fixed = repair_article(art, site_corpus, todays, meter, post=post_ai)
                    except D.CapReached:
                        problems = problems + ["daily cost cap reached during repair"]
                        repairs_left = -1
                        break                          # same as the writer's cap path: stop spending, this draft is FAILED
                    except Exception as e:
                        print("platform_poster: repair skipped (%s)" % type(e).__name__)
                if not fixed:
                    break
                p2, st2 = evaluate(fixed)
                print("platform_poster: repair %s problems %d -> %d" % (pl, len(problems), len(p2)))
                if len(p2) <= len(problems):
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
                    ok, reasons = qa_check(art, url, source_texts, page_text, meter, post=post_ai)
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
                p2, st2 = evaluate(fixed)
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
