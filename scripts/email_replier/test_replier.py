import unittest, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from email.message import EmailMessage
import replier as r


def mk(frm="Jo <jo@site.com>", subj="Re: hello", irt="<a@inayaflow.ai>", refs="<x@y> <a@inayaflow.ai>",
       mid="<m1@site.com>", **h):
    m = EmailMessage()
    m["From"] = frm
    m["Subject"] = subj
    m["Message-ID"] = mid
    if irt:
        m["In-Reply-To"] = irt
    if refs:
        m["References"] = refs
    for k, v in h.items():
        m[k.replace("_", "-")] = v
    m.set_content("hi")
    return m


class T(unittest.TestCase):
    def test_parse_ok(self):
        c, s, d = r.parse_llm_json('x {"category":"yes_swap","summary":"ok","draft":"Hi"} y')
        self.assertEqual((c, d), ("YES_SWAP", "Hi"))

    def test_parse_bad(self):
        self.assertEqual(r.parse_llm_json("nope")[0], "UNCLEAR")
        self.assertEqual(r.parse_llm_json('{"category":"WAT"}')[0], "UNCLEAR")

    def test_no_draft_for_noreply_cats(self):
        self.assertEqual(r.parse_llm_json('{"category":"AUTO_ACK","draft":"hi"}')[2], "")

    def test_guardrails(self):
        for bad in ["It costs $5", "We can pay you", "our price is", "send an invoice",
                    "10% discount", "see https://evil.com/x", "", "word " * 200]:
            self.assertIsNotNone(r.check_guardrails(bad), bad)
        self.assertIsNone(r.check_guardrails("Happy to swap links. The InayaFlow team"))
        self.assertIsNone(r.check_guardrails("see https://inayaflow.ai/tools"))

    def test_thread_match(self):
        self.assertTrue(r.matches_our_outreach(mk(), ["<a@inayaflow.ai>"]))
        self.assertFalse(r.matches_our_outreach(mk(), ["<zzz@inayaflow.ai>"]))
        self.assertFalse(r.matches_our_outreach(mk(irt=None, refs=None), ["<a@inayaflow.ai>"]))

    def test_gmail_origin_match(self):
        m = mk(irt="<CAabc@mail.gmail.com>", refs="<CAabc@mail.gmail.com>")
        self.assertTrue(r.matches_our_outreach(m, [], "Hi, InayaFlow here"))
        self.assertFalse(r.matches_our_outreach(m, [], "unrelated quote"))
        self.assertFalse(r.matches_our_outreach(mk(irt="<z@other.com>", refs="<z@other.com>"), [], "InayaFlow"))

    def test_earlier_swap_from_quote(self):
        m = mk(refs="<a@x> <b@mail.gmail.com>")
        self.assertTrue(r.earlier_swap_offer_in_thread(m, {}, "we could swap links"))
        self.assertFalse(r.earlier_swap_offer_in_thread(mk(refs="<a@x>"), {}, "we could swap links"))

    def test_domain_match(self):
        m = mk(frm="Lil <lil@lilachbullock.com>", irt="<q@weird>", refs="<q@weird>")
        self.assertTrue(r.matches_our_outreach(m, [], "", {"lilachbullock.com"}))
        self.assertFalse(r.matches_our_outreach(m, [], "", {"other.com"}))
        sub = mk(frm="x@mail.klap.app", irt=None, refs=None, subj="Re: hi")
        self.assertTrue(r.matches_our_outreach(sub, [], "", {"klap.app"}))
        fresh = mk(frm="x@klap.app", irt=None, refs=None, subj="Hello there")
        self.assertFalse(r.matches_our_outreach(fresh, [], "", {"klap.app"}))

    def test_answered_by_hand(self):
        m = mk(refs="<a@x> <b@y> <c@mail.gmail.com>")
        self.assertTrue(r.answered_by_hand(m, {}, "InayaFlow swap"))
        self.assertFalse(r.answered_by_hand(mk(refs="<c@mail.gmail.com>"), {}, "InayaFlow"))
        sent = {"<a@inayaflow.ai>": {"text": "x", "is_reply": True}}
        self.assertTrue(r.answered_by_hand(mk(), sent, ""))

    def test_go_live(self):
        self.assertEqual(r.parse_go_live(""), 0.0)
        self.assertGreater(r.parse_go_live("2026-11-18T12:00:00"), 0)

    def test_reply_headers(self):
        o = r.build_reply(mk(subj="Hello"), "Thanks!")
        self.assertEqual(o["To"], "jo@site.com")
        self.assertEqual(o["Subject"], "Re: Hello")
        self.assertEqual(o["In-Reply-To"], "<m1@site.com>")
        self.assertTrue(o["References"].endswith("<m1@site.com>"))
        self.assertIn("The InayaFlow team", o.get_content())

    def test_no_double_re(self):
        self.assertEqual(r.build_reply(mk(subj="Re: x"), "a")["Subject"], "Re: x")

    def test_automated(self):
        self.assertTrue(r.is_automated(mk(frm="noreply@x.com")))
        self.assertTrue(r.is_automated(mk(Auto_Submitted="auto-replied")))
        self.assertFalse(r.is_automated(mk()))

    def test_strip_quotes(self):
        t = "Sure!\n> old\nOn Mon, 3 Nov 2026 at 10:00 Bob <b@x.com> wrote:\nmore"
        self.assertEqual(r.strip_quotes(t), "Sure!")

    def test_earlier_swap(self):
        sent = {"<a@inayaflow.ai>": {"text": "we can swap links", "is_reply": True}}
        self.assertTrue(r.earlier_swap_offer_in_thread(mk(), sent))
        sent["<a@inayaflow.ai>"]["is_reply"] = False
        self.assertFalse(r.earlier_swap_offer_in_thread(mk(), sent))


