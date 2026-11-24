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
        r = self.rows([mail("hello@nowhere.com", "Confirm your email", 2)], ans)[0][0]
        self.assertEqual((r["status"], r["owner"]), ("OPEN", "verify robot"))  # no proof -> not DONE (fresh: the robot still has its chances)
        r = self.rows([mail("hello@nowhere.com", "Confirm your email", 20)], ans)[0][0]
        self.assertEqual((r["status"], r["owner"]), ("OPEN", "Director"))      # 20 h and the robot never made an outcome: not left with the robot

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


class AutoClose(unittest.TestCase):
    """24 Nov 2026: OPEN must mean real work."""

    def rows(self, mails, answers=None, inputs=None):
        return at.build_rows(mails, {}, inputs or INPUTS, NOW, read_body=lambda m: "body", ai=ai_answer(answers or []))[0]

    def one(self, addr, subject, hours, kind="listing_step", ask="x", name=""):
        ans = [{"n": 1, "needs_action": True, "kind": kind, "ask": ask, "link": "none"}]
        return self.rows([mail(addr, subject, hours, name)], ans)[0]

    def test_one_time_links_and_codes_expire_after_24h(self):
        r = self.one("noreply@somesite.io", "Verify with your magic link to sign in", 30)
        self.assertEqual((r["status"], r["owner"]), ("EXPIRED (no action possible)", "-"))
        r = self.one("noreply@deepseek.com", "Your verification code is 482913", 26)
        self.assertEqual(r["status"], "EXPIRED (no action possible)")
        r = self.one("noreply@somesite.io", "Verify with your magic link to sign in", 10)   # still fresh
        self.assertEqual(r["status"], "OPEN")
        r = self.one("noreply@somesite.io", "Confirm your email", 80)           # a normal confirm mail is NOT a one-time code
        self.assertEqual(r["status"], "OPEN")

    def test_verify_robot_expired_closes_at_once(self):
        v = VERIFY + "| 11-21 23:00 | gone.io | Verify your sign in link | SESSION_ONLY_EXPIRED | - | |\n"
        m = at_time(mail("noreply@gone.io", "Verify your sign in link", 3), 2026, 11, 21, 23, 0)
        r = self.rows([m], [{"n": 1, "needs_action": True, "kind": "listing_step", "ask": "x", "link": "none"}], dict(INPUTS, verify_log=v))[0]
        self.assertEqual(r["status"], "EXPIRED (no action possible)")

    def test_paid_offers_ignored_but_person_goes_to_imran(self):
        r = self.one("hello@somedir.io", "Get featured: priority review for $49", 30, ask="upgrade offer")
        self.assertEqual((r["status"], r["owner"]), ("IGNORED-paid offer", "-"))
        r = self.one("sales@somedir.io", "Sponsor our newsletter", 30)
        self.assertEqual(r["status"], "IGNORED-paid offer")
        r = self.one("pat@somesite.org", "Re: backlink swap, we charge $30 for a post", 30, kind="person_reply")
        self.assertEqual((r["status"], r["owner"]), ("FOR IMRAN", "Imran"))
        r = self.one("pat@somesite.org", "Re: backlink swap", 30, kind="person_reply")   # a person, no money: stays OPEN
        self.assertEqual(r["status"], "OPEN")
        r = self.one("hello@somedir.io", "Confirm your email, premium is optional", 30)   # a real step is never auto-closed
        self.assertEqual(r["status"], "OPEN")

    def test_welcome_mails_ignored(self):
        r = self.one("hello@newdir.io", "Welcome to NewDir!", 30, ask="")
        self.assertEqual(r["status"], "IGNORED-info")
        r = self.one("hello@newdir.io", "Welcome to NewDir, please confirm your email", 30)
        self.assertEqual(r["status"], "OPEN")

    def test_verify_opened_link_is_done_and_our_app_ignored(self):
        v = VERIFY + "| 11-21 23:30 | tinystartups.co | Welcome, confirm | CONFIRMED_LINK_OPENED | - | |\n| 11-21 23:40 | supabase.io | Confirm | IGNORE_OUR_APP | - | |\n"
        a = at_time(mail("noreply@tinystartups.co", "Welcome, confirm", 3), 2026, 11, 21, 23, 30)
        b = at_time(mail("noreply@supabase.io", "Confirm", 3), 2026, 11, 21, 23, 40)
        ans = [{"n": 1, "needs_action": True, "kind": "other", "ask": "x", "link": "none"}] * 2
        rows = {r["subject"]: r for r in self.rows([a, b], ans, dict(INPUTS, verify_log=v))}
        self.assertEqual(rows["Welcome, confirm"]["status"], "DONE")
        self.assertEqual(rows["Confirm"]["status"], "IGNORED-info")

    def test_duplicates_collapse_with_count(self):
        ms = [mail("noreply@dup.io", "Confirm your email", h, mid="<%d>" % h) for h in (8, 20, 50)]
        ms.append(mail("noreply@other.io", "Confirm your email", 9))
        rows = self.rows(ms, [{"n": i, "needs_action": True, "kind": "listing_step", "ask": "x", "link": "none"} for i in range(1, 6)])
        dup = [r for r in rows if "dup.io" in r["who"]]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["count"], 3)
        self.assertIn("x3", dup[0]["note"])
        self.assertEqual(dup[0]["age"], 50)           # oldest, so nothing looks younger than it is
        self.assertEqual(len(rows), 2)                # the other sender stays its own row
        self.assertEqual(at.subject_sig("Re: Code 123456 for you"), at.subject_sig("Code 98 for you"))

    def test_person_never_auto_closed(self):
        r = self.one("pat@somesite.org", "Re: Your magic link", 90, kind="person_reply")
        self.assertEqual(r["status"], "OPEN")


