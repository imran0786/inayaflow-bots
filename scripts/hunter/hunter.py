#!/usr/bin/env python3
"""PROSPECT HUNTER (23 Nov 2026): queues ONE research job for the cloud job runner every 3 hours, 24/7.

Imran: "One agent helper should 24/7 be building the links, the backlinks, the list of directories, the list of backlinks,
platforms that can give us backlinks."

What it does: writes jobs/pending/<id>.md on the PRIVATE branch imran0786/genie ops-jobs (same brief format as
scripts/submit-job.mjs in the project folder). The job runner (job-runner.yml, every 10 minutes) runs it on Claude Sonnet with web
search and writes jobs/done/<id>.md: strict JSON lines of VERIFIED prospects. The next backlink-batch run reads new results
(scripts/reports/hunter_ingest.py) and adds them to the prospect list and the directory candidates.

Rotation: 8 slots a day (every 3 hours) walk through the 11 target pages and the 5 prospect types so every page gets every type.
It sends nothing, signs up nowhere, spends only through the job runner's own caps (max_cost per job, daily cap).
PUBLIC repo: prints the job id and counts only.
"""
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports"))
import common as c

BRANCH = "ops-jobs"
WORK = os.environ.get("HUNTER_WORK", "/tmp/ops_jobs_hunter")
MAX_COST = float(os.environ.get("HUNTER_MAX_COST", "0.60"))       # job-runner ceiling is $1; Director budget 23 Nov: 8 jobs a day at $0.60
MAX_SEARCHES = int(os.environ.get("HUNTER_MAX_SEARCHES", "10"))   # job-runner ceiling is 10
MAX_WAITING = int(os.environ.get("HUNTER_MAX_WAITING", "3"))      # never pile up more hunter jobs than the runner can clear

PAGES = [  # (slug used in the job id and the result's "page" field, URL, what it is, topic words the prospect must be about)
    ("homepage", "https://inayaflow.ai/", "InayaFlow, an AI TikTok agent for creators: each morning it delivers ready-to-film ideas, hooks, scripts, trending sounds and posting times",
     "TikTok growth, TikTok analytics or AI tools for TikTok creators"),
    ("hashtag-strategy", "https://inayaflow.ai/blog/tiktok-hashtag-strategy", "our guide: how many hashtags on TikTok (the limit is 5 per post), length, order and strategy",
     "how many hashtags to use on TikTok / TikTok hashtag strategy"),
    ("follower-checker", "https://inayaflow.ai/tools/tiktok-follower-count-checker", "our free TikTok follower count checker (type any @username; no login)",
     "checking a TikTok account's followers, following, likes and videos / TikTok stats tools"),
    ("money-calculator", "https://inayaflow.ai/tools/tiktok-money-calculator", "our free TikTok money calculator (estimates Creator Rewards earnings from views; no sign-up)",
     "how much TikTok pays creators / TikTok earnings / TikTok Creator Rewards"),
    ("shadowban-checker", "https://inayaflow.ai/tools/tiktok-shadowban-checker", "our free TikTok shadowban self-check (compares recent views with usual views; not a ban verdict)",
     "TikTok shadowbans, sudden drops in views, how to appeal"),
    ("hashtag-generator", "https://inayaflow.ai/tools/tiktok-hashtag-generator", "our free TikTok hashtag generator (turns a topic into up to 5 hashtags; no AI, no account)",
     "which hashtags to use on TikTok / TikTok hashtag ideas"),
    ("hashtag-counter", "https://inayaflow.ai/tools/tiktok-hashtag-counter", "our free TikTok hashtag counter and caption length checker (counts hashtags and characters)",
     "TikTok hashtag limit and caption length"),
    ("engagement-rate", "https://inayaflow.ai/tools/tiktok-engagement-rate-calculator", "our free TikTok engagement rate calculator with 2026 benchmarks",
     "TikTok engagement rate (how to calculate it, benchmarks)"),
    ("best-time", "https://inayaflow.ai/tools/best-time-to-post-on-tiktok-today", "our free tool that shows the best time to post on TikTok today in the visitor's time zone",
     "the best time to post on TikTok"),
    ("coins-to-usd", "https://inayaflow.ai/tools/tiktok-coins-to-usd", "our free TikTok coins to USD calculator with LIVE gift values",
     "TikTok coins, gifts and their value in dollars"),
    ("username-generator", "https://inayaflow.ai/tools/tiktok-username-generator", "our free TikTok username generator (20 ideas checked against TikTok's rules)",
     "choosing a TikTok username"),
]
TYPES = [  # (type value in the result, what to find)
    ("blog_article", "BLOG ARTICLES or posts on EXACTLY the page's topic, published or updated in 2025-2026, by a site that could plausibly link to a free tool or guide. "
                     "Needs an e-mail address PRINTED on that site's own page (article, /contact, /about, /write-for-us). type = blog_article."),
    ("directory", "FREE DIRECTORIES, launch sites and tool lists where a free listing of our page/tool is possible (a submit form that needs no payment). type = directory or launch_site."),
    ("platform", "WEB 2.0 / PLATFORM sites where we can publish a profile, article or post for free and the link we add is FOLLOWED or at least a real link "
                 "(rel read in the page code of a LIVE example). type = platform."),
    ("resource_page", "RESOURCE PAGES / 'best tools' / 'useful links' pages on TikTok or social media topics that list outside tools, with an e-mail or contact printed on the site's own page. type = resource_page."),
    ("journalist", "JOURNALIST / EXPERT-QUOTE requests (Qwoted, Source of Sources, Featured, HARO-style) open now where a TikTok data point from this page fits. "
                   "Give the request URL and deadline. type = journalist."),
]


