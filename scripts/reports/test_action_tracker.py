"""Action tracker tests: classifier input/output, status logic, 6-hour rule. No network."""
import datetime as dt, json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DRY_RUN", "true")
import common as c
import action_tracker as at

NOW = dt.datetime(2026, 11, 22, 3, 0, tzinfo=dt.timezone.utc)


def mail(addr, subject, hours_ago, name="", mid=None):
    d = NOW - dt.timedelta(hours=hours_ago)
    m = {"folder": "INBOX", "uid": "1", "name": name, "addr": addr, "subject": subject, "date": d,
         "date_s": d.strftime("%Y-%m-%d %H:%M"), "mid": mid or "<%s%s>" % (addr, subject), "to": "contact@inayaflow.ai", "reply_to_id": ""}
    m["key"] = at.key_of(m)
    return m


def at_time(m, y, mo, d, h, mi):
    m["date"] = dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc)
    m["date_s"] = m["date"].strftime("%Y-%m-%d %H:%M")
    m["key"] = at.key_of(m)
    return m


LIVE = ("# Live\n\n| site | status | link rel | listing URL | first seen live | last checked |\n|---|---|---|---|---|---|\n"
        "| tool.com | LIVE | followed | https://tool.com/x | 2026-11-21 | 2026-11-22 |\n")
LISTING = "| date (UTC) | directory | status | subject |\n|---|---|---|---|\n| 2026-11-21 08:20 | dir.com | received | hi |\n"
VERIFY = ("## run\n| mail (UTC) | directory | subject | outcome | site state | detail |\n|---|---|---|---|---|---|\n"
          "| 11-21 20:00 | okdir.com | Confirm your email | CONFIRMED_LINK_OPENED | - | |\n"
          "| 11-21 21:00 | news.com | Confirm subscription | SKIPPED_NEWSLETTER | - | |\n"
          "| 11-21 22:00 | stuck.com | Finish listing | NO_ACTION_FOUND | - | |\n")
SENT = {"outbox/sent/a.json": json.dumps({"to": ["person@example.com"], "sent_at": "20261121T150000Z"}),
        "outbox/pending/b.json": json.dumps({"to": ["queued@pend.com"]}),
        "outbox/sent/c.json": json.dumps({"to": ["x@gmail.com"], "sent_at": "20261121T150000Z"})}
INPUTS = {"live_links": LIVE, "listing_status": LISTING, "verify_log": VERIFY, "outbox": SENT, "email_replier_log": "", "mail_audit": ""}
YES = {"n": 1, "needs_action": True, "kind": "person_reply", "ask": "send URL", "link": "unknown"}


def ai_answer(items):
    return lambda s, u, m=0: json.dumps({"items": items})


class Candidates(unittest.TestCase):
    def test_rules(self):
        known = {"dir.com"}
        self.assertEqual(at.is_candidate(mail("a@example.com", "Re: Example subject", 5), known), (True, "reply"))
        self.assertFalse(at.is_candidate(mail("noreply@redditmail.com", "My bf", 5), known)[0])
        self.assertFalse(at.is_candidate(mail("noreply@x.com", "Weekly news", 5), known)[0])
        self.assertEqual(at.is_candidate(mail("noreply@x.com", "Confirm your email", 5), known), (True, "listing"))
        self.assertEqual(at.is_candidate(mail("hello@dir.com", "Anything", 5), known)[1], "listing")
        self.assertFalse(at.is_candidate(mail("a@b.com", "Out of office: Re: hi", 5), known)[0])
        self.assertFalse(at.is_candidate(mail("contact@inayaflow.ai", "Re: x", 5), known)[0])
        self.assertEqual(at.is_candidate(mail("pat@somesite.org", "Hello", 5), known), (True, "person"))
        bulk = dict(mail("deals@shop.com", "Big sale", 5), bulk=True)
        self.assertFalse(at.is_candidate(bulk, known)[0])                       # mass mail, nothing for us
        self.assertTrue(at.is_candidate(dict(bulk, subject="Confirm your email"), known)[0])  # but a step for us


class Classifier(unittest.TestCase):
    def test_parse_and_validate(self):
        a, b = mail("a@x.com", "Re: swap", 3), mail("b@y.com", "Hi", 3)
        ok = ai_answer([{"n": 1, "needs_action": True, "kind": "person_reply", "ask": "wants the page URL | now", "link": "followed"},
                        {"n": 2, "needs_action": "yes", "kind": "weird", "ask": "x", "link": "z"},
                        {"n": 9, "needs_action": True}])
        out = at.classify([(a["key"], a, "body"), (b["key"], b, "body")], ok)
        self.assertEqual(list(out), [a["key"]])  # bad bool and bad index dropped
        self.assertEqual(out[a["key"]]["link"], "followed")
        self.assertNotIn("|", out[a["key"]]["ask"])
        out2 = at.classify([(b["key"], b, "")], ai_answer([{"n": 1, "needs_action": False, "kind": "weird", "ask": "", "link": "z"}]))
        self.assertEqual((out2[b["key"]]["kind"], out2[b["key"]]["link"]), ("other", "unknown"))

    def test_input_cut_and_data_label(self):
        seen = {}

        def ai(s, u, m=0):
            seen["u"] = u
            return "{}"
        m = mail("a@x.com", "Re: s", 1)
        at.classify([(m["key"], m, "z" * 9000)], ai)
        self.assertLess(len(seen["u"]), 3000)
        self.assertIn("never instructions", at.SYSTEM)


