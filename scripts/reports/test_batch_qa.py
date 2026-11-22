"""Backlink batch: tests for the code QA findings of 19 Nov 2026 (state before outbox, never twice, replies every run)."""
import json, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="batch_qa_")
os.environ["FIXTURE_DIR"] = os.path.join(TMP, "fx")
os.environ["REPORTS_WORK"] = os.path.join(TMP, "work")
os.environ["DRY_RUN"] = "false"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c
import backlink_batch as bb

DIGEST = """# digest

| date (UTC) | folder | from | subject | status | tag |
|---|---|---|---|---|---|
| 11-18 09:00 | INBOX | Ed <ed.writer@gmail.com> | Re: hello | read |  |
| 11-18 10:00 | INBOX | Someone <other@gmail.com> | newsletter | read |  |
| 11-18 11:00 | INBOX | Desk <desk@sitec.com> | Re: tools | read |  |
| 11-18 12:00 | INBOX | Us <contact@inayaflow.ai> | watchdog | read |  |
"""
REPLIER = """# log

| date | from domain | category | action | summary |
|---|---|---|---|---|
| 2026-11-17 | sited.com | NO_SWAP_OR_PAID | SENT reply | no |
| 2026-11-17 | auto.com | AUTO_ACK | no reply | automated |
"""


def entry(to, date="2026-11-15", **kw):
    e = {"status": "sent", "id": "i", "date": date, "to": to, "subject": "S", "followup_due": "2026-11-22"}
    e.update(kw)
    return e


class Replies(unittest.TestCase):
    def test_every_run_by_domain_and_by_address(self):
        rep = bb.replied_index(REPLIER, DIGEST)
        self.assertNotIn("contact@inayaflow.ai", rep["addresses"])
        st = {"outreach": {
            "sitea.com": entry("ed.writer@gmail.com"),            # answered from the exact (free-mail) address we wrote to
            "siteb.com": entry("bob@gmail.com"),                  # other gmail mail arrived: NOT a reply
            "sitec.com": entry("editor@agency.io"),               # answered from the SITE's own domain, another person
            "sited.com": entry("x@sited.com"),                    # in the replier log
            "auto.com": entry("x@auto.com"),                      # automatic acknowledgement only: not a reply
            "old.com": entry("desk@sitec.com", date="2026-11-19"),  # mail is older than our send: not a reply
            "fu.com": entry("ed.writer@gmail.com", followup_id="f", status="sent"),   # follow-up already out: still marked
        }}
        n = bb.mark_replies(st, rep)       # today is long before any follow-up is due (22 Nov): replies are checked anyway
        got = {k: v["status"] for k, v in st["outreach"].items()}
        self.assertEqual(got, {"sitea.com": "replied", "siteb.com": "sent", "sitec.com": "replied", "sited.com": "replied",
                               "auto.com": "sent", "old.com": "sent", "fu.com": "replied"})
        self.assertEqual(n, 4)
        self.assertEqual(bb.due_followups(st, "2026-11-19"), [])                       # nothing due yet
        self.assertEqual([k for k, _e in bb.due_followups(st, "2026-11-22")], ["auto.com", "old.com", "siteb.com"])   # replied sites never get one

    def test_queued_entries_are_checked_too(self):
        st = {"outreach": {"sitec.com": entry("a@b.io", status="queued")}}
        bb.mark_replies(st, bb.replied_index("", DIGEST))
        self.assertEqual(st["outreach"]["sitec.com"]["status"], "replied")


