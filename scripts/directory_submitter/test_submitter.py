#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import submitter as s


def F(sel, **kw):
    d = {"sel": sel, "tag": "input", "type": "text", "label": "", "placeholder": "", "name": "", "id": "", "required": False, "maxlength": None, "options": None}
    d.update(kw)
    return d


class T(unittest.TestCase):
    def test_guardrail_text(self):
        self.assertEqual(s.detect_block_text("Please verify you are human"), "captcha")
        self.assertEqual(s.detect_block_text("Just a moment... checking if the site connection is secure"), "captcha")
        self.assertEqual(s.detect_block_text("Enter your credit card number"), "payment required")
        self.assertIn("badge", s.detect_block_text("A badge on your site is required for free listings"))
        self.assertIsNone(s.detect_block_text("Submit your tool. It is free."))

    def test_login(self):
        self.assertTrue(s.detect_login("", 5, True))
        self.assertTrue(s.detect_login("Please sign in to submit", 1, False))
        self.assertIsNone(s.detect_login("Please sign in to submit", 6, False))

    def test_field_hard_stops(self):
        self.assertEqual(s.check_fields([F("a", type="password")]), "login or sign-up wall (password field)")
        self.assertEqual(s.check_fields([F("a", type="tel", required=True, label="Phone")]), "phone number required")
        self.assertIsNone(s.check_fields([F("a", type="tel", required=False, label="Phone")]))
        self.assertEqual(s.check_fields([F("a", label="Card number")]), "payment required")

    def test_paid_plan(self):
        self.assertTrue(s.plan_is_paid([F("a", tag="select", options=["Basic $9", "Pro $29"])]))
        self.assertFalse(s.plan_is_paid([F("a", tag="select", options=["Free", "Pro $29"])]))

    def test_mapping_accepts_true_facts(self):
        fields = [F("n", label="Tool name", required=True), F("u", label="Website", required=True),
                  F("e", label="Email", required=True), F("d", label="Description", maxlength=160)]
        m = {"n": "InayaFlow", "u": "https://inayaflow.ai", "e": "contact@inayaflow.ai", "d": s.LISTING["short_description"][:100]}
        clean, miss = s.validate_mapping(m, fields)
        self.assertEqual(len(clean), 4)
        self.assertIsNone(miss)

    def test_mapping_rejects_invented(self):
        fields = [F("n", label="Name", required=True), F("r", label="Monthly revenue", required=True)]
        clean, miss = s.validate_mapping({"n": "InayaFlow", "r": "$12,000"}, fields)
        self.assertNotIn("r", clean)
        self.assertIn("no true value", miss)
        clean, miss = s.validate_mapping({"n": "InayaFlow", "r": "__UNKNOWN__"}, fields)
        self.assertIn("Monthly revenue", miss)

    def test_mapping_unknown_selector_and_checkbox(self):
        fields = [F("c", type="checkbox", label="I agree to the terms"), F("m", type="checkbox", label="Subscribe to newsletter")]
        clean, _ = s.validate_mapping({"c": "__CHECK__", "m": "__CHECK__", "zzz": "InayaFlow"}, fields)
        self.assertEqual(list(clean), ["c"])

    def test_required_checkbox_without_agree_blocks(self):
        fields = [F("m", type="checkbox", label="I confirm I own this product", required=True)]
        clean, miss = s.validate_mapping({"m": "__CHECK__"}, fields)
        self.assertIn("no true value", miss)

    def test_mapping_select_free_option(self):
        fields = [F("p", tag="select", type="select-one", label="Plan", options=["Free", "Featured $49"])]
        self.assertEqual(s.validate_mapping({"p": "Free"}, fields)[0], {"p": "Free"})
        self.assertEqual(s.validate_mapping({"p": "Featured $49"}, fields)[0], {})

    def test_parse_mapping(self):
        self.assertEqual(s.parse_mapping('noise {"a": "b"} tail'), {"a": "b"})
        self.assertEqual(s.parse_mapping("garbage"), {})

    def test_select_directories(self):
        rows = [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}, {"name": "E"}]
        state = {"A": {"result": "SUBMITTED", "attempts": 1},
                 "B": {"result": "BLOCKED:no submit form found", "attempts": 1, "fix": "old"},
                 "C": {"result": "BLOCKED:no submit form found", "attempts": 2, "fix": "old"},
                 "D": {"result": "BLOCKED:captcha", "attempts": 1, "fix": "old"}}
        out = s.select_directories(rows, state, 0, 15, 80)
        self.assertEqual([r["name"] for r in out], ["B", "E"])  # B fixable+1 try; C used 2; D not fixable; A done

    def test_stop_at_target_successes_not_attempts(self):
        rows = [{"name": str(i)} for i in range(50)]
        self.assertEqual(s.select_directories(rows, {}, 30, 15, 80, successes_today=20, target=20), [])
        self.assertEqual(len(s.select_directories(rows, {}, 30, 15, 80, successes_today=5, target=20)), 15)
        self.assertEqual(len(s.select_directories(rows, {}, 75, 15, 80, successes_today=5, target=20)), 5)
        self.assertEqual(s.select_directories(rows, {}, 80, 15, 80, successes_today=0, target=20), [])

    def test_retry_only_after_fix(self):
        st = {"result": "BLOCKED:no submit form found", "attempts": 1, "fix": s.FIX_VERSION}
        self.assertFalse(s.can_retry(st))          # same version: no fix yet
        st["fix"] = "older"
        self.assertTrue(s.can_retry(st))
        st["attempts"] = 2
        self.assertFalse(s.can_retry(st))          # never more than 2 in total

    def test_reason_codes(self):
        for res, code in (("BLOCKED:no submit form found (url /)", "NO_FORM"), ("BLOCKED:captcha", "CAPTCHA"),
                          ("BLOCKED:payment required", "PAID"), ("BLOCKED:badge or backlink required on our site", "BADGE_REQUIRED"),
                          ("BLOCKED:phone number required", "PHONE"), ("BLOCKED:login by Google/GitHub/X only", "LOGIN_GOOGLE_ONLY"),
                          ("ERROR:Error Page.goto: net::ERR_NAME_NOT_RESOLVED", "DEAD"),
                          ("ERROR:TimeoutError Locator.click: Timeout 20000ms", "DROPDOWN_TIMEOUT"),
                          ("BLOCKED:magic link not received within 90 s", "CODE_NOT_RECEIVED")):
            self.assertEqual(s.reason_code(res)[0], code, res)
        self.assertEqual(s.reason_code("SUBMITTED"), (None, None))

    def test_summary_and_list_low(self):
        line = s.summary_line("2026-11-18", 10, 3, 1, {"NO_FORM": 4, "CAPTCHA": 1}, 12)
        self.assertIn("successes 3", line)
        self.assertIn("NO_FORM 4", line)
        self.assertIn("LIST LOW", line)
        self.assertNotIn("LIST LOW", s.summary_line("d", 1, 1, 0, {}, 60))
        self.assertEqual(s.untried_count([{"name": "a"}, {"name": "b"}], {"a": {}}), 1)

    def test_confirm_links_same_domain_only(self):
        txt = "Click https://evil.com/confirm?x=1 or https://app.fooducts.com/verify/abc or https://fooducts.com/about"
        links = s.pick_confirm_links(txt, "https://www.fooducts.com/submit")
        self.assertEqual(links[0], "https://app.fooducts.com/verify/abc")
        self.assertNotIn("https://evil.com/confirm?x=1", links)

    def test_sender_matches(self):
        self.assertTrue(s.sender_matches("Foo <noreply@mail.fooducts.com>", "https://www.fooducts.com"))
        self.assertFalse(s.sender_matches("x@other.com", "https://www.fooducts.com"))

    def test_listing_form_vs_newsletter(self):
        self.assertFalse(s.is_listing_form([F("e", type="email", label="Enter your email")]))
        self.assertTrue(s.is_listing_form([F("u", label="Website URL"), F("e", type="email", label="Email")]))

    def test_custom_widgets(self):
        fields = [F("p", tag="customselect", type="customselect", label="Pricing*", required=True, options=["Free", "Freemium", "Paid"]),
                  F("b", tag="buttons", type="buttons", label="Buy ad space?*", required=True, options=["YES", "NO"])]
        clean, miss = s.validate_mapping({"p": "Freemium", "b": "NO"}, fields)
        self.assertEqual(clean, {"p": "Freemium", "b": "NO"})
        self.assertIsNone(miss)
        clean, miss = s.validate_mapping({"p": "Gold"}, fields)
        self.assertIn("no true value", miss)

    def test_site_password(self):
        a = s.site_password("k1", "https://www.example.com/submit")
        self.assertEqual(a, s.site_password("k1", "https://app.example.com/x"))
        self.assertNotEqual(a, s.site_password("k1", "https://other.com"))
        self.assertNotEqual(a, s.site_password("k2", "https://example.com"))
        self.assertGreaterEqual(len(a), 20)
        self.assertTrue(any(c.isdigit() for c in a) and "!" in a)
        with self.assertRaises(RuntimeError):
            s.site_password("", "https://example.com")

    def test_find_code(self):
        self.assertEqual(s.find_code("Your code is 482913. Thanks"), "482913")
        self.assertIsNone(s.find_code("Copyright 2026 Foo"))
        self.assertIsNone(s.find_code("call 123456789012"))

    def test_customselect_without_options_accepts_true_value(self):
        fields = [F("p", tag="customselect", type="customselect", label="Pricing", required=True, options=[])]
        self.assertEqual(s.validate_mapping({"p": "Freemium"}, fields)[0], {"p": "Freemium"})
        self.assertEqual(s.validate_mapping({"p": "Gold"}, fields)[0], {})

    def test_submit_link_and_magic_regex(self):
        for t in ("Submit tool", "Add your product", "List your tool", "Get listed", "Launch", "Submit startup"):
            self.assertTrue(s.SUBMIT_LINK_RE.search(t), t)
        self.assertTrue(s.MAGIC_RE.search("We will email you a magic link"))
        self.assertTrue(s.REQUIRED_ERR_RE.search("THIS QUESTION IS REQUIRED"))

    def test_skip_rows_and_retry_order(self):
        rows = [{"name": "N1"}, {"name": "S", "skip": "not a directory"}, {"name": "R"}, {"name": "N2"}]
        state = {"R": {"result": "BLOCKED:no submit form found", "attempts": 1, "fix": "old"}}
        out = s.select_directories(rows, state, 0, 15, 80)
        self.assertEqual([r["name"] for r in out], ["R", "N1", "N2"])  # retry-after-fix first, skip never
        self.assertEqual(s.untried_count(rows, state), 2)

    def test_non_fixable_never_retried(self):
        for res in ("BLOCKED:captcha", "BLOCKED:paid plan", "BLOCKED:login by Google/GitHub/X only", "ERROR:Page.goto: net::ERR_NAME_NOT_RESOLVED"):
            self.assertFalse(s.can_retry({"result": res, "attempts": 1, "fix": "old"}), res)

    def test_new_regexes(self):
        for t in ("Sign up", "Register", "Create account", "Get started", "Join now"):
            self.assertTrue(s.SIGNUP_LINK_RE.search(t), t)
        self.assertTrue(s.SUBMIT_LINK_RE.search("Or list for free"))
        self.assertTrue(s.LOGIN_PAGE_RE.search("Don't have an account? Sign up"))
        self.assertTrue(s.ALREADY_RE.search("This email is already registered"))
        self.assertTrue(s.SOCIAL_RE.search("Continue with Google"))
        self.assertTrue(s.AUTH_URL_RE.search("/auth/signup"))
        self.assertEqual(s.reason_code("BLOCKED:no sign-up button or email form found (links: a)")[0], "SIGNUP_FAILED")
        self.assertEqual(s.reason_code("BLOCKED:sign-up login failed: no login form")[0], "SIGNUP_FAILED")
        self.assertEqual(s.reason_code("ERROR:dropdown timeout on required field: Pricing (TimeoutError)")[0], "DROPDOWN_TIMEOUT")
        self.assertEqual(s.reason_code("ERROR:payment required (HTTP 402)")[0], "PAID")

    def test_list_low_threshold(self):
        self.assertIn("LIST LOW", s.summary_line("d", 1, 0, 0, {}, 59))
        self.assertNotIn("LIST LOW", s.summary_line("d", 1, 0, 0, {}, 60))

    def test_precheck_exits_at_cap(self):
        rows = [{"name": str(i)} for i in range(30)]
        self.assertEqual(s.select_directories(rows, {}, 0, 15, 80, successes_today=20, target=20), [])
        self.assertEqual(s.select_directories(rows, {}, 80, 15, 80, successes_today=1, target=20), [])

    def test_clean_out_hides_urls_and_emails(self):
        t = s.clean_out("Page.goto timeout at https://x.com/verify?token=abc123 for contact@inayaflow.ai")
        self.assertNotIn("token", t)
        self.assertNotIn("@", t)

    def test_submit_rank_order(self):
        texts = ["Launch", "Get listed", "Submit your product", "Submit a tool", "Submit", "Add tool", "Submit now"]
        ranked = sorted(texts, key=s.submit_rank)
        self.assertEqual(ranked[0], "Submit")
        self.assertLess(s.submit_rank("Submit a tool"), s.submit_rank("Get listed"))
        self.assertLess(s.submit_rank("Get listed"), s.submit_rank("Launch"))
        self.assertLess(s.submit_rank("Launch"), s.submit_rank("Submit now"))

    def test_model_json_parse(self):
        self.assertEqual(s.parse_mapping('```json {"a": "b"} ```'), {"a": "b"})

    def test_list_builder(self):
        import build_list
        md = "| 1 | FutureTools | https://x.io | AI | yes | a | b | c | note | TODO |\n| 2 | Paid | https://p.io | AI | no | a | b | c | n | PAID |\n"
        self.assertEqual([r["name"] for r in build_list.build(md)], ["FutureTools"])


if __name__ == "__main__":
    unittest.main()
