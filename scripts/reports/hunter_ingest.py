"""Feeder for the 24/7 prospect hunter (22 Nov 2026).

The hunter is a cloud research job (scripts/hunter/hunter.py queues it in the job runner, branch ops-jobs). Its result
file (jobs/done/<id>-hunter-<page>...md) holds JSON lines, one per prospect it verified. This module (called from the
backlink batch) turns NEW results into:
  - outreach rows (blog articles / resource pages with an e-mail printed on the site's OWN page) -> ops/outreach_prospects.json
  - directory rows (free directories, launch sites, platforms that give a followed or nofollow link) -> data/directories_candidates.json
Nothing is trusted blindly: every row is checked for shape and deduplicated against everything already known and against the
outbox; the batch's own live checks (prepare_first for e-mails, the directory agent for sites) still run before anything goes out.
Public repo: this code never prints addresses, links or names (counts only).
"""
import json
import re
from urllib.parse import urlparse

import common as c

PAGE_MATCH = {"homepage": "home", "hashtag-strategy": "hashtag-strategy", "follower-checker": "followers", "money-calculator": "money",
              "shadowban-checker": "shadowban", "hashtag-generator": "hashtags", "hashtag-counter": "hashtag-counter",
              "engagement-rate": "engagement", "best-time": "best-time tool", "coins-to-usd": "coins", "username-generator": "username",
              "no-views": "no-views", "shadowban-guide": "shadowban-guide", "analyze": "analyze", "how-many-views": "how-many-views", "hooks": "hooks", "captions": "captions"}
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
BAD_LOCAL = re.compile(r"^(noreply|no-reply|donotreply|do-not-reply|abuse|privacy|dmca|legal|postmaster|webmaster@)", re.I)
MAIL_TYPES = {"blog_article", "resource_page"}
DIR_TYPES = {"directory", "launch_site", "platform"}


def clean(s, n):
    """Text from the hunter is untrusted: no HTML, no links, no addresses, no line breaks, no control characters, capped length."""
    s = re.sub(r"<[^>]*>", " ", str(s or ""))
    s = re.sub(r"https?://\S+|www\.\S+", " ", s)
    s = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", " ", s)
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s)
    s = re.sub(r"[<>{}`|]", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:n]


def parse_lines(text):
    """JSON objects from the lines of a result file (anything else is ignored)."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().strip("`")
        if line.startswith("{") and line.endswith("}"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def _host(u):
    try:
        return c.reg_domain(urlparse(u).hostname or "")
    except Exception:
        return ""


def outreach_row(d, job_id, n):
    """Row for ops/outreach_prospects.json or None. Needs: a real page URL, an e-mail printed on that site's own page, a known page of ours."""
    url, email, src = str(d.get("url", "")), str(d.get("email", "")).strip().lower(), str(d.get("email_source_url", ""))
    match = PAGE_MATCH.get(str(d.get("page", "")))
    if d.get("type") not in MAIL_TYPES or not match or not url.startswith(("https://", "http://")):
        return None
    if not EMAIL.match(email) or BAD_LOCAL.match(email) or email.endswith("@inayaflow.ai"):
        return None
    site = _host(url)
    if not site or _host(src) != site:            # the address must come from the SAME site's own page
        return None
    if c.reg_domain(email.split("@")[-1]) != site:  # a free-mail or third-party address is not the site's own contact
        return None
    return {"n": n, "site": site, "page": url.split()[0][:300], "page_date": "", "topic": clean(d.get("topic", ""), 80), "match": match,
            "contact": src, "emails": [email], "pri": "B", "note": "hunter %s" % job_id}


def directory_row(d, job_id, today):
    """Row for data/directories_candidates.json or None. Needs: free = yes, a followed / nofollow link seen, a submit URL."""
    url = str(d.get("url", ""))
    rel = str(d.get("rel", "")).lower()
    if d.get("type") not in DIR_TYPES or str(d.get("free", "")).lower() != "yes" or rel not in ("followed", "nofollow"):
        return None
    if not url.startswith("https://") or not str(d.get("rel_evidence", "")).strip():
        return None
    dom = _host(url)
    if not dom or dom == "inayaflow.ai":
        return None
    name = clean(d.get("name"), 60) or dom
    return {"name": name, "domain": dom, "submit_url": url, "source": "hunter %s" % job_id, "their_category": "", "their_auto_flag": "",
            "their_notes": "", "their_status": None, "http": None, "final_url": url, "free_check": "unchecked", "captcha_words_on_page": False,
            "has_form_on_page": False, "quality": "unknown", "usable": False, "followed": "unknown", "checked": today,
            "judge_reason": "hunter says %s: %s" % (rel, clean(d.get("rel_evidence", ""), 120)), "status": "UNCHECKED hunter"}


def ingest(done_files, state, prospects, cands, exclude_domains, outbox_idx, in_outbox, today):
    """Process every NEW hunter result. Mutates `state["hunter_seen"]`, `prospects` (list) and `cands` (dict with "sites").
    Returns counts only."""
    seen = set(state.get("hunter_seen", []))
    known_dirs = {c.reg_domain(s.get("domain", "")) for s in cands.get("sites", [])} | \
                 {_host(s.get("submit_url", "")) for s in cands.get("sites", [])}
    have_sites = {c.reg_domain(str(p.get("site", ""))) for p in prospects} | {_host(p.get("page", "")) for p in prospects}
    have_sites |= set(state.get("outreach", {}).keys())
    n_next = max([int(p.get("n", 0) or 0) for p in prospects] + [0]) + 1
    cnt = {"results": 0, "lines": 0, "prospects": 0, "directories": 0, "dropped": 0}
    for path, text in sorted(done_files.items()):
        name = path.rsplit("/", 1)[-1]
        job_id = name[:-3] if name.endswith(".md") else name
        if "-hunter-" not in name or job_id in seen:
            continue
        seen.add(job_id)
        cnt["results"] += 1
        for d in parse_lines(text):
            cnt["lines"] += 1
            row = outreach_row(d, job_id, n_next)
            if row:
                k = row["site"]
                emails = row["emails"]
                if k in have_sites or k in exclude_domains or any(c.reg_domain(e.split("@")[-1]) in exclude_domains for e in emails) or in_outbox(k, emails, outbox_idx):
                    cnt["dropped"] += 1
                    continue
                prospects.append(row)
                have_sites.add(k)
                n_next += 1
                cnt["prospects"] += 1
                continue
            drow = directory_row(d, job_id, today)
            if drow:
                dom = drow["domain"]
                if dom in known_dirs or dom in exclude_domains:
                    cnt["dropped"] += 1
                    continue
                cands.setdefault("sites", []).append(drow)
                known_dirs.add(dom)
                cnt["directories"] += 1
                continue
            cnt["dropped"] += 1
    state["hunter_seen"] = sorted(seen)[-500:]
    return cnt
