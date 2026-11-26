import io, os, sys, unittest, contextlib, datetime
sys.path.insert(0, os.path.dirname(__file__))
import router as R
import unittest.mock

DIRS = {"agenthunter.io", "aidirs.best", "indiehunt.io", "earlyhunt.com", "buildlist.io", "launchingnext.com",
        "tinylaunch.com", "sideprojectors.com"}
OUTR = {"lilachbullock.com"}
SENT = {"<sent1@inayaflow.ai>"}


def boom(_):
    raise AssertionError("Claude must not be called")


def run(frm, subj, body="", irt="", refs="", unsub=False, ai=boom):
    return R.classify(frm, subj, body, irt, refs, DIRS, OUTR, SENT, unsub, ai=ai)


class T(unittest.TestCase):
    def test_verify(self):
        for frm, s in [("noreply@agenthunter.io", "Confirm your email"), ("support@aidirs.best", "Confirm your email"),
                       ("hi@indiehunt.io", "Your IndieHunt sign-in link"), ("hi@earlyhunt.com", "Sign in to EarlyHunt"),
                       ("hello@buildlist.io", "Confirm your Buildlist subscription")]:
            self.assertEqual(run(frm, s)[0], "DIRECTORY_VERIFY", s)

    def test_status(self):
        for frm, s in [("approvals@launchingnext.com", "InayaFlow submission, plus get published in 1 day"),
                       ("chris@updates.tinylaunch.com", "Launch Scheduled!"),
                       ("hello@sideprojectors.com", "Your project has been received!")]:
            self.assertEqual(run(frm, s)[0], "DIRECTORY_STATUS", s)

    def test_outreach_by_domain_and_thread(self):
        self.assertEqual(run("lilach@lilachbullock.com", "Re: Your TikTok pay per 1000 views article...")[0], "OUTREACH_REPLY")
        self.assertEqual(run("x@unknown.example", "Re: hi", irt="<sent1@inayaflow.ai>")[0], "OUTREACH_REPLY")

    def test_ai_used_for_rest(self):
        ai = lambda u: '{"bucket":"JOURNALIST_REQUEST","reason":"SOS digest","action":"review"}'
        self.assertEqual(run("peter@sourceofsources.com", "[SOS] Tuesday Afternoon Media Queries", ai=ai)[0], "JOURNALIST_REQUEST")
        ai = lambda u: '{"bucket":"PLATFORM_ACCOUNT","reason":"google notice","action":"none"}'
        self.assertEqual(run("noreply-accounts@google.com", "You shared some Google Account data with X", ai=ai)[0], "PLATFORM_ACCOUNT")
        ai = lambda u: '{"bucket":"NEWSLETTER_PROMO","reason":"newsletter","action":"skip"}'
        self.assertEqual(run("hello@deeperlearning.producthunt.com", "This week in AI", unsub=True, ai=ai)[0], "NEWSLETTER_PROMO")

    def test_ai_failure_never_drops(self):
        self.assertEqual(run("a@b.example", "hello", ai=boom)[0], "OTHER_NEEDS_IMRAN")
        self.assertEqual(run("a@b.example", "hello", ai=lambda u: '{"bucket":"NOPE"}')[0], "OTHER_NEEDS_IMRAN")
        self.assertEqual(run("a@b.example", "hello", ai=lambda u: "not json")[0], "OTHER_NEEDS_IMRAN")

    def test_directory_newsletter_not_rule(self):
        d = R.classify_det("hello@buildlist.io", "Weekly picks", "great tools", "", "", DIRS, OUTR, SENT, True)
        self.assertIsNone(d)

    def test_audit(self):
        now = datetime.datetime(2026, 11, 18, 12, tzinfo=datetime.timezone.utc)
        h = lambda n: now - datetime.timedelta(hours=n)
        items = [{"date": h(5), "frm": "a@x", "subj": "old", "bucket": "OUTREACH_REPLY", "flags": []},
                 {"date": h(5), "frm": "b@x", "subj": "done", "bucket": "OUTREACH_REPLY", "flags": ["IFBotHandled"]},
                 {"date": h(1), "frm": "c@x", "subj": "fresh", "bucket": "DIRECTORY_VERIFY", "flags": []},
                 {"date": h(30), "frm": "d@x", "subj": "ancient", "bucket": "OTHER_NEEDS_IMRAN", "flags": []},
                 {"date": h(3), "frm": "e@x", "subj": "promo", "bucket": "NEWSLETTER_PROMO", "flags": []}]
        out = R.build_audit(items, now)
        top = out.split("## Counts")[0]
        self.assertIn("old", top)
        for s in ("done", "fresh", "ancient"):
            self.assertNotIn(s, top)
        self.assertIn("completed in 24 h: 1", out)
        self.assertNotIn("promo", out)

    def test_audit_done_sources_and_legacy(self):
        now = datetime.datetime(2026, 11, 18, 12, tzinfo=datetime.timezone.utc)
        h = lambda n: now - datetime.timedelta(hours=n)
        gl = now - datetime.timedelta(hours=9)
        items = [{"date": h(5), "frm": "A <a@x.org>", "subj": "thread", "bucket": "OUTREACH_REPLY", "flags": [], "mid": "<m1@x>"},
                 {"date": h(6), "frm": "B <b@acked.org>", "subj": "Re: Pay me", "bucket": "OUTREACH_REPLY", "flags": []},
                 {"date": h(12), "frm": "C <c@x.org>", "subj": "old", "bucket": "OUTREACH_REPLY", "flags": []},
                 {"date": h(4), "frm": "D <d@x.org>", "subj": "missed", "bucket": "OUTREACH_REPLY", "flags": []}]
        acks = [("acked.org :: pay me", h(6).strftime("%m-%d %H:%M"))]
        out = R.build_audit(items, now, {"<m1@x>"}, acks, gl)
        top = out.split("## LEGACY")[0]
        self.assertIn("missed", top)
        for s in ("thread", "Pay me", "old"):
            self.assertNotIn(s, top)
        self.assertIn("old", out.split("## LEGACY")[1].split("## Counts")[0])

    def test_dir_extra_domain(self):
        d = R.classify_det("hello@sideprojectors.com", "Your project has been received!", "", "", "",
                           DIRS | {"sideprojectors.com"}, OUTR | {"sideprojectors.com"}, SENT)
        self.assertEqual(d[0], "DIRECTORY_STATUS")

    def test_own_mail_skipped(self):
        d = R.classify_det("InayaFlow Team <contact@inayaflow.ai>", "watchdog all clear", "", "", "", DIRS, OUTR, SENT)
        self.assertEqual(d[0], "NEWSLETTER_PROMO")

    def test_status_words(self):
        self.assertEqual(R.status_word("InayaFlow submission, plus get published in 1 day"), "upsell")
        self.assertEqual(R.status_word("Launch Scheduled!"), "scheduled")
        self.assertEqual(R.status_word("Your project has been received!"), "received")
        self.assertEqual(R.status_word("Your listing was approved"), "approved")
        self.assertEqual(R.status_word("Submission rejected"), "rejected")

    def test_sections(self):
        now = datetime.datetime(2026, 11, 18, 12, tzinfo=datetime.timezone.utc)
        h = lambda n: now - datetime.timedelta(hours=n)
        mk_ = lambda n, b, subj: {"date": h(n), "frm": "x <x@y.org>", "subj": subj, "bucket": b, "flags": []}
        items = [mk_(5, "DIRECTORY_VERIFY", "verifyme"), mk_(5, "CREATOR_OR_PARTNER", "creatorx"),
                 mk_(5, "JOURNALIST_REQUEST", "sosdigest"), mk_(5, "DIRECTORY_STATUS", "statusmail")]
        out = R.build_audit(items, now, frozenset(), (), now - datetime.timedelta(hours=9))
        p = {k: out.split("## " + k)[1].split("\n## ")[0] for k in ("NOT HANDLED", "FOR IMRAN", "DIRECTOR TODO")}
        self.assertIn("verifyme", p["NOT HANDLED"]); self.assertNotIn("creatorx", p["NOT HANDLED"])
        self.assertIn("creatorx", p["FOR IMRAN"])
        self.assertIn("sosdigest", p["DIRECTOR TODO"])
        self.assertNotIn("statusmail", out)

    def test_listing_log(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "ls.md")
        it = [{"date": datetime.datetime(2026, 11, 18, 1, tzinfo=datetime.timezone.utc), "frm": "a <a@tinylaunch.com>", "subj": "Launch Scheduled!"}]
        self.assertEqual(R.log_listing_status(it, p), 1)
        self.assertEqual(R.log_listing_status(it, p), 0)
        self.assertIn("scheduled", open(p).read())

    def test_stdout_has_no_sender_or_subject(self):
        buf = io.StringIO()
        saved = os.environ.pop("CONTACT_EMAIL_PASSWORD", None)
        with contextlib.redirect_stdout(buf):
            R.main()
        if saved:
            os.environ["CONTACT_EMAIL_PASSWORD"] = saved
        out = buf.getvalue()
        for s in ["agenthunter", "@", "Confirm your email"]:
            self.assertNotIn(s, out)