class FakeImap:
    """Minimal IMAP stand-in holding one inbound reply from a fake third party."""
    def __init__(self, raw):
        self.raw = raw
    def login(self, u, p): pass
    def list(self): return "OK", [b'(\\Sent) "/" "Sent"']
    def select(self, *a, **k): return "OK", [b"1"]
    def append(self, *a, **k): return "OK", []
    def logout(self): pass
    def uid(self, cmd, *a):
        if cmd == "SEARCH":
            if "UNANSWERED" in a:
                return "OK", [b"1"]
            return "OK", [b""]
        if cmd == "FETCH":
            return "OK", [(b"1 (BODY[] {1})", self.raw)]
        return "OK", []


class StdoutAudit(unittest.TestCase):
    def test_no_third_party_data_in_stdout(self):
        import io, tempfile, contextlib, unittest.mock as mock
        secret_addr, secret_subj, secret_body = "zed.qux@thirdparty-example.org", "Quibble-Subject-77", "Wibble-Body-Text-99"
        m = mk(frm="Zed Qux <%s>" % secret_addr, subj="Re: " + secret_subj,
               irt="<CAabc@mail.gmail.com>", refs="<CAabc@mail.gmail.com>", Date="Wed, 18 Nov 2026 12:00:00 +0000")
        m.set_content(secret_body + "\n> InayaFlow swap")
        m.replace_header("Subject", "Re: " + secret_subj)
        raw = m.as_bytes()
        out = io.StringIO()
        sent = []
        class FakeSmtp:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def login(self, *a): pass
            def send_message(self, msg): sent.append(msg)
        draft = '{"category":"YES_SWAP","summary":"Zed Qux ' + secret_subj + '","draft":"Thanks, we would love to swap. Which page?"}'
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                with mock.patch.dict(os.environ, {"CONTACT_EMAIL_PASSWORD": "x"}),                      mock.patch.object(r.imaplib, "IMAP4_SSL", lambda *a, **k: FakeImap(raw)),                      mock.patch.object(r.smtplib, "SMTP_SSL", FakeSmtp),                      mock.patch.object(r, "llm_call", lambda *a: draft),                      mock.patch.object(r, "GO_LIVE_TS", 1.0),                      mock.patch.dict(r.CFG, {"dry": False}),                      contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    rc = r.safe_main()
            finally:
                os.chdir(old)
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertEqual(len(sent), 1)  # the logic did run and replied
        self.assertIn("replied=1", text)
        for needle in (secret_addr, "thirdparty-example", "Zed", "Qux", secret_subj, secret_body):
            self.assertNotIn(needle, text)

    def test_crash_prints_type_only(self):
        import io, contextlib, unittest.mock as mock
        out = io.StringIO()
        with mock.patch.object(r, "main", side_effect=ValueError("secret@x.com Subject text")), contextlib.redirect_stdout(out):
            self.assertEqual(r.safe_main(), 1)
        self.assertNotIn("secret@x.com", out.getvalue())
        self.assertIn("ValueError", out.getvalue())