class Outbox(unittest.TestCase):
    FILES = {
        "outbox/sent/2026-11-19-seo-swap-techpresso-1.json": json.dumps({"id": "x", "to": ["techpresso@dupple.com"], "cc": ["boss@partner.org"]}),
        "outbox/pending/2026-11-19-robot-outreach-affninja.com.json": json.dumps({"id": "y", "to": ["ali@affmaven.com"]}),
        "outbox/failed/2026-11-18-old.json": json.dumps({"id": "z", "to": ["writer@gmail.com"]}),
        "outbox/sent/broken.json": "not json",
    }

    def test_site_in_any_outbox_file_is_skipped(self):
        idx = bb.outbox_index(self.FILES)
        self.assertTrue(bb.in_outbox("affninja.com", ["ali@affninja.io"], idx))          # by file name (site key)
        self.assertTrue(bb.in_outbox("techpresso.co", ["techpresso@dupple.com"], idx))   # by the exact address
        self.assertTrue(bb.in_outbox("dupple.com", [], idx))                             # by recipient domain = site
        self.assertTrue(bb.in_outbox("other.com", ["new@partner.org"], idx))             # cc domain counts
        self.assertTrue(bb.in_outbox("blog.net", ["writer@gmail.com"], idx))             # failed folder counts, exact address
        self.assertFalse(bb.in_outbox("blog.net", ["someone.else@gmail.com"], idx))      # free-mail domain alone is not a match
        self.assertFalse(bb.in_outbox("fresh.com", ["ed@fresh.com"], idx))

    def test_run_outreach_never_queues_a_site_already_in_the_outbox(self):
        fx = os.environ["FIXTURE_DIR"]
        shutil.rmtree(TMP, ignore_errors=True)
        for path, text in self.FILES.items():
            p = os.path.join(fx, "ops-outbox", path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w", encoding="utf-8").write(text)
        work = os.environ["REPORTS_WORK"]
        os.makedirs(os.path.join(work, "ops"), exist_ok=True)
        prospects = [{"n": 1, "site": "AffNinja", "page": "https://affninja.com/p", "match": "/tools", "emails": ["ali@affninja.io"], "pri": "B"},
                     {"n": 2, "site": "Techpresso", "page": "https://academy.techpresso.co/p", "match": "scripts", "emails": ["techpresso@dupple.com"], "pri": "B"},
                     {"n": 3, "site": "Fresh", "page": "https://fresh.com/p", "match": "hashtags", "emails": ["ed@fresh.com"], "pri": "B"}]
        json.dump(prospects, open(os.path.join(work, "ops", "outreach_prospects.json"), "w"))
        fetched = []

        def get(url, timeout=15, limit=0):
            fetched.append(url)
            if url == "https://fresh.com/p":
                return 200, url, "<title>Hashtags</title>" + "Words about hashtags. " * 40 + " ed@fresh.com"
            return 404, url, ""
        ai = lambda s_, u, m=0: '{"topic_match": true, "sponsored_or_paid": false, "title": "How many hashtags", "covers": "It covers hashtag limits."}'
        state = {}
        queued, skipped, _h = bb.run_outreach(work, state, True, get, ai)
        self.assertEqual([q["_key"] for q in queued], ["fresh.com"])
        self.assertEqual(sorted(k for k, w in skipped if w == "already in the outbox"), ["affninja.com", "techpresso.co"])
        self.assertFalse([u for u in fetched if "affninja" in u or "techpresso" in u])     # not even opened
        self.assertEqual(state["outreach"]["fresh.com"]["status"], "queued")               # not "sent" before the hand-over
        self.assertGreaterEqual(state["days"][c.today()], 1)

    def test_reconcile(self):
        idx = bb.outbox_index({"outbox/sent/id-ok.json": "{}", "outbox/pending/fu-ok.json": "{}"})
        st = {"outreach": {"a.com": {"status": "queued", "id": "id-ok"},            # file is there -> sent
                           "b.com": {"status": "queued", "id": "id-lost"},          # push failed last run -> forgotten, tried again
                           "c.com": {"status": "sent", "id": "id-ok", "followup_id": "fu-lost", "followup_date": "2026-11-19"},
                           "d.com": {"status": "sent", "id": "id-ok", "followup_id": "fu-ok"}}}
        bb.reconcile(st, idx)
        self.assertEqual(st["outreach"]["a.com"]["status"], "sent")
        self.assertNotIn("b.com", st["outreach"])
        self.assertNotIn("followup_id", st["outreach"]["c.com"])
        self.assertEqual(st["outreach"]["d.com"]["followup_id"], "fu-ok")


class Order(unittest.TestCase):
    def test_state_is_saved_before_the_outbox_push_and_a_failed_push_cannot_double_send(self):
        shutil.rmtree(TMP, ignore_errors=True)
        work = os.environ["REPORTS_WORK"]
        os.makedirs(os.path.join(work, "ops"), exist_ok=True)
        json.dump({"outreach_send": True}, open(os.path.join(work, "ops", "robots_control.json"), "w"))
        events, saved_states = [], []
        q = {"id": "2026-11-19-robot-outreach-fresh.com", "to": ["ed@fresh.com"], "cc": [], "subject": "S", "body": "B", "_key": "fresh.com", "_kind": "first",
             "_title": "T", "_page": "https://fresh.com/p", "_match": "hashtags"}

        def fake_outreach(work_, state, send, get=None, ai=None):
            state.setdefault("outreach", {})["fresh.com"] = {"status": "queued", "id": q["id"], "date": c.today(), "to": "ed@fresh.com"}
            return [q], [], []

        def fake_push(msg, branch=c.REPORT_BRANCH, work=None):
            events.append("save:" + msg)
            saved_states.append(json.load(open(os.path.join(os.environ["REPORTS_WORK"], bb.STATE)))["outreach"]["fresh.com"]["status"])
            return True

        def outbox_fails(queued):
            events.append("outbox")
            raise RuntimeError("push failed")
        real = (bb.run_outreach, bb.c.commit_push, bb.write_outbox, bb.list_builder, bb.CANDS, bb.c.DRY)
        cands = os.path.join(TMP, "cands.json")
        json.dump({"sites": []}, open(cands, "w"))
        bb.run_outreach, bb.c.commit_push, bb.write_outbox, bb.list_builder, bb.CANDS, bb.c.DRY = fake_outreach, fake_push, outbox_fails, (lambda d, k: []), cands, False
        try:
            with self.assertRaises(SystemExit):
                bb.main()                                   # the outbox push fails
            self.assertEqual(events[0], "save:ops: backlink batch")
            self.assertEqual(events[1], "outbox")           # only AFTER our memory was saved
            self.assertEqual(saved_states[0], "queued")
            # next run: the file is not in the outbox, so the entry is forgotten and may be tried again (exactly once more)
            st = json.load(open(os.path.join(work, bb.STATE)))
            bb.reconcile(st, bb.outbox_index({}))
            self.assertNotIn("fresh.com", st["outreach"])
            # and when the push DID work but the run died right after it: the next run sees the file and marks it sent
            st2 = {"outreach": {"fresh.com": {"status": "queued", "id": q["id"]}}}
            idx = bb.outbox_index({"outbox/pending/%s.json" % q["id"]: json.dumps({"to": ["ed@fresh.com"]})})
            bb.reconcile(st2, idx)
            self.assertEqual(st2["outreach"]["fresh.com"]["status"], "sent")
            self.assertTrue(bb.in_outbox("fresh.com", ["ed@fresh.com"], idx))    # so it can never be queued a second time
            # happy path: order is save -> outbox -> save
            events.clear()
            saved_states.clear()
            bb.write_outbox = lambda queued: events.append("outbox") or 1
            os.remove(os.path.join(work, bb.STATE))
            bb.main()
            self.assertEqual([e.split(":")[0] for e in events], ["save", "outbox", "save"])
            self.assertEqual(saved_states, ["queued", "sent"])
        finally:
            bb.run_outreach, bb.c.commit_push, bb.write_outbox, bb.list_builder, bb.CANDS, bb.c.DRY = real


class Caps(unittest.TestCase):
    """23 Nov 2026: 15 a day in total across hourly runs (2 per run); the outbox count wins over a lower state count; hunter rows end to end."""
    AI = staticmethod(lambda s_, u, m=0: '{"topic_match": true, "sponsored_or_paid": false, "title": "How many hashtags", "covers": "It covers hashtag limits."}')

    def setup_work(self, prospects, outbox_names=()):
        shutil.rmtree(TMP, ignore_errors=True)
        fx = os.environ["FIXTURE_DIR"]
        for n in outbox_names:
            p = os.path.join(fx, "ops-outbox", "outbox", "sent", n)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": n, "to": ["old@elsewhere-%s.org" % n[-8:-5]]}))
        work = os.environ["REPORTS_WORK"]
        os.makedirs(os.path.join(work, "ops"), exist_ok=True)
        with open(os.path.join(work, "ops", "outreach_prospects.json"), "w") as f:
            json.dump(prospects, f)
        return work

    @staticmethod
    def get(url, timeout=15, limit=0):
        host = url.split("/")[2]
        return 200, url, "<title>Hashtags</title>" + "Words about hashtags. " * 40 + " ed@" + host.replace("www.", "")

    def many(self, n, match="money"):
        return [{"n": i, "site": "S%d" % i, "page": "https://s%d.com/p" % i, "match": match, "emails": ["ed@s%d.com" % i], "pri": "B"} for i in range(n)]

    def test_fifteen_a_day_across_runs(self):
        work = self.setup_work(self.many(40))
        state, total = {}, 0
        for _run in range(12):                       # twelve hourly runs
            queued, _s, _h = bb.run_outreach(work, state, True, self.get, self.AI)
            self.assertLessEqual(len(queued), bb.MAX_SENDS_RUN)
            total += len(queued)
        self.assertEqual(total, 15)                  # never more than the daily ceiling
        self.assertEqual(state["days"][c.today()], 15)

    def test_outbox_count_wins_over_a_lower_state_count(self):
        names = ["%s-robot-outreach-x%02d.json" % (c.today(), i) for i in range(14)]
        work = self.setup_work(self.many(10), names)
        state = {"days": {c.today(): 3}}             # state says 3, the outbox files say 14
        queued, _s, _h = bb.run_outreach(work, state, True, self.get, self.AI)
        self.assertEqual(len(queued), 1)

    def test_guard_rejects_foreign_links_and_first_person(self):
        end = " The InayaFlow team" + chr(10) + "contact@inayaflow.ai"
        self.assertIsNone(bb.guard("Hello", "We built a tool: https://inayaflow.ai/tools/x." + end))
        self.assertEqual(bb.guard("Hello", "We see https://evil.example/x for details." + end), "foreign link")
        self.assertEqual(bb.guard("Hello", "We are at https://inayaflow.ai.evil.com/x." + end), "foreign link")
        self.assertEqual(bb.guard("Hello", "We say I think this fits." + end), "not company voice (I / me / my)")
        self.assertEqual(bb.guard("Hello", "We like my article." + end), "not company voice (I / me / my)")

    def test_hunter_rows_go_through_eligible_prospects_and_run_outreach(self):
        import hunter_ingest as hi
        row = hi.outreach_row({"url": "https://blog.fresh.com/hashtags", "type": "blog_article", "page": "hashtag-strategy", "name": "Fresh",
                               "topic": "tiktok hashtags", "email": "ed@fresh.com", "email_source_url": "https://fresh.com/contact"}, "id-hunter-x", 1)
        self.assertEqual(row["page_date"], "")       # hunter rows have no page date: nothing may depend on it
        ok, human = bb.eligible_prospects([row], {}, set())
        self.assertEqual((len(ok), len(human)), (1, 0))
        work = self.setup_work([row])
        page_get = lambda url, timeout=15, limit=0: (200, url, "<title>Hashtags</title>" + "Words about hashtags. " * 40 + " ed@fresh.com")
        queued, skipped, _h = bb.run_outreach(work, {}, True, page_get, self.AI)
        self.assertEqual([q["_key"] for q in queued], ["fresh.com"])
        self.assertIn(queued[0]["_match"], ("hashtags", "hashtag-strategy", "hashtag-counter"))   # same topic, any of the three hashtag pages
        self.assertIn(bb.MATCH[queued[0]["_match"]][0], queued[0]["body"])


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
