"""Directory link checker. Daily, cloud only. No sign-ups, no form submits: GET requests only.

Reads the submitted sites from the PRIVATE logs checkout (LOGS_DIR, default /tmp/logs, branch ops-directory-logs),
looks for our listing on each site and for an <a href> to inayaflow.ai, and writes
ops/live_links.md and ops/live_links_new.md in LOGS_DIR. PUBLIC stdout = counts only (no site names, no URLs).
"""
import datetime, json, os, re, time, urllib.request, urllib.error, urllib.parse
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS = os.environ.get("LOGS_DIR", "/tmp/logs")
OPS = os.path.join(LOGS, "ops")
UA = "Mozilla/5.0 (compatible; InayaFlowLinkChecker/1.0; +https://inayaflow.ai)"
TIMEOUT = 15
GAP = float(os.environ.get("REQ_GAP", "1.0"))
MAX_REQ_PER_SITE = 24
OURS = ("inayaflow.ai",)
SEED = ["aitoolsdirectory.com", "thesaasdir.com", "tinylaunch.com", "launchingnext.com", "alternativeto.net",
        "saashub.com", "sourceforge.net", "starterstory.com", "startupstash.com", "uneed.best",
        "sideprojectors.com", "producthunt.com", "crunchbase.com", "peerlist.io", "indiehackers.com"]
SEED_NOTES = {"thesaasdir.com": "slug inayaflow", "tinylaunch.com": "launch 21 Dec"}
GUESS = ["/tool/inayaflow", "/tools/inayaflow", "/product/inayaflow", "/products/inayaflow", "/startup/inayaflow",
         "/ai/inayaflow", "/projects/inayaflow", "/project/inayaflow", "/p/inayaflow", "/item/inayaflow",
         "/software/inayaflow", "/inayaflow", "/tool/inayaflow-ai", "/products/inayaflow-ai"]
SEARCH = ["/?s=inayaflow", "/search?q=inayaflow", "/?q=inayaflow", "/search?query=inayaflow"]
_last = [0.0]


def nkey(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def reg_domain(host):
    parts = (host or "").lower().split(":")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []  # (href, rel)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            a = dict(attrs)
            if a.get("href"):
                self.links.append((a["href"], (a.get("rel") or "").lower()))


def rel_of(rels):
    """Strongest value for our links: followed beats nofollow/ugc/sponsored."""
    if not rels:
        return None
    toks = [set(r.split()) for r in rels]
    if any(not (t & {"nofollow", "ugc", "sponsored"}) for t in toks):
        return "followed"
    for k in ("sponsored", "ugc", "nofollow"):
        if any(k in t for t in toks):
            return k
    return "nofollow"


def is_ours(href):
    try:
        h = (urllib.parse.urlparse(href).hostname or "").lower()
    except Exception:
        return False
    return h in OURS or h.endswith(".inayaflow.ai")


def our_page(links):
    """Which of OUR pages the link points to (path, '/' = homepage): the first link of the strongest kind. '' when there is none."""
    ours = [(h, r) for h, r in links if is_ours(h)]
    if not ours:
        return ""
    best = rel_of([r for _h, r in ours])
    for h, r in ours:
        if rel_of([r]) == best:
            path = urllib.parse.urlparse(h).path or "/"
            return "/" if path.strip("/") == "" else "/" + path.strip("/")
    return ""


def analyse(html):
    p = Links()
    try:
        p.feed(html)
    except Exception:
        pass
    ours = [rel for href, rel in p.links if is_ours(href)]
    return (rel_of(ours) if ours else None), p.links


def get(url):
    """Returns (status, body, blocked). 1 request/second, timeouts, never raises."""
    wait = GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.5",
                                               "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(2_500_000).decode("utf-8", "replace")
            status, hdr = r.status, r.headers
    except urllib.error.HTTPError as e:
        status, hdr = e.code, e.headers
        try:
            body = e.read(200_000).decode("utf-8", "replace")
        except Exception:
            body = ""
    except Exception:
        return 0, "", False
    low = body.lower()
    cf = "cloudflare" in str(hdr.get("server", "")).lower() or "cf-ray" in str(hdr).lower() or "cf-mitigated" in str(hdr).lower()
    blocked = (status in (403, 429, 503) and (cf or "just a moment" in low)) or \
              (status == 200 and "just a moment" in low[:3000] and "cloudflare" in low[:8000])
    return status, body, blocked


def sitemap_hits(base, budget):
    found = []
    st, body, bl = get(base + "/sitemap.xml")
    budget[0] -= 1
    if st != 200:
        return found, bl
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)
    found += [u for u in locs if "inayaflow" in u.lower() and not u.endswith(".xml")]
    for s in [u for u in locs if u.endswith(".xml")][:4]:
        if found or budget[0] <= 0:
            break
        st, b, _ = get(s)
        budget[0] -= 1
        if st == 200:
            found += [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", b)
                      if "inayaflow" in u.lower() and not u.endswith(".xml")]
    return found[:3], bl