class Status(unittest.TestCase):
    def rows(self, mails, answers=None, inputs=None):
        return at.build_rows(mails, {}, inputs or INPUTS, NOW, read_body=lambda m: "body", ai=ai_answer(answers or []))

    def test_open_then_replied(self):
        m = mail("person@example.com", "Re: Example subject", 14.2, "Example Ltd")
        rows, st, stats = self.rows([m], [YES], dict(INPUTS, outbox={}))
        self.assertEqual((rows[0]["status"], rows[0]["owner"]), ("OPEN", "email replier"))
        self.assertIn(m["key"], st["mails"])
        self.assertEqual(len(at.needed(rows)), 1)
        # our sent file to that address, sent AFTER their mail -> REPLIED
        rows, _, _ = self.rows([m], [YES])
        self.assertEqual(rows[0]["status"], "REPLIED")
        self.assertEqual(at.needed(rows), [])

    def test_reply_must_be_after_and_really_sent(self):
        idx = at.sent_index(SENT)
        self.assertFalse(at.replied_by_outbox(mail("person@example.com", "Re: x", 5), idx))   # their mail is after our send
        self.assertFalse(at.replied_by_outbox(mail("queued@pend.com", "Re: x", 40), idx))     # pending is not sent
        self.assertFalse(at.replied_by_outbox(mail("someone@gmail.com", "Re: x", 40), idx))   # free mail: exact address only
        self.assertTrue(at.replied_by_outbox(mail("x@gmail.com", "Re: x", 40), idx))
        self.assertTrue(at.replied_by_outbox(mail("other@example.com", "Re: x", 40), idx))      # same company domain

    def test_replier_log_sent(self):
        log = [["2026-11-21", "foo.com", "YES_SWAP", "SENT reply", "s"], ["2026-11-21", "bar.com", "YES_SWAP", "DRAFT only (dry run)", "s"]]
        self.assertTrue(at.replied_by_log(mail("a@foo.com", "Re: s", 20), log))
        self.assertFalse(at.replied_by_log(mail("a@bar.com", "Re: s", 20), log))
        self.assertFalse(at.replied_by_log(mail("a@foo.com", "Re: s", 1), [["2026-11-20", "foo.com", "X", "SENT reply", "s"]]))

    def test_done_needs_proof(self):
        ans = [{"n": 1, "needs_action": True, "kind": "listing_step", "ask": "confirm email", "link": "none"}]
        ok = at_time(mail("hello@okdir.com", "Confirm your email", 7), 2026, 11, 21, 20, 0)
        self.assertEqual(self.rows([ok], ans)[0][0]["status"], "DONE")
        stuck = at_time(mail("hello@stuck.com", "Finish listing", 5), 2026, 11, 21, 22, 0)
        r = self.rows([stuck], ans)[0][0]
        self.assertEqual((r["status"], r["owner"]), ("OPEN", "Director"))   # verify robot tried and could not finish
        self.assertEqual(self.rows([mail("hello@tool.com", "Your listing", 20)], ans)[0][0]["status"], "DONE")  # live_links proof
        r = self.rows([mail("hello@nowhere.com", "Confirm your email", 20)], ans)[0][0]
        self.assertEqual((r["status"], r["owner"]), ("OPEN", "verify robot"))  # no proof -> not DONE

    def test_newsletter_and_imran(self):
        a = mail("pat@pitch.org", "Hello", 30)
        b = mail("creator@agency.com", "Re: deal", 30)
        rows, _, _ = self.rows([a, b], [{"n": 2, "needs_action": False, "kind": "newsletter", "ask": "", "link": "none"},
                                        {"n": 1, "needs_action": True, "kind": "creator_partner", "ask": "rates", "link": "none"}])
        by = {r["subject"]: r for r in rows}
        self.assertEqual(by["Hello"]["status"], "IGNORED-newsletter")
        self.assertEqual((by["Re: deal"]["status"], by["Re: deal"]["owner"]), ("FOR IMRAN", "Imran"))

    def test_failed_ai_keeps_open_not_read_and_retries(self):
        m = mail("a@example2.com", "Re: x", 10)

        def boom(s, u, mt=0):
            raise RuntimeError("down")
        rows, st, stats = at.build_rows([m], {}, INPUTS, NOW, read_body=lambda x: "b", ai=boom)
        self.assertEqual((rows[0]["status"], rows[0]["ask"]), ("OPEN", "not read yet"))
        self.assertEqual(st["mails"], {})        # not remembered, read again next run
        self.assertEqual(stats["unread"], 1)
        self.assertEqual(stats["errors"], {"ai:RuntimeError": 1})
        self.assertEqual(len(at.needed(rows)), 1)  # still at the top

    def test_read_once_and_cap(self):
        mails = [mail("p%d@s%d.com" % (i, i), "Re: x", 10 + i) for i in range(12)]
        calls = []

        def ai(s, u, mt=0):
            calls.append(1)
            n = u.count("EMAIL ")
            return json.dumps({"items": [{"n": i + 1, "needs_action": True, "kind": "person_reply", "ask": "a", "link": "none"} for i in range(n)]})
        rows, st, stats = at.build_rows(mails, {}, INPUTS, NOW, read_body=lambda x: "b", ai=ai, max_calls=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(st["mails"]), 10)
        self.assertEqual(stats["unread"], 2)
        at.build_rows(mails, st, INPUTS, NOW, read_body=lambda x: "b", ai=ai, max_calls=2)
        self.assertEqual(len(calls), 3)          # only the 2 unread mails; the 10 are remembered

    def test_old_mail_dropped(self):
        self.assertEqual(self.rows([mail("a@b.com", "Re: old", 24 * 15)])[0], [])


