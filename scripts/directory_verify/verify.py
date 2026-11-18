"""Directory verify sweeper (18 Nov 2026): finishes directory sign-ups that wait on a mail in the contact@ mailbox.

Why: the browser-use directory agent reads confirmation mail only while it is on a site. Mail that arrives after its
session (or that it used without telling anyone) stayed "NOT HANDLED" in the mail router's audit and nobody acted.

What it does, every run (GitHub Actions only), for mail of the last 72 h in INBOX + Junk that is older than 20 min
and not yet flagged IFDirHandled / IFDirSeen:
  - the SENDER's domain must be a directory we know (lists in data/ + the agent's state). Anything else: not touched, listed for a human.
  - newsletter double opt-in mail: never opened (not needed for a listing), flagged handled.
  - a confirm/verify link ON THE SENDER'S OWN DOMAIN: opened once in a headless browser (phase 2, --open). Off-domain links are never opened.
  - login codes / magic sign-in links: only useful inside a live browser session, so they are closed as expired and the
    site is put on the agent's retry list (the agent logs in again and reads a fresh code itself).
  - anything else (e.g. "there is a problem with your listing"): listed for a human with a scrubbed excerpt.
Never: solves CAPTCHAs, pays, fills in forms, sends mail, deletes or moves mail, marks mail read (BODY.PEEK only).
PUBLIC repo: stdout = sender domain + outcome code + counts. Subjects, excerpts and link paths go to the PRIVATE repo
(workflow pushes ops/directory_verify_*.md|json to imran0786/genie, branch ops-directory-logs).
"""
import datetime, email, hashlib, html, imaplib, json, os, re, sys
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOST = os.environ.get("IMAP_HOST", "mail.privateemail.com")
USER = os.environ.get("CONTACT_EMAIL", "contact@inayaflow.ai")
HOURS = int(os.environ.get("VERIFY_HOURS", "72"))
MIN_AGE_MIN = int(os.environ.get("VERIFY_MIN_AGE_MIN", "20"))   # younger mail may belong to a running agent session
SESSION_TTL_MIN = int(os.environ.get("VERIFY_SESSION_TTL_MIN", "30"))  # codes / magic links older than this are dead
MAX_OPEN = int(os.environ.get("VERIFY_MAX_OPEN", "8"))
MAX_OPEN_TRIES = 2
DRY = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
OPS = os.path.join(ROOT, "ops")
LOG_PATH = os.path.join(OPS, "directory_verify_log.md")
DRY_PATH = os.path.join(OPS, "directory_verify_dryrun.md")
NEEDS_PATH = os.path.join(OPS, "directory_verify_needs_human.md")
VSTATE_PATH = os.path.join(OPS, "directory_verify_state.json")
AGENT_STATE_PATH = os.path.join(OPS, "directory_agent_state.json")
EXTRA_DOMAINS = os.environ.get("VERIFY_EXTRA_DOMAINS", "")

KW_DONE, KW_SEEN = "IFDirHandled", "IFDirSeen"
KW_ROUTE_VERIFY, KW_ROUTE_PREFIX = "IFRoute_DIRECTORY_VERIFY", "IFRoute_"

VERIFY_SUBJ_RE = re.compile(r"confirm|verif|activat|magic.?link|sign.?in|log.?in|one.?time|\bcode\b|\botp\b|complete your", re.I)
NEWSLETTER_RE = re.compile(r"subscription|subscrib|newsletter|digest|weekly", re.I)
CONFIRM_RE = re.compile(r"confirm|verif|activat|validat", re.I)
MAGIC_RE = re.compile(r"magic|sign.?in|log.?in|one.?time|\botp\b|passcode|\bcode\b", re.I)
LINK_OK_RE = re.compile(r"confirm|verif|activat|validat|token|magic|auth|callback|sign-?in|login|otp|code=", re.I)
LINK_BAD_RE = re.compile(r"unsubscribe|opt-?out|preferences|\.(png|jpe?g|gif|svg|css|js|ico)(\?|$)|privacy|terms|pricing|checkout|"
                         r"billing|payment|upgrade|/plans?\b|sponsor|advertis", re.I)