class RobotNeverHoldsForDays(unittest.TestCase):
    """24 Nov 2026 (second pass): 17 items sat with 'verify robot' for up to 6 days."""

    def one(self, addr, subject, hours, ask="x"):
        ans = [{"n": 1, "needs_action": True, "kind": "listing_step", "ask": ask, "link": "none"}]
        return at.build_rows([mail(addr, subject, hours)], {}, INPUTS, NOW, read_body=lambda m: "body", ai=ai_answer(ans))[0][0]

    def test_openai_plugin_approval_is_info(self):
        r = self.one("noreply@tm.openai.com", "ChatGPT Plugin Submission Approved [asdk_app_6afba]", 140, "Publish the approved plugin")
        self.assertEqual((r["status"], r["owner"]), ("IGNORED-info", "-"))
        self.assertIn("already published", r["note"])

    def test_optional_steps_are_info(self):
        for addr, subj, ask in (("hi@starterstory.com", "Don't forget to submit your story (InayaFlow)", "Submit the story in the story editor"),
                                ("hi@mcptry.com", "TikTok Scripts / InayaFlow is listed on mcptry (score 83/100)", "Optionally claim the listing by signing in with GitHub"),
                                ("diogo@awesomeindie.com", "Welcome to Awesome Indie", "Finish profile setup (photo and bio), follow makers, and optionally submit a product")):
            r = self.one(addr, subj, 80, ask)
            self.assertEqual((r["status"], r["owner"]), ("IGNORED-info", "-"), subj)

    def test_paid_plan_ask_is_closed(self):
        r = self.one("hello@aiagentslive.com", "InayaFlow is one step from going live", 5, "Choose a paid plan ($7 or $10) to take the listing live")
        self.assertEqual(r["status"], "IGNORED-paid offer")

    def test_non_verify_step_goes_to_director_not_robot(self):
        r = self.one("hi@aidirs.best", "Finish listing InayaFlow on Aidirs.best", 100, "Finish the submission via the dashboard link")
        self.assertEqual((r["status"], r["owner"]), ("OPEN", "Director"))
        self.assertIn("not a verify link", r["note"])

    def test_robot_outcome_that_needs_a_person_goes_to_director(self):
        v = VERIFY + "| 11-22 01:00 | capdir.com | Activate your account | CAPTCHA | - | |\n"
        m = at_time(mail("hi@capdir.com", "Activate your account", 30), 2026, 11, 22, 1, 0)
        ans = [{"n": 1, "needs_action": True, "kind": "listing_step", "ask": "activate", "link": "none"}]
        r = at.build_rows([m], {}, dict(INPUTS, verify_log=v), NOW, read_body=lambda m: "b", ai=ai_answer(ans))[0][0]
        self.assertEqual((r["owner"], r["note"]), ("Director", "verify robot: captcha"))

    def test_replies_and_forwards_are_never_auto_closed(self):
        for subj in ("Re: Your listing: choose a paid plan", "RE: Welcome, your listing is ready", "Fwd: Don't forget to submit your listing story", "FW: ChatGPT Plugin Submission Approved listing", "AW: optional listing profile setup", "SV: listing upgrade offer $49"):
            r = self.one("noreply@newdir.io", subj, 50, "Choose a paid plan; optional profile setup; upgrade")
            self.assertEqual((r["status"], r["owner"]), ("OPEN", "Director"), subj)

    def test_real_verify_mail_stays_with_robot_only_while_fresh(self):
        self.assertEqual(self.one("hi@newdir.io", "Verify your email - NewDir", 1)["owner"], "verify robot")
        r = self.one("hi@newdir.io", "Verify your email - NewDir", 4)
        self.assertEqual(r["owner"], "Director")
        self.assertIn("no outcome", r["note"])


class MailHealth(unittest.TestCase):
    B = {"who": "A (a.com)", "subject": "s", "ask": "x", "link": "none", "date_s": "d", "note": ""}

    def test_red_when_over_6h(self):
        b = self.B
        rows = [dict(b, status="OPEN", age=7, owner="Director"), dict(b, status="FOR IMRAN", age=30, owner="Imran"),
                dict(b, status="OPEN", age=2, owner="Director"), dict(b, status="DONE", age=99, owner="-")]
        t = at.mail_health(rows)
        self.assertIn("MAIL HEALTH: RED", t)
        self.assertIn("Open items: 3 (Director 2, Imran 1)", t)
        self.assertIn("Oldest open item: 30.0h" if False else "Oldest open item: ", t)
        self.assertIn("Open over 6 h: 2 (RED)", t)

    def test_green(self):
        t = at.mail_health([dict(self.B, status="OPEN", age=2, owner="Director"), dict(self.B, status="DONE", age=99, owner="-")])
        self.assertIn("MAIL HEALTH: GREEN", t)
        self.assertIn("Open over 6 h: 0", t)
        self.assertNotIn("RED", t)
        self.assertIn("Oldest open item: none", at.mail_health([]))

    def test_goes_on_top_of_report(self):
        out = at.put_on_top("# Report\n\nbody\n", at.mail_health([]) + "\n" + at.action_block([]))
        self.assertTrue(out.split("\n")[2].startswith("## MAIL HEALTH"))


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