class DailyAiCap(unittest.TestCase):
    def setUp(self):
        self._ok = os.environ.get("AI_COUNTER_OK")
        os.environ["AI_COUNTER_OK"] = "true"

    def tearDown(self):
        if self._ok is None:
            os.environ.pop("AI_COUNTER_OK", None)
        else:
            os.environ["AI_COUNTER_OK"] = self._ok

    def test_fails_closed_when_the_counter_was_not_restored(self):
        import tempfile
        os.environ.pop("AI_COUNTER_OK", None)
        p = os.path.join(tempfile.mkdtemp(), "ops", "ai_calls_router.json")
        self.assertFalse(R.ai_take_call(p, "2026-11-23"))
        self.assertFalse(os.path.exists(p))

    def test_workflow_serialises_runs_and_retries_the_push(self):
        w = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".github", "workflows", "mail-router.yml"), encoding="utf8").read()
        self.assertIn("group: mail-router", w)
        self.assertIn("cancel-in-progress: false", w)
        self.assertIn("for i in 1 2 3 4; do", w)                         # the push is retried (with a rebase); if it still fails the job goes red
        self.assertIn("git pull -q --rebase origin ops-mail-router", w)
        self.assertIn('[ "$ok" = "1" ] ||', w)

    def test_workflow_restores_sets_the_flag_and_pushes_the_counter(self):
        w = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".github", "workflows", "mail-router.yml"), encoding="utf8").read()
        restore = w.split('echo "log branch restored"', 1)[1].split("else", 1)[0]
        self.assertIn("AI_COUNTER_OK=true", restore)               # only on the branch that really restored the log branch
        self.assertIn("ROUTER_OUT_DIR: /tmp/push/ops", w)          # the counter lives in the restored folder
        self.assertIn("ops/ai_calls_router.json", w)

    def test_daily_cap_counts_and_resets(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "ops", "ai_calls_router.json")
        self.assertEqual(R.AI_CALLS_PER_DAY, 150)
        for _ in range(R.AI_CALLS_PER_DAY):
            self.assertTrue(R.ai_take_call(p, "2026-11-23"))
        self.assertFalse(R.ai_take_call(p, "2026-11-23"))
        self.assertEqual(R.ai_calls_today(p, "2026-11-23"), 150)
        self.assertTrue(R.ai_take_call(p, "2026-11-24"))

    def test_main_loop_defers_when_the_daily_cap_is_used(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "router.py"), encoding="utf8").read()
        self.assertIn("if ai_used >= MAX_AI or not ai_take_call():", src)          # the check sits BEFORE the model call
        self.assertLess(src.index("not ai_take_call()"), src.index("bucket, reason, _how = classify("))

    def test_workflow_saves_the_counter(self):
        w = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".github", "workflows", "mail-router.yml"), encoding="utf8").read()
        self.assertIn("ops/ai_calls_router.json", w)