CAPTCHA_RE = re.compile(r"verify you are human|are you a robot|i'?m not a robot|just a moment|checking your browser|"
                        r"unusual traffic|captcha|attention required", re.I)
CODE_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
# site states (from the agent) where a confirmation would change nothing
CLOSED = {"PAID", "DEAD", "SOCIAL_LOGIN_ONLY", "BADGE_REQUIRED", "PHONE_REQUIRED", "NO_FORM"}
FINISHED = CLOSED | {"SUBMITTED", "ALREADY_LISTED", "UNCONFIRMED"}
HANDLED_OUTCOMES = {"SKIPPED_NEWSLETTER", "SITE_CLOSED", "CONFIRMED_LINK_OPENED", "SESSION_ONLY_SITE_FINISHED", "SESSION_ONLY_EXPIRED"}
HUMAN_OUTCOMES = {"UNKNOWN_SENDER", "LINK_OFF_DOMAIN", "NO_ACTION_FOUND", "CAPTCHA", "OPEN_FAILED"}


def reg_domain(host):  # same rule as the agent and the old bot
    parts = (host or "").lower().split(":")[0].strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


def sender_domain(frm):
    a = parseaddr(frm or "")[1].lower()
    return reg_domain(a.split("@", 1)[1]) if "@" in a else ""


def load_json(path, default):
    try:
        return json.load(open(path, encoding="utf8"))
    except Exception:
        return default


def known_domains(root=ROOT, agent_state=None, extra=""):
    """Registered domains of every directory in our lists, batch files and the agent's state."""
    out = set()

    def add(url):
        h = urlparse(url if "://" in str(url) else "https://" + str(url)).hostname
        if h and "." in h:
            out.add(reg_domain(h))

    data = os.path.join(root, "data")
    for d in load_json(os.path.join(data, "directories.json"), []):
        add(d.get("submit_url", ""))
    for c in load_json(os.path.join(data, "directories_candidates.json"), {}).get("sites", []):
        add(c.get("submit_url", ""))
        add(c.get("domain", ""))
    try:
        names = [f for f in os.listdir(data) if f.startswith("directory_agent") and f.endswith(".json")]
    except Exception:
        names = []
    for f in names:
        for s in load_json(os.path.join(data, f), {}).get("sites", []):
            add(s.get("url", ""))
    for k in (agent_state or {}):
        if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", k):
            out.add(reg_domain(k))
    for l in re.split(r"[\s,]+", extra or ""):
        if "." in l and not l.startswith("#"):
            out.add(reg_domain(l))
    out.discard("")
    return out


def mail_text(msg):
    out = []
    try:
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    out.append(part.get_content())
                except Exception:
                    pass
    except Exception:
        pass
    return "\n".join(out)


def all_links(text):
    urls = re.findall(r"https?://[^\s<>\"')\]]+", html.unescape(text or ""))
    seen, out = set(), []
    for u in urls:
        u = u.rstrip(".,;")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def confirm_links(text, sender_dom):
    """(own, off): confirm-looking https links on the sender's OWN registered domain, and confirm-looking links elsewhere."""
    own, off = [], []
    for u in all_links(text):
        if LINK_BAD_RE.search(u) or not LINK_OK_RE.search(u):
            continue
        p = urlparse(u)
        if p.scheme == "https" and p.hostname and reg_domain(p.hostname) == sender_dom:
            own.append(u)
        else:
            off.append(u)
    own.sort(key=lambda u: 0 if CONFIRM_RE.search(u) else 1)
    return own, off


def find_code(text):
    for m in CODE_RE.finditer(re.sub(r"https?://\S+", " ", text or "")):
        if not re.fullmatch(r"20\d\d", m.group(1)):
            return m.group(1)
    return None


def plain(text):
    t = re.sub(r"<(style|script).*?</\1>", " ", text or "", flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t))).strip()