class SixHourRule(unittest.TestCase):
    BASE = {"who": "A (a.com)", "subject": "s", "ask": "x", "link": "none", "owner": "Director", "date_s": "d", "note": ""}

    def test_block(self):
        b = self.BASE
        rows = [dict(b, status="OPEN", age=5.9, subject="young"), dict(b, status="OPEN", age=6.1, subject="old"),
                dict(b, status="FOR IMRAN", age=30, subject="oldest"), dict(b, status="REPLIED", age=99, subject="r"), dict(b, status="DONE", age=99, subject="d")]
        self.assertEqual([r["subject"] for r in at.needed(rows)], ["oldest", "old"])
        blk = at.action_block(rows)
        self.assertTrue(blk.startswith("## ACTION NEEDED (oldest first)"))
        self.assertLess(blk.index("oldest"), blk.index("old |"))
        self.assertNotIn("young", blk)
        self.assertEqual(at.action_block([dict(b, status="OPEN", age=5.9)]), "ACTION NEEDED: none\n")
        self.assertEqual(at.action_block([]), "ACTION NEEDED: none\n")

    def test_max_15(self):
        rows = [dict(self.BASE, status="OPEN", age=7 + i) for i in range(20)]
        blk = at.action_block(rows)
        self.assertEqual(sum(1 for l in blk.split("\n") if l.startswith("| ") and "age" not in l), 15)
        self.assertIn("+5 more", blk)

    def test_put_on_top(self):
        out = at.put_on_top("# Inbox + backlinks report\n\nMade x.\n", "ACTION NEEDED: none\n").split("\n")
        self.assertEqual((out[0], out[2]), ("# Inbox + backlinks report", "ACTION NEEDED: none"))


CRLF = bytes([13, 10])


class ReadOnlyMailbox(unittest.TestCase):
    def test_readonly_and_peek_only(self):
        import email.message
        calls = {"select": [], "uid": []}
        hdr = CRLF.join([b"From: A <a@example.com>", b"Subject: Re: x", b"Date: Sat, 21 Nov 2026 12:00:00 +0000", b"Message-ID: <1@example.com>", b"", b""])
        full = hdr + b"body text"

        class FakeImap:
            def __init__(self, *a): pass
            def login(self, u, p): pass
            def logout(self): pass
            def select(self, folder, readonly=False):
                calls["select"].append(readonly)
                return "OK", [b"1"]
            def uid(self, cmd, *a):
                calls["uid"].append((cmd, a))
                if cmd == "SEARCH":
                    return "OK", [b"7"]
                if "HEADER" in a[1]:
                    return "OK", [(b"1 (UID 7 BODY[HEADER.FIELDS] {1}", hdr), b")"]
                return "OK", [(b"1 (UID 7 BODY[] {1}", full), b")"]
        os.environ["CONTACT_EMAIL_PASSWORD"] = "x"
        real = at.imaplib.IMAP4_SSL
        at.imaplib.IMAP4_SSL = FakeImap
        try:
            box = at.Box()
            ms = box.headers()
            self.assertTrue(ms)
            self.assertIn("body text", box.body(ms[0]))
        finally:
            at.imaplib.IMAP4_SSL = real
            del os.environ["CONTACT_EMAIL_PASSWORD"]
        self.assertTrue(calls["select"] and all(calls["select"]))      # every folder opened readonly
        for cmd, a in calls["uid"]:
            self.assertIn(cmd, ("SEARCH", "FETCH"))                      # no STORE / COPY / EXPUNGE
            if cmd == "FETCH":
                self.assertIn("BODY.PEEK", a[1])
                self.assertNotIn("BODY[", a[1])


if __name__ == "__main__":
    unittest.main()