def check_site(site):
    """site: dict(domain, logged urls). Returns dict(status, rel, url)."""
    domain = site["domain"]
    base = "https://" + domain
    host_ok = lambda u: reg_domain(urllib.parse.urlparse(u).hostname or "") == reg_domain(domain)
    state = {"blocked": False, "reached": False}
    tried = set()
    pages = []  # existing listing pages without a link to us
    budget = [MAX_REQ_PER_SITE]

    def fetch(u):
        if u in tried or budget[0] <= 0:
            return None
        tried.add(u)
        budget[0] -= 1
        st, body, bl = get(u)
        state["blocked"] = state["blocked"] or bl
        if st:
            state["reached"] = True
        if st != 200 or bl:
            return None
        return body

    def listing(u):
        body = fetch(u)
        if body is None:
            return None
        rel, lk = analyse(body)
        if rel:
            return {"status": "LIVE", "rel": rel, "url": u, "page": our_page(lk)}
        if "inayaflow" in body.lower() and u.rstrip("/") != base:
            pages.append(u)
        return None

    for u in site.get("logged", []):
        if host_ok(u):
            r = listing(u)
            if r:
                return r
    hits, bl = sitemap_hits(base, budget)
    state["blocked"] = state["blocked"] or bl
    for u in hits:
        r = listing(u)
        if r:
            return r
    for path in SEARCH[:2]:
        body = fetch(base + path)
        if body is None:
            continue
        _, links = analyse(body)
        seen = []
        for href, _rel in links:
            full = urllib.parse.urljoin(base + path, href)
            if host_ok(full) and "inayaflow" in full.lower().split("?")[0] and full not in seen:
                seen.append(full)
        for h in seen[:3]:
            r = listing(h)
            if r:
                return r
    for g in GUESS:
        r = listing(base + g)
        if r:
            return r
    if pages:
        return {"status": "LIVE", "rel": "no link", "url": pages[0]}
    if state["blocked"] and not state["reached"]:
        return {"status": "BLOCKED_CHECK", "rel": "", "url": ""}
    if state["blocked"]:
        return {"status": "BLOCKED_CHECK", "rel": "", "url": ""}
    if not state["reached"]:
        return {"status": "UNREACHABLE", "rel": "", "url": ""}
    return {"status": "PENDING", "rel": "", "url": ""}