def scrub(text, limit=600):
    """Excerpt for the private log: no full links, no codes, no addresses."""
    t = re.sub(r"https?://([^/\s<>\"')\]]+)[^\s<>\"')\]]*", lambda m: "[link:%s]" % m.group(1).lower(), text or "")
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", t)
    t = re.sub(r"(?<!\d)\d{4,8}(?!\d)", "[code]", t)
    return t.replace("|", "/")[:limit]


def link_label(u):
    p = urlparse(u)
    return "%s%s" % (p.hostname or "", re.sub(r"[A-Za-z0-9_-]{16,}", "[token]", p.path or "")[:60])


def decide(subject, sender_dom, text, known, site_status, age_min, routed_verify=True):
    """Pure decision for one mail. Returns (outcome, link or None).
    outcome: IGNORE | WAIT | UNKNOWN_SENDER | SKIPPED_NEWSLETTER | SITE_CLOSED | OPEN_LINK | SESSION_ONLY_SITE_FINISHED |
             SESSION_ONLY_EXPIRED | LINK_OFF_DOMAIN | NO_ACTION_FOUND"""
    looks_verify = bool(VERIFY_SUBJ_RE.search(subject or ""))
    if not routed_verify and not looks_verify:
        return "IGNORE", None
    if sender_dom not in known:
        return ("UNKNOWN_SENDER" if routed_verify else "IGNORE"), None
    if age_min < MIN_AGE_MIN:
        return "WAIT", None
    if NEWSLETTER_RE.search(subject or ""):
        return "SKIPPED_NEWSLETTER", None
    if site_status in CLOSED:
        return "SITE_CLOSED", None
    body = plain(text)
    own, off = confirm_links(text, sender_dom)
    magic = bool(MAGIC_RE.search(subject or "")) and not CONFIRM_RE.search(subject or "")
    code = find_code((subject or "") + " " + body)
    if own and not magic:
        return "OPEN_LINK", own[0]
    if magic or (code and looks_verify):
        if site_status in FINISHED:
            return "SESSION_ONLY_SITE_FINISHED", None
        return ("SESSION_ONLY_EXPIRED" if age_min >= SESSION_TTL_MIN else "WAIT"), None
    if off:
        return "LINK_OFF_DOMAIN", None
    return "NO_ACTION_FOUND", None


def open_link(ctx, url, sender_dom):
    """Open one confirm link. Returns (outcome, detail). Clicks at most one plain 'Confirm/Verify' button. Never fills anything."""
    pg = ctx.new_page()
    try:
        pg.set_default_timeout(15000)
        resp = pg.goto(url, wait_until="domcontentloaded", timeout=40000)
        pg.wait_for_timeout(3000)
        try:
            pg.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        status = resp.status if resp is not None else 0
        title = (pg.title() or "")[:80]
        try:
            body = pg.inner_text("body", timeout=5000)[:3000]
        except Exception:
            body = ""
        if CAPTCHA_RE.search(title) or (CAPTCHA_RE.search(body) and len(body) < 600) or pg.locator(
                "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile'], iframe[src*='challenges.cloudflare']").count():
            return "CAPTCHA", "robot check on the confirm page (not solved)"
        clicked = ""
        try:
            btn = pg.get_by_role("button", name=re.compile(r"^\s*(confirm|verify)( my)?( e-?mail( address)?| account| sign.?up)?\s*$", re.I))
            if btn.count() == 1 and reg_domain(urlparse(pg.url).hostname or "") == sender_dom:
                btn.first.click(timeout=5000)
                pg.wait_for_timeout(3000)
                clicked = " +confirm button"
                body = pg.inner_text("body", timeout=5000)[:3000]
        except Exception:
            pass
        final = urlparse(pg.url).hostname or ""
        detail = "http %s, landed on %s%s, title %r, page says: %s" % (status, final, clicked, title, scrub(re.sub(r"\s+", " ", body), 160))
        if status >= 400 or status == 0:
            return "OPEN_FAILED", detail
        return "CONFIRMED_LINK_OPENED", detail
    except Exception as e:
        return "OPEN_FAILED", type(e).__name__
    finally:
        try:
            pg.close()
        except Exception:
            pass