def slot(now=None):
    """(page, type, slot number): 8 slots a day (every 3 hours); every page meets every type over 55 slots."""
    now = now or c.now()
    n = now.timetuple().tm_yday * 8 + now.hour // 3
    return PAGES[n % len(PAGES)], TYPES[(n // len(PAGES)) % len(TYPES)], n


def job_id(now, page, typ):
    return "%s-hunter-%s-%s" % (now.strftime("%Y-%m-%d-%H%M"), page[0], typ[0].replace("_", "-"))


def build_brief(now, page, typ):
    pid = job_id(now, page, typ)
    slug, url, what, topic = page
    tkey, tfind = typ
    job = """PROSPECT HUNT (research only: you send nothing, sign up nowhere, write nothing except the one result).

OUR PAGE: %s
It is %s.
TOPIC a prospect must be about: %s.

FIND: %s

HOW: use web search and fetch. Open each candidate page yourself; never list something you did not open. Work until you have up to 12 VERIFIED prospects or the search budget is used.
Skip: sites already linking to inayaflow.ai, spam / link-farm / casino / adult sites, pages that are sponsored or paid-only, anything needing a payment, a phone number or a CAPTCHA to contact or list.
Topic match is HARD: a prospect must be about the topic above, not about TikTok in general.
Never invent an e-mail address. Use an e-mail ONLY if it is printed on that site's OWN page (put that page's URL in email_source_url; the address must be on the SAME site). No address printed = leave email empty.
For links: open a live example page on the site and read the link's rel in the page code. "followed" = no nofollow/ugc/sponsored. Put what you saw in rel_evidence (one short sentence). Unknown = "unknown".

RESULT: your single result must contain, after a first line "HUNT %s DONE", ONLY JSON lines (one object per line, no markdown fence, no commentary between them), then a last line "COUNT n". Each line:
{"url": "<prospect page or submit page>", "type": "%s", "page": "%s", "name": "<site name>", "topic": "<what the page is about, 8 words max>", "email": "<printed address or empty>", "email_source_url": "<url where it is printed or empty>", "rel": "followed|nofollow|unknown", "rel_evidence": "<what you saw>", "free": "yes|no|unknown", "note": "<15 words max>"}
The "page" value must be exactly: %s. Use the type values: blog_article, directory, launch_site, platform, resource_page, journalist.
If you find nothing verifiable, write "HUNT %s DONE" and "COUNT 0" - never pad the list.
Company voice "we" in any note; true claims only; no Arabic.""" % (url, what, topic, tfind, pid, tkey, slug, slug, pid)
    allowed = "read, write_result, web"
    return "\n".join(["---", "id: " + pid, "role: research", "model: sonnet", "max_cost: %.2f" % MAX_COST, "allowed: " + allowed,
                      "max_searches: %d" % MAX_SEARCHES, "output: " + pid, "submitted: " + now.isoformat(), "---", "",
                      "## Job", "", job, "", "## Approvals (word for word; nothing else is approved)", "",
                      "Imran, 22 Nov 2026: \"Spend 14, maybe $15 a day ... One agent helper should 24/7 be building the links, the backlinks, the list of directories, the list of backlinks, platforms that can give us backlinks 24/7.\"", ""])


def waiting(work):
    d = os.path.join(work, "jobs", "pending")
    try:
        return sum(1 for n in os.listdir(d) if "-hunter-" in n)
    except OSError:
        return 0


def main():
    now = c.now()
    page, typ, n = slot(now)
    pid = job_id(now, page, typ)
    if c.DRY:
        print("hunter: dry run, would queue %s" % pid)
        return 0
    work = c.clone(BRANCH, WORK)
    if waiting(work) >= MAX_WAITING:
        print("hunter: %d hunter job(s) still waiting, nothing queued" % waiting(work))
        return 0
    for d in ("pending", "briefs", "done", "failed"):
        if os.path.exists(os.path.join(work, "jobs", d, pid + ".md")):
            print("hunter: slot already queued today (%s)" % d)
            return 0
    os.makedirs(os.path.join(work, "jobs", "pending"), exist_ok=True)
    with open(os.path.join(work, "jobs", "pending", pid + ".md"), "w", encoding="utf-8") as f:
        f.write(build_brief(now, page, typ))
    pushed = c.commit_push("job: %s (research)" % pid, BRANCH, work)
    print("hunter: queued %s pushed=%s page=%s type=%s" % (pid, pushed, page[0], typ[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