# ---------- inputs ----------
def load_json(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def read(p):
    try:
        return open(p, encoding="utf-8").read()
    except Exception:
        return ""


def name_domains():
    """name key -> domain, from the public data files."""
    m = {}
    data = os.path.join(ROOT, "data")
    for r in load_json(os.path.join(data, "directories.json"), []):
        if r.get("submit_url"):
            m[nkey(r["name"])] = reg_domain(urllib.parse.urlparse(r["submit_url"]).hostname or "")
    for r in load_json(os.path.join(data, "directories_candidates.json"), {}).get("sites", []):
        dom = r.get("domain") or reg_domain(urllib.parse.urlparse(r.get("submit_url", "")).hostname or "")
        if dom:
            m[nkey(r["name"])] = reg_domain(dom)
    if os.path.isdir(data):
        for f in os.listdir(data):
            if f.startswith("directory_agent") and f.endswith(".json"):
                b = load_json(os.path.join(data, f), {})
                for r in (b.get("sites") if isinstance(b, dict) else b) or []:
                    if isinstance(r, dict) and r.get("url") and r.get("name"):
                        m[nkey(r["name"])] = reg_domain(urllib.parse.urlparse(r["url"]).hostname or "")
    return m


URL_RE = re.compile(r"https?://[^\s|)>\]\"']+")


def load_sites():
    nd = name_domains()
    sites = {}

    def add(domain, logged=()):
        domain = reg_domain(domain)
        if not domain or "." not in domain or domain in OURS:
            return
        s = sites.setdefault(domain, {"domain": domain, "logged": []})
        for u in logged:
            if u and u not in s["logged"]:
                s["logged"].append(u)
    ast = load_json(os.path.join(OPS, "directory_agent_state.json"), {})
    ost = load_json(os.path.join(OPS, "directory_state.json"), {})
    unresolved = 0
    subs = [(v.get("name", k), k, None) for k, v in ast.items()
            if v.get("status") in ("SUBMITTED", "UNCONFIRMED", "ALREADY_LISTED")]
    subs += [(k, k, v.get("url")) for k, v in ost.items() if str(v.get("result", "")).startswith("SUBMITTED")]
    for name, key, url in subs:
        dom = nd.get(nkey(name)) or nd.get(nkey(key))
        if not dom and "." in key:
            dom = key
        if dom:
            add(dom, [url])
        else:
            unresolved += 1
    for f in ("directory_log.md", "directory_agent_log.md"):
        for u in URL_RE.findall(read(os.path.join(OPS, f))):
            h = reg_domain(urllib.parse.urlparse(u).hostname or "")
            if h in sites and "inayaflow" in u.lower():
                add(h, [u])
    man = os.path.join(OPS, "manual_submissions.md")
    if not os.path.exists(man):
        os.makedirs(OPS, exist_ok=True)
        with open(man, "w", encoding="utf-8") as fh:
            fh.write("# Manual submissions (private extra list for the link checker)\n\n"
                     "One site per line: `- domain - optional note`. Add new manual listings here.\n\n")
            for d in SEED:
                fh.write(f"- {d}" + (f" - {SEED_NOTES[d]}" if d in SEED_NOTES else "") + "\n")
    for line in read(man).splitlines():
        m = re.match(r"\s*[-*]\s*([a-z0-9.-]+\.[a-z]{2,})\b", line, re.I)
        if m:
            add(m.group(1))
    return list(sites.values()), unresolved


# ---------- previous state / output ----------
def load_prev():
    prev = {}
    for line in read(os.path.join(OPS, "live_links.md")).splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 6 and c[0] not in ("site", "---") and not c[0].startswith(("-", "#")) and "." in c[0]:
            prev[c[0]] = {"status": c[1], "rel": c[2], "url": c[3], "first": c[4], "last": c[5], "page": c[6] if len(c) > 6 else ""}
    return prev


def main():
    today = datetime.date.today().isoformat()
    sites, unresolved = load_sites()
    prev = load_prev()
    rows, new = [], []
    cnt = {"checked": 0, "followed": 0, "nofollow": 0, "pending": 0, "gone": 0, "blocked": 0}
    for s in sites:
        d = s["domain"]
        r = check_site(s)
        cnt["checked"] += 1
        p = prev.get(d, {})
        was_live = p.get("status") == "LIVE"
        status, rel, url = r["status"], r["rel"], r["url"]
        first = p.get("first", "") if was_live else ""
        if status == "LIVE":
            if not first:
                first = today
                new.append((d, rel, url))
            cnt["followed" if rel == "followed" else "nofollow"] += 1
        elif status == "PENDING":
            if was_live:
                status, rel, url, first = "GONE", p.get("rel", ""), p.get("url", ""), p.get("first", "")
                cnt["gone"] += 1
            else:
                cnt["pending"] += 1
        else:  # BLOCKED_CHECK / UNREACHABLE: could not check, keep last known live state
            cnt["blocked"] += 1
            if was_live:
                status, rel, url, first = "LIVE", p.get("rel", ""), p.get("url", ""), p.get("first", "")
        page = r.get("page", "") if (status == "LIVE" and r["status"] == "LIVE") else (p.get("page", "") if status in ("LIVE", "GONE") else "")
        rows.append((d, status, rel, url, first, today, page))
    rows.sort(key=lambda x: (x[1] != "LIVE", x[0]))
    os.makedirs(OPS, exist_ok=True)
    with open(os.path.join(OPS, "live_links.md"), "w", encoding="utf-8") as f:
        f.write(f"# Live links check (last run {today})\n\nStatus: LIVE (link rel = followed / nofollow / ugc / sponsored / no link), "
                "PENDING (no listing found yet), GONE (was live, now missing), BLOCKED_CHECK (bot wall, could not check), "
                "UNREACHABLE.\n\n| site | status | link rel | listing URL | first seen live | last checked | our page |\n|---|---|---|---|---|---|---|\n")
        for r in rows:
            f.write("| " + " | ".join(r) + " |\n")
    if new:
        path = os.path.join(OPS, "live_links_new.md")
        fresh = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", encoding="utf-8") as f:
            if fresh:
                f.write("# Newly live links (append only)\n\n| date | site | link rel | listing URL |\n|---|---|---|---|\n")
            for d, rel, url in new:
                f.write(f"| {today} | {d} | {rel} | {url} |\n")
    print(f"checked={cnt['checked']} live_followed={cnt['followed']} live_nofollow={cnt['nofollow']} "
          f"pending={cnt['pending']} gone={cnt['gone']} blocked_or_unreachable={cnt['blocked']} "
          f"unresolved_names={unresolved} new_today={len(new)}")


if __name__ == "__main__":
    main()