class HaikuRoom26Nov(unittest.TestCase):
    def test_room_effort_and_one_retry_that_takes_its_own_counted_call(self):
        sent, took = [], []
        replies = [{"content": [], "stop_reason": "max_tokens", "usage": {"input_tokens": 500, "output_tokens": 4000}},
                   {"content": [{"type": "text", "text": '{"bucket": "OTHER_NEEDS_IMRAN"}'}], "stop_reason": "end_turn", "usage": {}}]
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), contextlib.redirect_stdout(io.StringIO()) as out:
            t = R.ai_call("u", post=lambda b, k: sent.append(b) or replies[len(sent) - 1], take=lambda: took.append(1) or True)
        self.assertIn("OTHER_NEEDS_IMRAN", t)
        self.assertEqual([b["max_tokens"] for b in sent], [4000, 8000])
        self.assertEqual(sent[0]["output_config"], {"effort": "low"})
        self.assertEqual(len(took), 1)                    # the retry took one call from the daily counter
        self.assertIn("stop=max_tokens", out.getvalue())

    def test_no_retry_when_the_daily_cap_is_used_up(self):
        sent = []
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), contextlib.redirect_stdout(io.StringIO()):
            t = R.ai_call("u", post=lambda b, k: sent.append(b) or {"content": [], "stop_reason": "max_tokens"}, take=lambda: False)
        self.assertEqual((t, len(sent)), ("", 1))


if __name__ == "__main__":
    unittest.main()