def folders(imap):
    names = ["INBOX"]
    typ, data = imap.list()
    for raw in data or []:
        line = raw.decode(errors="ignore")
        m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
        if m and re.search(r"junk|spam", m.group(1), re.I) and m.group(1) not in names:
            names.append(m.group(1))
    return names


def parse_flags(meta):
    m = re.search(r"FLAGS \(([^)]*)\)", meta)
    return m.group(1).split() if m else []


def site_status_for(dom, agent_state):
    e = agent_state.get(dom) or {}
    return e.get("status")


def main():
    do_open = "--open" in sys.argv
    pw = os.environ.get("CONTACT_EMAIL_PASSWORD")
    if not pw:
        print("verify: no mailbox password in env")
        return 2
    os.makedirs(OPS, exist_ok=True)
    agent_state = load_json(AGENT_STATE_PATH, {})
    vstate = load_json(VSTATE_PATH, {})
    for k in ("confirmed", "retry", "open_tries"):
        vstate.setdefault(k, {})
    known = known_domains(agent_state=agent_state, extra=EXTRA_DOMAINS)
    now = datetime.datetime.now(datetime.timezone.utc)
    today = now.date().isoformat()
    since = now - datetime.timedelta(hours=HOURS)
    imap = imaplib.IMAP4_SSL(HOST, 993)
    try:
        imap.login(USER, pw)
    except Exception:
        print("verify: IMAP LOGIN FAILED")
        return 3
    rows, counts, pending, opened = [], {}, 0, 0
    browser = ctx = pw_ctx = None
    try:
        for folder in folders(imap):
            try:
                if imap.select('"%s"' % folder, readonly=DRY)[0] != "OK":
                    continue
            except Exception:
                continue
            typ, data = imap.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"), "UNKEYWORD", KW_DONE, "UNKEYWORD", KW_SEEN)
            for uid in (data[0] or b"").split()[-400:]:
                t, d = imap.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
                if t != "OK" or not d or not isinstance(d[0], tuple):
                    continue
                flags = parse_flags(d[0][0].decode(errors="ignore"))
                routes = [f for f in flags if f.startswith(KW_ROUTE_PREFIX)]
                if routes and KW_ROUTE_VERIFY not in routes:
                    continue  # the router filed it elsewhere (status, newsletter, partner...)
                msg = email.message_from_bytes(d[0][1], policy=policy.default)
                try:
                    md = parsedate_to_datetime(str(msg.get("Date"))).astimezone(datetime.timezone.utc)
                except Exception:
                    md = now
                if md < since:
                    continue
                subj = re.sub(r"\s+", " ", str(msg.get("Subject", "")))[:120]
                dom = sender_domain(str(msg.get("From", "")))
                text = mail_text(msg)
                age = (now - md).total_seconds() / 60
                st = site_status_for(dom, agent_state)
                outcome, link = decide(subj, dom, text, known, st, age, routed_verify=KW_ROUTE_VERIFY in routes)
                if outcome in ("IGNORE", "WAIT"):
                    continue
                detail = ""
                if outcome == "OPEN_LINK":
                    detail = "link " + link_label(link)
                    if DRY or not do_open or opened >= MAX_OPEN:
                        pending += 1
                        outcome = "LINK_PENDING"
                    else:
                        if ctx is None:
                            from playwright.sync_api import sync_playwright
                            pw_ctx = sync_playwright().start()
                            browser = pw_ctx.chromium.launch(headless=True)
                            ctx = browser.new_context()
                        opened += 1
                        outcome, d2 = open_link(ctx, link, dom)
                        detail += "; " + d2
                        mid = hashlib.sha1(str(msg.get("Message-ID", subj)).encode()).hexdigest()[:12]
                        if outcome == "OPEN_FAILED":
                            vstate["open_tries"][mid] = vstate["open_tries"].get(mid, 0) + 1
                            if vstate["open_tries"][mid] < MAX_OPEN_TRIES:
                                outcome = "OPEN_FAILED_WILL_RETRY"
                        elif outcome == "CONFIRMED_LINK_OPENED":
                            vstate["confirmed"][dom] = today
                            if st not in FINISHED:
                                vstate["retry"][dom] = today  # account confirmed: the agent should log in and submit
                elif outcome == "SESSION_ONLY_EXPIRED":
                    vstate["retry"][dom] = today
                    detail = "login code / sign-in link is only valid inside a live session; site put on the agent's retry list"
                elif outcome == "SESSION_ONLY_SITE_FINISHED":
                    detail = "code / sign-in link belonged to a session that already ended with site status %s" % st
                elif outcome == "SITE_CLOSED":
                    detail = "site status %s: nothing to confirm" % st
                elif outcome == "SKIPPED_NEWSLETTER":
                    detail = "newsletter double opt-in, not needed for a listing; not opened"
                elif outcome == "LINK_OFF_DOMAIN":
                    detail = "confirm-looking links only on other domains (%s); not opened" % ", ".join(
                        sorted({(urlparse(u).hostname or "")[:40] for u in confirm_links(text, dom)[1]})[:3])
                if not DRY:
                    kw = KW_DONE if outcome in HANDLED_OUTCOMES else KW_SEEN if outcome in HUMAN_OUTCOMES else None
                    if kw:
                        imap.uid("STORE", uid, "+FLAGS", "(%s)" % kw)
                counts[outcome] = counts.get(outcome, 0) + 1
                print("RESULT | %s | %s" % (dom or "?", outcome), flush=True)
                rows.append({"mail": md.strftime("%m-%d %H:%M"), "dom": dom, "subj": subj.replace("|", "/")[:90], "outcome": outcome,
                             "state": st or "-", "detail": detail.replace("|", "/")[:400], "excerpt": scrub(plain(text))})
    finally:
        try:
            imap.logout()
        except Exception:
            pass
        for o, m in ((browser, "close"), (pw_ctx, "stop")):
            try:
                if o is not None:
                    getattr(o, m)()
            except Exception:
                pass
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    hdr = "| mail (UTC) | directory | subject | outcome | site state | detail |\n|---|---|---|---|---|---|\n"
    fmt = lambda r: "| %s | %s | %s | %s | %s | %s |" % (r["mail"], r["dom"], r["subj"], r["outcome"], r["state"], r["detail"])
    if DRY:
        with open(DRY_PATH, "w", encoding="utf8") as f:
            f.write("# Directory verify sweeper: DRY RUN %s (nothing opened, nothing flagged)\n\n%s%s\n\n## Excerpts (scrubbed)\n\n" % (
                stamp, hdr, "\n".join(fmt(r) for r in rows)))
            for r in rows:
                f.write("- **%s | %s | %s**: %s\n" % (r["mail"], r["dom"], r["subj"], r["excerpt"]))
    elif [r for r in rows if do_open or r["outcome"] != "LINK_PENDING"]:
        rows = [r for r in rows if do_open or r["outcome"] != "LINK_PENDING"]  # phase 1 leaves links to phase 2
        new = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf8") as f:
            if new:
                f.write("# Directory verify sweeper log (private)\n")
            f.write("\n## %s%s\n%s%s\n" % (stamp, " (links opened)" if do_open else "", hdr, "\n".join(fmt(r) for r in rows)))
        human = [r for r in rows if r["outcome"] in HUMAN_OUTCOMES]
        if human:
            new = not os.path.exists(NEEDS_PATH)
            with open(NEEDS_PATH, "a", encoding="utf8") as f:
                if new:
                    f.write("# Directory mail the robot could not finish (a person reads these; newest at the bottom)\n")
                for r in human:
                    f.write("\n- %s | %s | %s | **%s** | %s\n  - excerpt: %s\n" % (r["mail"], r["dom"], r["subj"], r["outcome"], r["detail"], r["excerpt"]))
        json.dump(vstate, open(VSTATE_PATH, "w", encoding="utf8"), indent=1, sort_keys=True)
    print("SUMMARY verify: dry=%s open=%s known_domains=%d mails=%d links_pending=%d %s" % (
        DRY, do_open, len(known), len(rows), pending, " ".join("%s=%d" % kv for kv in sorted(counts.items()))), flush=True)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("pending=%d\n" % pending)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("verify: ERROR %s" % type(e).__name__)
        sys.exit(1)