class ModelFallback(unittest.TestCase):
    def setUp(self):
        import tempfile
        r.MODEL_USED.update({"haiku": 0, "deepseek": 0})
        self._count, self._ok = r.AI_COUNT_PATH, os.environ.get("AI_COUNTER_OK")
        r.AI_COUNT_PATH = os.path.join(tempfile.mkdtemp(), "ops", "ai_calls_replier.json")      # tests never touch ops/
        os.environ["AI_COUNTER_OK"] = "true"

    def tearDown(self):
        r.AI_COUNT_PATH = self._count
        if self._ok is None:
            os.environ.pop("AI_COUNTER_OK", None)
        else:
            os.environ["AI_COUNTER_OK"] = self._ok

    def test_fails_closed_when_the_counter_was_not_restored(self):
        import unittest.mock as mock
        os.environ.pop("AI_COUNTER_OK", None)
        self.assertFalse(r.ai_take_call(today="2026-11-23"))
        self.assertFalse(os.path.exists(r.AI_COUNT_PATH))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": "d"}), mock.patch.object(r, "_post_json") as post:
            with self.assertRaises(RuntimeError):
                r.llm_call("a", "b")
        post.assert_not_called()                                   # no private repo = no model call, neither Haiku nor DeepSeek

    def test_workflow_restores_the_counter_serialises_runs_and_sets_the_flag(self):
        w = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".github", "workflows", "email-replier.yml"), encoding="utf8").read()
        self.assertIn("group: email-replier", w)
        self.assertIn("cancel-in-progress: false", w)
        self.assertIn("ai_calls_replier.json", w)
        self.assertIn("AI_COUNTER_OK=true", w)

    def test_daily_model_call_cap(self):
        import unittest.mock as mock
        self.assertEqual(r.AI_CALLS_PER_DAY, 150)
        for _ in range(r.AI_CALLS_PER_DAY):
            self.assertTrue(r.ai_take_call(today="2026-11-23"))
        self.assertFalse(r.ai_take_call(today="2026-11-23"))                 # the 151st call is refused
        self.assertTrue(r.ai_take_call(today="2026-11-24"))                  # a new UTC day starts again
        self.assertEqual(r.ai_calls_today(today="2026-11-24"), 1)
        # at the cap, llm_call makes NO model call (neither Haiku nor DeepSeek) and raises: the mail is skipped and retried tomorrow
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        json = __import__("json")
        json.dump({"date": today, "calls": r.AI_CALLS_PER_DAY}, open(r.AI_COUNT_PATH, "w"))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": "d"}), mock.patch.object(r, "_post_json") as post:
            with self.assertRaises(RuntimeError):
                r.llm_call("a", "b")
        post.assert_not_called()

    def test_each_attempt_counts_including_the_retry(self):
        import unittest.mock as mock
        seq = [RuntimeError("x"), {"content": [{"type": "text", "text": "H2"}]}]
        def fake(url, body, headers):
            v = seq.pop(0)
            if isinstance(v, Exception):
                raise v
            return v
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}), mock.patch.object(r, "_post_json", fake), mock.patch.object(r.time, "sleep"):
            r.llm_call("a", "b")
        self.assertEqual(r.ai_calls_today(), 2)

    def test_a_long_reply_is_cut_before_the_model(self):
        self.assertLessEqual(len(r._user_prompt("s", "x" * 50000)), 1500 + 6000 + 100)

    def test_haiku_primary(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": "d"}),              mock.patch.object(r, "_post_json", return_value={"content": [{"type": "text", "text": "H"}]}) as post:
            self.assertEqual(r.llm_call("a", "b"), "H")
        self.assertEqual(post.call_args[0][0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(post.call_args[0][1]["model"], "claude-haiku-5-5")
        self.assertEqual(r.MODEL_USED, {"haiku": 1, "deepseek": 0})

    def test_retry_once_then_deepseek(self):
        import unittest.mock as mock, urllib.error
        calls = []
        def fake(url, body, headers):
            calls.append(url)
            if "anthropic" in url:
                raise urllib.error.HTTPError(url, 529, "overloaded", {}, None)
            return {"choices": [{"message": {"content": "D"}}]}
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": "d"}),              mock.patch.object(r, "_post_json", fake), mock.patch.object(r.time, "sleep"):
            self.assertEqual(r.llm_call("a", "b"), "D")
        self.assertEqual(sum("anthropic" in c for c in calls), 2)
        self.assertEqual(r.MODEL_USED, {"haiku": 0, "deepseek": 1})

    def test_retry_succeeds_second_time(self):
        import unittest.mock as mock
        seq = [RuntimeError("x"), {"content": [{"type": "text", "text": "H2"}]}]
        def fake(url, body, headers):
            v = seq.pop(0)
            if isinstance(v, Exception):
                raise v
            return v
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}),              mock.patch.object(r, "_post_json", fake), mock.patch.object(r.time, "sleep"):
            self.assertEqual(r.llm_call("a", "b"), "H2")
        self.assertEqual(r.MODEL_USED["haiku"], 1)


class MarksEveryDecision(unittest.TestCase):
    """Audit needs IFBotHandled on every post-go-live mail the replier decided on, even when it does not reply."""
    def _run(self, llm_json, date):
        import io, tempfile, contextlib, unittest.mock as mock
        m = mk(frm="Jo <jo@thirdparty-example.org>", subj="Re: hello", irt="<CAabc@mail.gmail.com>",
               refs="<CAabc@mail.gmail.com>", Date=date)
        m.set_content("Our rate is $50 per link.\n> InayaFlow swap")
        stores = []
        class Rec(FakeImap):
            def uid(self, cmd, *a):
                if cmd == "STORE":
                    stores.append(a)
                    return "OK", []
                return FakeImap.uid(self, cmd, *a)
        old = os.getcwd()
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            try:
                with mock.patch.dict(os.environ, {"CONTACT_EMAIL_PASSWORD": "x"}),                      mock.patch.object(r.imaplib, "IMAP4_SSL", lambda *a, **k: Rec(m.as_bytes())),                      mock.patch.object(r, "llm_call", lambda *a: llm_json),                      mock.patch.object(r, "GO_LIVE_TS", 1700000000.0),                      mock.patch.dict(r.CFG, {"dry": False}),                      contextlib.redirect_stdout(io.StringIO()):
                    r.safe_main()
            finally:
                os.chdir(old)
        return stores

    def test_money_deal_skip_is_marked(self):
        stores = self._run('{"category":"MONEY_DEAL_OTHER","summary":"wants money","draft":""}', "Wed, 18 Nov 2026 12:00:00 +0000")
        self.assertTrue(any("IFBotHandled" in str(s) for s in stores))
        self.assertFalse(any("Answered" in str(s) for s in stores))

    def test_pre_golive_not_touched(self):
        stores = self._run('{"category":"MONEY_DEAL_OTHER","summary":"x","draft":""}', "Sun, 01 Jan 2023 12:00:00 +0000")
        self.assertEqual(stores, [])


if __name__ == "__main__":
    unittest.main()
