"""Offline tests for the list-builder robot (24 Nov 2026). No network, no secrets."""
import contextlib, io, json, os, re, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, HERE)
import builder as b

TODAY = "2026-11-24"
SECRET = "sk-ant-TESTSECRET-12345"
ROW_KEYS = {"name", "domain", "submit_url", "source", "their_category", "their_auto_flag", "their_notes", "their_status", "http", "final_url",
            "free_check", "captcha_words_on_page", "has_form_on_page", "quality", "usable", "followed", "checked", "status", "category", "added", "source_query",
            "found_by", "proof", "needs_signup"}


def usage(inp=20000, out=2000, searches=3):
    return {"input_tokens": inp, "output_tokens": out, "server_tool_use": {"web_search_requests": searches}}


def answer(items, usg=None):
    text = "LIST DONE\n" + "\n".join(json.dumps(i) for i in items) + "\nCOUNT %d" % len(items)
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": usg or usage()}


def good(i, **kw):
    d = {"name": "Site %d" % i, "url": "https://site%d-example.com/submit" % i, "type": "directory", "free": "yes", "note": "a free tool directory"}
    d.update(kw)
    return d


def alive(url, timeout=12, limit=0):
    return 200, url, "<html><form>submit your tool</form></html>" + "x" * 400


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path_names = ("DATA", "CAND_PATH", "STATE_PATH", "SIGNUP_PATH", "REPORT_PATH", "DS_CAND_PATH", "DS_STATE_PATH", "DS_SIGNUP_PATH", "DS_REPORT_PATH", "COMPARE_PATH")
        self.saved = ({n: getattr(b, n) for n in self.path_names}, b.DRY)
        for n, fn in (("SIGNUP_PATH", "needs_signup_sonnet.md"), ("REPORT_PATH", "list_builder_report_sonnet.md"), ("DS_CAND_PATH", "directories_candidates_deepseek.json"),
                      ("DS_STATE_PATH", "list_builder_state_deepseek.json"), ("DS_SIGNUP_PATH", "needs_signup_deepseek.md"),
                      ("DS_REPORT_PATH", "list_builder_report_deepseek.md"), ("COMPARE_PATH", "list_builder_compare.md")):
            setattr(b, n, os.path.join(self.dir, fn))
        b.DATA = self.dir
        b.CAND_PATH = os.path.join(self.dir, "directories_candidates.json")
        b.STATE_PATH = os.path.join(self.dir, "list_builder_state.json")
        b.DRY = False
        self.write_cands([{"name": "Old", "domain": "old-example.com", "submit_url": "https://old-example.com/submit", "final_url": "https://www.old-example.com/",
                           "status": "TODO", "checked": "2026-11-20"}])

    def tearDown(self):
        for n, v in self.saved[0].items():
            setattr(b, n, v)
        b.DRY = self.saved[1]
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_cands(self, sites):
        json.dump({"generated": "2026-11-23", "sites": sites}, open(b.CAND_PATH, "w", encoding="utf8"))

    def cands(self):
        return json.load(open(b.CAND_PATH, encoding="utf8"))["sites"]

    def state(self):
        return json.load(open(b.STATE_PATH, encoding="utf8"))

    def ds_cands(self):
        return json.load(open(b.DS_CAND_PATH, encoding="utf8"))["sites"] if os.path.exists(b.DS_CAND_PATH) else []

    def ds_state(self):
        return json.load(open(b.DS_STATE_PATH, encoding="utf8"))

    def go(self, post, getter=alive, **kw):
        buf = io.StringIO()
        kw.setdefault("proof", lambda u: ("UNKNOWN", "test"))                       # never the network
        with contextlib.redirect_stdout(buf):
            r = b.run(post=post, getter=getter, today=TODAY, workers=1, **kw)
        return r, buf.getvalue()


class Helpers(unittest.TestCase):
    def test_registrable_domain(self):
        self.assertEqual(b.reg_domain("www.blog.example.com"), "example.com")
        self.assertEqual(b.reg_domain("shop.example.co.uk"), "example.co.uk")
        self.assertEqual(b.reg_domain("a.b.example.com.au"), "example.com.au")

    def test_dedupe_key_shared_hosts_keep_the_path(self):
        self.assertEqual(b.dedupe_key("https://www.Example.com/a/b"), "example.com")
        self.assertEqual(b.dedupe_key("https://github.com/sindresorhus/awesome/blob/main/readme.md"), "github.com/sindresorhus/awesome")
        self.assertNotEqual(b.dedupe_key("https://github.com/a/one"), b.dedupe_key("https://github.com/a/two"))

    def test_norm_url(self):
        self.assertEqual(b.norm_url("example.com/x?utm_source=a&b=1#frag"), "https://example.com/x?b=1")
        for bad in ("", "ftp://x.com", "http://127.0.0.1/x", "localhost", "https://nodot/"):
            self.assertIsNone(b.norm_url(bad), bad)

    def test_spam_filter(self):
        for t in ("Buy backlinks cheap", "best online casino list", "PBN link packages", "adult dating directory", "guest post service $50"):
            self.assertTrue(b.is_spam(t), t)
        self.assertTrue(b.is_spam("Nice site", "https://greatdeals.xyz.casino/"))
        self.assertFalse(b.is_spam("AI tools directory", "https://aitools-example.com/submit", "free listing for AI tools"))

    def test_parse_rows_ignores_noise(self):
        rows = b.parse_rows('LIST DONE\n{"name": "A", "url": "https://a.com"},\nnot json\n{"name": "no url"}\n{broken\nCOUNT 1')
        self.assertEqual([r["name"] for r in rows], ["A"])


class Rotation(unittest.TestCase):
    def test_twelve_categories_in_the_brief(self):
        self.assertEqual(len(b.CATEGORIES), 12)
        self.assertEqual({k for k, *_ in b.CATEGORIES}, {
            "ai_saas_directories", "launch_sites", "tool_roundups", "github_awesome_lists", "saas_review_platforms", "guest_post_pages", "resource_pages",
            "communities_profiles", "podcasts_creators", "journalist_platforms", "edu_library_guides", "startup_directories_by_country"})

    def test_every_category_every_n_calls_then_angle_moves_on(self):
        first = [b.pick_category(i)[0] for i in range(12)]
        self.assertEqual(len(set(first)), 12)
        self.assertEqual([b.pick_category(12 + i)[0] for i in range(12)], first)             # same order each turn
        for i in range(12):
            self.assertNotEqual(b.pick_category(i)[3], b.pick_category(12 + i)[3])           # but a new angle
        self.assertEqual(b.pick_category(0)[3], b.pick_category(12 * len(b.CATEGORIES[0][3]))[3])   # and it wraps

    def test_kinds(self):
        kinds = {k: kind for k, kind, *_ in b.CATEGORIES}
        for k in ("ai_saas_directories", "launch_sites", "saas_review_platforms", "communities_profiles", "startup_directories_by_country"):
            self.assertEqual(kinds[k], "directory")
        for k in ("tool_roundups", "github_awesome_lists", "guest_post_pages", "resource_pages", "podcasts_creators", "journalist_platforms", "edu_library_guides"):
            self.assertEqual(kinds[k], "outreach")


class Run(Base):
    def test_adds_rows_with_the_schema_and_status(self):
        calls = []

        def post(url, body, headers):
            calls.append(body)
            return answer([good(len(calls) * 10 + i) for i in range(3)])
        r, out = self.go(post, **{})
        self.assertEqual(r["status"], "DONE")
        new = [s for s in self.cands() if s.get("added") == TODAY]
        self.assertEqual(len(new), 3 * len(calls))
        for row in new:
            self.assertTrue(ROW_KEYS <= set(row), ROW_KEYS - set(row))
            self.assertIn(row["status"], ("TODO", "TODO outreach"))
            self.assertEqual(row["checked"], TODAY)
            self.assertRegex(row["added"], r"^\d{4}-\d\d-\d\d$")
            self.assertTrue(row["source_query"].startswith(row["category"]))
        by_status = {(r_["category"], r_["status"], r_["usable"]) for r_ in new}
        self.assertIn(("ai_saas_directories", "TODO", True), by_status)               # directory type: the directory agent may try it
        self.assertIn(("tool_roundups", "TODO outreach", False), by_status)           # outreach type: never form-submitted

    def test_dedupe_against_the_whole_file_other_lists_and_itself(self):
        json.dump([{"name": "L", "submit_url": "https://listed-example.com/add"}], open(os.path.join(self.dir, "directories.json"), "w"))
        json.dump({"products": [{"id": "x", "url": "https://inayaflow.ai/"}]}, open(os.path.join(self.dir, "listings.json"), "w"))
        json.dump([{"source": "https://myblog.blogspot.com/2026/11/p.html", "target": "/x"}], open(os.path.join(self.dir, "own_backlinks.json"), "w"))
        extra = os.path.join(self.dir, "ops_known.txt")
        open(extra, "w").write("https://opsknown-example.com\n")
        os.environ["LIST_BUILDER_KNOWN_FILES"] = extra
        self.addCleanup(os.environ.pop, "LIST_BUILDER_KNOWN_FILES", None)
        items = [good(1, url="https://old-example.com/other-page"),                    # same domain as a candidate row
                 good(2, url="https://www.OLD-example.com/"),                           # same, another spelling
                 good(3, url="https://sub.listed-example.com/x"),                       # domain from directories.json
                 good(4, url="https://inayaflow.ai/blog"),                              # our own site
                 good(5, url="https://opsknown-example.com/submit"),                    # domain from the ops file
                 good(6, url="https://fresh-example.com/a"), good(7, url="https://fresh-example.com/b"),   # twice in one answer
                 good(8, url="https://otherblog.blogspot.com/2026/11/other.html"),      # a different blogspot blog: allowed
                 good(9, url="https://myblog.blogspot.com/2026/12/other.html")]         # the same blogspot blog: duplicate
        r, _ = self.go(lambda *a: answer(items), **{})
        got = [s["domain"] for s in self.cands() if s.get("added") == TODAY]
        self.assertEqual(sorted(set(got)), ["fresh-example.com", "otherblog.blogspot.com"])
        self.assertEqual(len(got), len(set(got)))                                       # never twice, also across the calls of one run

    def test_spam_dead_and_paid(self):
        items = [good(1, name="Buy backlinks here", url="https://spam-example.com/"),
                 good(2, url="https://dead-example.com/"),
                 good(3, url="https://paid-example.com/", free="no"),
                 good(4, url="https://paidpage-example.com/", note="paid listing only"),
                 good(5, url="https://redirdead-example.com/"),
                 good(6, url="https://ok-example.com/")]

        def getter(url, timeout=12, limit=0):
            if "dead-example" in url:
                return 404, url, ""
            if "redirdead" in url:
                return 0, url, ""
            if "paidpage" in url:
                return 200, url, "<html>Submission fee: we charge a listing fee</html>" + "x" * 400
            return alive(url)
        self.go(lambda *a: answer(items), getter)
        rows = {s["domain"]: s for s in self.cands() if s.get("added") == TODAY}
        self.assertEqual(sorted(rows), ["ok-example.com", "paid-example.com", "paidpage-example.com"])
        self.assertEqual(rows["paid-example.com"]["status"], "HOLD paid")
        self.assertFalse(rows["paid-example.com"]["usable"])
        self.assertEqual(rows["paidpage-example.com"]["status"], "HOLD paid")
        self.assertEqual(rows["ok-example.com"]["status"], "TODO")
        st = self.state()
        self.assertIn("dead-example.com", st["rejected"])                                # remembered: not paid for twice

    def test_redirect_to_a_known_or_spam_site_is_dropped(self):
        items = [good(1, url="https://alias-example.com/"), good(2, url="https://clean-example.com/")]

        def getter(url, timeout=12, limit=0):
            if "alias" in url:
                return 200, "https://old-example.com/home", "<html>" + "x" * 400
            if "clean" in url:
                return 200, url, "<html>Best casino and betting list " + "x" * 400
            return alive(url)
        self.go(lambda *a: answer(items), getter)
        self.assertEqual([s for s in self.cands() if s.get("added") == TODAY], [])

    def test_dry_run_saves_no_candidates_but_books_the_spend(self):
        b.DRY = True
        before = open(b.CAND_PATH, encoding="utf8").read()
        r, out = self.go(lambda *a: answer([good(1)]))
        self.assertEqual(open(b.CAND_PATH, encoding="utf8").read(), before)
        self.assertGreater(self.state()["spent"][TODAY], 0)
        self.assertIn("dry run", self.state()["last_summary"])

    def test_missing_file_fails_closed(self):
        os.remove(b.CAND_PATH)
        r, out = self.go(lambda *a: answer([good(1)]))
        self.assertEqual(r["status"], "NO_DATA")
        self.assertFalse(os.path.exists(b.STATE_PATH))

    def test_a_failing_call_moves_the_cursor_on_and_never_prints_the_error(self):
        def post(*a):
            raise RuntimeError("secret body " + SECRET)
        r, out = self.go(post)
        self.assertEqual(self.cands().__len__(), 1)
        self.assertGreater(self.state()["cursor"], 1)
        self.assertNotIn(SECRET, out)
        self.assertIn("RuntimeError", out)


class Cap(Base):
    def test_cap_is_never_exceeded_even_when_every_call_is_dear(self):
        n = []

        def post(url, body, headers):
            n.append(1)
            return answer([good(len(n) * 100 + i) for i in range(2)], usage(inp=30000, out=4000, searches=4))   # ~$0.14 each
        for _ in range(3):                                                                # three runs the same day share the cap
            self.go(post)
        spent = self.state()["spent"][TODAY]
        self.assertLessEqual(spent, b.DAY_CAP + 1e-9)
        self.assertGreater(spent + b.RESERVE, b.DAY_CAP - 0.5)                            # the cap really was used (at most one real cost short of it)
        before = len(n)
        self.go(post)
        self.assertEqual(len(n), before)                                                  # cap used: no more calls at all

    def test_reserve_is_saved_before_the_request_goes_out(self):
        seen = []

        def post(url, body, headers):
            seen.append(json.load(open(b.STATE_PATH, encoding="utf8"))["spent"].get(TODAY))
            raise RuntimeError("killed")
        self.go(post)
        self.assertEqual(seen[0], b.RESERVE)                                              # on disk while the request was in flight
        self.assertGreaterEqual(self.state()["spent"][TODAY], b.RESERVE)                  # a failed request keeps its reserve booked

    def test_cap_from_env_default_and_worst_case(self):
        self.assertEqual(b.DAY_CAP, 3.00)
        self.assertLessEqual(b.MAX_CALLS_PER_RUN * 3 * b.RESERVE + 0, 99)                 # (pause_turn continuations are metered too)
        m = b.Meter({"spent": {TODAY: 2.90}}, TODAY)
        self.assertFalse(m.room_for_call())                                               # 2.90 + 0.20 > 3.00
        with self.assertRaises(b.CapReached):
            m.book()

    def test_pause_turn_continuations_are_metered_and_capped(self):
        n = []

        def post(url, body, headers):
            n.append(1)
            return {"content": [{"type": "text", "text": ""}], "stop_reason": "pause_turn", "usage": usage(inp=30000, out=4000, searches=4)}
        st = b.load_state()
        m = b.Meter(st, TODAY)
        b.ask_model("s", "u", m, post=post)
        self.assertLessEqual(len(n), 3)
        self.assertLessEqual(m.spent, b.DAY_CAP + 1e-9)


class Summary(Base):
    def test_summary_line_and_state(self):
        def post(url, body, headers):
            return answer([good(len(self.calls) * 10 + i) for i in range(2)])
        self.calls = []
        counter = iter(range(1000))

        def post(url, body, headers):                                                      # noqa: F811
            k = next(counter)
            return answer([good(k * 10 + i) for i in range(2)])
        r, out = self.go(post)
        line = self.state()["last_summary"]
        m = re.match(r"^list-builder: sonnet \+(\d+) new \((\d+) followed, \$([\d.]+)\)$", line)
        self.assertTrue(m, line)
        self.assertEqual(int(m.group(1)), len(r["rows"]))
        cm = re.match(r"^list-builder: \+(\d+) new \(total (\d+); by category (.+)\)$", self.state()["days"][TODAY]["category_summary"])
        self.assertTrue(cm)
        self.assertEqual(int(cm.group(2)), len(self.cands()))
        self.assertIn(line, out)
        self.assertEqual(self.state()["days"][TODAY]["added"], len(r["rows"]))
        self.assertEqual(b.summary_line(0, 5, {}), "list-builder: +0 new (total 5; by category none)")
        self.assertEqual(b.summary_line(3, 10, {"b": 1, "a": 2}), "list-builder: +3 new (total 10; by category a 2, b 1)")

    def test_second_run_same_day_adds_to_the_day_total(self):
        counter = iter(range(10000))
        post = lambda *a: answer([good(next(counter)) for _ in range(2)])                 # noqa: E731
        r1, _ = self.go(post)
        r2, _ = self.go(post)
        day = self.state()["days"][TODAY]
        self.assertEqual(day["added"], len(r1["rows"]) + len(r2["rows"]))
        self.assertEqual(day["runs"], 2)

    def test_stops_at_the_daily_target(self):
        old = (b.TARGET_NEW, b.MAX_NEW_PER_DAY)
        b.TARGET_NEW, b.MAX_NEW_PER_DAY = 5, 6
        self.addCleanup(lambda: setattr(b, "TARGET_NEW", old[0]) or setattr(b, "MAX_NEW_PER_DAY", old[1]))
        counter = iter(range(10000))
        r, _ = self.go(lambda *a: answer([good(next(counter)) for _ in range(4)]))
        self.assertLessEqual(len(r["rows"]), 6)
        self.assertGreaterEqual(len(r["rows"]), 5)


class NoSecrets(Base):
    def test_nothing_secret_in_stdout_or_state_or_candidates(self):
        os.environ["ANTHROPIC_API_KEY"] = SECRET
        self.addCleanup(os.environ.pop, "ANTHROPIC_API_KEY", None)
        counter = iter(range(10000))
        r, out = self.go(lambda url, body, headers: answer([good(next(counter))]))
        self.assertNotIn(SECRET, out)
        for fn in (b.STATE_PATH, b.CAND_PATH):
            self.assertNotIn(SECRET, open(fn, encoding="utf8").read())
        src = open(os.path.join(HERE, "builder.py"), encoding="utf8").read()
        self.assertNotRegex(src, r"print\([^)]*(environ|ANTHROPIC_API_KEY|headers)")

    def test_missing_key_message_has_no_value(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        r, out = self.go(None)
        self.assertIn("RuntimeError", out)


class Config(unittest.TestCase):
    wf = os.path.join(ROOT, ".github", "workflows", "list-builder.yml")

    def test_workflow(self):
        t = open(self.wf, encoding="utf8").read()
        self.assertIn('cron: "23 4 * * *"', t)
        self.assertIn('cron: "23 16 * * *"', t)
        self.assertIn("workflow_dispatch", t)
        self.assertIn("dry_run:", t)
        self.assertIn('LIST_BUILDER_DAY_USD_CAP: "3.00"', t)
        self.assertIn("$16.98 a day is the hard cap of the LISTED robots", t)
        self.assertIn("group: list-builder", t)
        self.assertIn("cancel-in-progress: false", t)
        self.assertIn("contents: write", t)
        self.assertIn("scripts/list_builder/test_builder.py", t)
        self.assertIn("data/list_builder_state.json", t)
        self.assertIn("merge_candidates.py", t)
        self.assertNotIn("GENIE_LOGS_TOKEN", t)                                           # nothing private is touched

    def test_env_and_code_cap_agree(self):
        t = open(self.wf, encoding="utf8").read()
        self.assertEqual(float(re.search(r'LIST_BUILDER_DAY_USD_CAP: "([\d.]+)"', t).group(1)), b.DAY_CAP)


class FakeDeepSeek:
    """A DeepSeek chat/completions answer for the same JSON lines."""

    def __init__(self, items, usg=None):
        self.items, self.bodies, self.usg = items, [], usg or {"prompt_tokens": 3000, "completion_tokens": 2000}

    def __call__(self, url, body, headers):
        self.bodies.append((url, body))
        text = "LIST DONE\n" + "\n".join(json.dumps(i) for i in self.items) + "\nCOUNT %d" % len(self.items)
        return {"choices": [{"message": {"content": text}}], "usage": self.usg}


class DeepSeekTwin(Base):
    def test_same_prompt_text_in_both_providers(self):
        cat = b.pick_category(3)
        s1, u1 = b.build_prompts("claude", cat[0], cat[2], cat[3], ["a.com"])
        s2, u2 = b.build_prompts("deepseek", cat[0], cat[2], cat[3], ["a.com"])
        self.assertEqual(u1, u2)
        self.assertEqual(s1, b.SYSTEM)
        self.assertTrue(s2.startswith(b.SYSTEM))                                    # the only difference is the appended no-web-search note
        self.assertEqual(s2[len(b.SYSTEM):], b.DEEPSEEK_NOTE)
        self.assertIn("NO web search", b.DEEPSEEK_NOTE)

    def test_dedupe_spam_and_live_check_apply_in_deepseek_mode(self):
        items = [good(1, url="https://old-example.com/other"), good(2, name="Buy backlinks cheap", url="https://spam-example.com/"),
                 good(3, url="https://dead-example.com/"), good(4, url="https://new-example.com/submit")]

        def getter(url, timeout=12, limit=0):
            return (404, url, "") if "dead-example" in url else alive(url)
        ds = FakeDeepSeek(items)
        r, out = self.go(ds, getter, provider="deepseek")
        before = open(b.CAND_PATH, encoding="utf8").read()
        self.assertEqual([s for s in self.cands() if s.get("added") == TODAY], [])     # Sonnet's file is never touched by DeepSeek
        rows = [s for s in self.ds_cands() if s.get("added") == TODAY]
        self.assertEqual({s["domain"] for s in rows}, {"new-example.com"})
        self.assertEqual({s["found_by"] for s in rows}, {"deepseek"})
        self.assertTrue(ROW_KEYS <= set(rows[0]))                                   # same row format as Sonnet rows
        self.assertTrue(ds.bodies[0][0].endswith("/chat/completions"))
        self.assertEqual(ds.bodies[0][1]["model"], b.DEEPSEEK_MODEL)
        self.assertFalse(os.path.exists(b.STATE_PATH))                                   # no shared state file: DeepSeek wrote its own
        pc = self.ds_state()["days"][TODAY]["providers"]["deepseek"]
        n = len(ds.bodies)
        self.assertEqual(pc["spam"], n)
        self.assertGreaterEqual(pc["dup"], n)                                     # old-example.com every call, plus repeats of what was just added or rejected
        self.assertEqual(pc["dead"], n)
        self.assertEqual(pc["added"], 1)

    def test_dedupe_against_what_sonnet_added(self):
        self.go(lambda *a: answer([good(7, url="https://shared-example.com/")]))      # Sonnet first
        ds = FakeDeepSeek([good(8, url="https://shared-example.com/x")])
        self.go(ds, provider="deepseek")
        mine = [s for s in self.cands() + self.ds_cands() if s["domain"] == "shared-example.com"]
        self.assertEqual([s["found_by"] for s in mine], ["sonnet"])                       # DeepSeek dedupes against Sonnet's FILE
        self.assertEqual(self.ds_cands(), [])

    def test_sonnet_dedupes_against_the_deepseek_file(self):
        json.dump({"generated": "2026-11-24", "sites": [{"name": "D", "domain": "dsheld-example.com", "submit_url": "https://dsheld-example.com/add", "found_by": "deepseek"}]},
                  open(b.DS_CAND_PATH, "w"))
        before_ds = open(b.DS_CAND_PATH, encoding="utf8").read()
        self.go(lambda *a: answer([good(1, url="https://dsheld-example.com/other"), good(2, url="https://fresh2-example.com/")]))
        self.assertEqual({s["domain"] for s in self.cands() if s.get("added") == TODAY}, {"fresh2-example.com"})
        self.assertEqual(open(b.DS_CAND_PATH, encoding="utf8").read(), before_ds)           # Sonnet never writes DeepSeek's file

    def test_no_cross_writes_and_separate_sheets(self):
        counter = iter(range(1000))
        self.go(lambda *a: answer([good(next(counter)) for _ in range(2)]), proof=lambda u: ("FOLLOWED", ""))
        sonnet_files = {f: open(os.path.join(self.dir, f), "rb").read() for f in os.listdir(self.dir)}
        self.assertNotIn("directories_candidates_deepseek.json", sonnet_files)
        self.assertNotIn("list_builder_state_deepseek.json", sonnet_files)
        self.assertNotIn("needs_signup_deepseek.md", sonnet_files)
        self.assertNotIn("list_builder_report_deepseek.md", sonnet_files)
        self.go(FakeDeepSeek([good(900, url="https://dsnew-example.com/")]), provider="deepseek", proof=lambda u: ("FOLLOWED", ""))
        for f, content in sonnet_files.items():
            if f != "list_builder_compare.md":                                              # the side-by-side summary is the only shared file
                self.assertEqual(open(os.path.join(self.dir, f), "rb").read(), content, f)   # Sonnet's own files are byte-identical after the DeepSeek run
        self.assertEqual({s["found_by"] for s in self.cands() if s.get("added") == TODAY}, {"sonnet"})
        self.assertEqual({s["found_by"] for s in self.ds_cands()}, {"deepseek"})
        self.assertEqual(list(self.state()["days"][TODAY]["providers"]), ["claude"])
        self.assertEqual(list(self.ds_state()["days"][TODAY]["providers"]), ["deepseek"])

    def test_no_key_exits_zero_with_the_skip_line(self):
        os.environ.pop("DEEPSEEK_API_KEY", None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = b.run(today=TODAY, provider="deepseek", proof=lambda u: ("UNKNOWN", ""))
        self.assertEqual(r["status"], "SKIPPED")
        self.assertIn("list-builder-deepseek: no key, skipped", buf.getvalue())
        self.assertFalse(os.path.exists(b.DS_STATE_PATH))
        env = dict(os.environ, LIST_BUILDER_PROVIDER="deepseek", LIST_BUILDER_DATA=self.dir, DRY_RUN="true")
        env.pop("DEEPSEEK_API_KEY", None)
        import subprocess
        p = subprocess.run([sys.executable, os.path.join(HERE, "builder.py")], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("list-builder-deepseek: no key, skipped", p.stdout)

    def test_deepseek_cap_is_separate_and_enforced(self):
        self.assertEqual(b.DEEPSEEK_DAY_CAP, 1.00)
        son = {"spent": {TODAY: 2.95}, "cursor": 0, "days": {}, "rejected": []}               # Sonnet nearly at its own cap (its own state file)
        dss = {"spent": {}, "cursor": 0, "days": {}, "rejected": []}
        self.assertFalse(b.Meter(son, TODAY).room_for_call())
        self.assertTrue(b.Meter(dss, TODAY, provider="deepseek").room_for_call())           # DeepSeek is unaffected
        dss["spent"][TODAY] = 0.99
        m = b.Meter(dss, TODAY, provider="deepseek")
        self.assertFalse(m.room_for_call())
        with self.assertRaises(b.CapReached):
            m.book()
        json.dump({"cursor": 0, "spent": {TODAY: 0.995}, "days": {}, "rejected": []}, open(b.DS_STATE_PATH, "w"))
        ds = FakeDeepSeek([good(1)])
        self.go(ds, provider="deepseek")
        self.assertEqual(ds.bodies, [])                                                      # cap used: no call at all

    def test_deepseek_cost_uses_the_official_prices(self):
        self.assertAlmostEqual(b.deepseek_cost({"prompt_tokens": 1000000, "completion_tokens": 1000000}), 0.30 + 1.20)
        self.assertAlmostEqual(b.deepseek_cost({"prompt_tokens": 1000000, "prompt_cache_hit_tokens": 1000000}), 0.006)

    def test_deepseek_run_stays_under_its_cap_and_leaves_sonnet_spend_alone(self):
        ds = FakeDeepSeek([good(1)], usg={"prompt_tokens": 100000, "completion_tokens": 4000})    # ~$0.035 a call
        self.go(ds, provider="deepseek")
        st = self.ds_state()
        self.assertLessEqual(st["spent"][TODAY], b.DEEPSEEK_DAY_CAP + 1e-9)
        self.assertGreater(st["spent"][TODAY], 0.2)
        self.assertFalse(os.path.exists(b.STATE_PATH))

    def test_row_fields_found_by_proof_and_needs_signup(self):
        items = [good(1, url="https://signup-example.com/", signup="yes"),
                 good(2, url="https://plain-example.com/"), good(3, url="https://pagesignup-example.com/")]

        def getter(url, timeout=12, limit=0):
            if "pagesignup" in url:
                return 200, url, "<html>Please sign up or register to submit your tool</html>" + "x" * 400
            return alive(url)
        verdicts = {"signup-example.com": "FOLLOWED", "plain-example.com": "NOFOLLOW_ONLY", "pagesignup-example.com": "UNKNOWN"}
        self.go(lambda *a: answer(items), getter, proof=lambda u: (verdicts[b.dedupe_key(u)], "x"))
        rows = {s["domain"]: s for s in self.cands() if s.get("added") == TODAY}
        self.assertEqual(rows["signup-example.com"]["needs_signup"], True)
        self.assertEqual(rows["plain-example.com"]["needs_signup"], False)
        self.assertEqual(rows["pagesignup-example.com"]["needs_signup"], True)
        self.assertEqual({v["found_by"] for v in rows.values()}, {"sonnet"})
        self.assertEqual({d: v["proof"] for d, v in rows.items()}, verdicts)

    def test_proof_limit_leaves_the_rest_unchecked(self):
        old = b.PROOF_MAX_FETCHES
        b.PROOF_MAX_FETCHES = 2
        self.addCleanup(setattr, b, "PROOF_MAX_FETCHES", old)
        calls = []
        counter = iter(range(1000))
        self.go(lambda *a: answer([good(next(counter)) for _ in range(6)]), proof=lambda u: calls.append(u) or ("FOLLOWED", ""))
        rows = [s for s in self.cands() if s.get("added") == TODAY]
        self.assertEqual(sum(1 for s in rows if s["proof"] == "FOLLOWED"), 2 * self.state()["days"][TODAY]["runs"])
        self.assertIn("UNCHECKED", {s["proof"] for s in rows})

    def test_needs_signup_md_content(self):
        items = [good(1, url="https://a-example.com/", signup="yes", name="Alpha"), good(2, url="https://b-example.com/", signup="yes", name="Beta"),
                 good(3, url="https://c-example.com/", signup="yes", name="Gamma"), good(4, url="https://d-example.com/", name="Delta")]
        verdicts = {"a-example.com": "FOLLOWED", "b-example.com": "NOFOLLOW_ONLY", "c-example.com": "UNKNOWN", "d-example.com": "FOLLOWED"}
        n = []

        def post(*a):
            n.append(1)
            return answer(items) if len(n) == 1 else answer([])
        self.go(post, proof=lambda u: (verdicts[b.dedupe_key(u)], ""))
        t = open(b.SIGNUP_PATH, encoding="utf8").read()
        self.assertIn("| Site | URL | Category | Found by | Proof |", t)
        self.assertIn("| Alpha | https://a-example.com/ |", t)
        self.assertIn("| Gamma | https://c-example.com/ |", t)
        self.assertNotIn("Beta", t)                                                  # NOFOLLOW_ONLY: not worth an account
        self.assertNotIn("Delta", t)                                                 # no sign-up needed
        self.assertIn("sonnet | FOLLOWED", t)

    def test_deepseek_has_its_own_signup_sheet(self):
        self.go(FakeDeepSeek([good(1, url="https://dsup-example.com/", signup="yes", name="DsUp")]), provider="deepseek", proof=lambda u: ("FOLLOWED", ""))
        t = open(b.DS_SIGNUP_PATH, encoding="utf8").read()
        self.assertIn("| DsUp | https://dsup-example.com/ |", t)
        self.assertIn("deepseek | FOLLOWED", t)
        self.assertFalse(os.path.exists(b.SIGNUP_PATH))                                     # Sonnet's sheet is not touched

    def test_needs_signup_md_newest_first(self):
        self.write_cands([{"name": "Old", "domain": "o.com", "submit_url": "https://o.com/", "category": "x", "found_by": "sonnet", "proof": "UNKNOWN", "needs_signup": True, "added": "2026-11-01"},
                          {"name": "New", "domain": "n.com", "submit_url": "https://n.com/", "category": "x", "found_by": "deepseek", "proof": "FOLLOWED", "needs_signup": True, "added": "2026-11-20"}])
        b.write_needs_signup(self.cands())
        t = open(b.SIGNUP_PATH, encoding="utf8").read()
        self.assertLess(t.index("| New |"), t.index("| Old |"))

    def test_compare_counters_summary_and_md(self):
        counter = iter(range(1000))
        self.go(lambda *a: answer([good(next(counter)) for _ in range(2)]), proof=lambda u: ("FOLLOWED", ""))
        ds = FakeDeepSeek([good(3, url="https://d1-example.com/"), good(4, url="https://old-example.com/x")])
        r, out = self.go(ds, provider="deepseek", proof=lambda u: ("UNKNOWN", ""))
        pr, pd = self.state()["days"][TODAY]["providers"]["claude"], self.ds_state()["days"][TODAY]["providers"]["deepseek"]
        for k in b.COUNTER_KEYS:
            self.assertIn(k, pr)
            self.assertIn(k, pd)
        self.assertGreater(pr["added"], 0)
        self.assertEqual(pr["followed"], pr["added"])
        self.assertEqual((pd["followed"], pd["unknown"], pd["added"]), (0, pd["added"], pd["added"]))
        self.assertGreaterEqual(pd["dup"], 1)
        self.assertGreater(pd["cost"], 0)
        line = self.ds_state()["last_summary"]                                              # the DeepSeek run prints the side-by-side line
        self.assertRegex(line, r"^list-builder: sonnet \+\d+ new \(\d+ followed, \$[\d.]+\) \| deepseek \+\d+ new \(0 followed, \$[\d.]+\)$")
        self.assertIn(line, out)
        md = open(b.COMPARE_PATH, encoding="utf8").read()
        self.assertEqual(md.count("| %s |" % TODAY), 1)
        self.assertIn("Sonnet", md)
        rs, rd = (open(f, encoding="utf8").read() for f in (b.REPORT_PATH, b.DS_REPORT_PATH))      # one sheet per provider, one row per day
        self.assertIn("| calls | cost | proposed | dead | spam | dup | added | followed | nofollow | noindex | unknown | needs_signup |", rs)
        self.assertEqual(rs.count("| %s |" % TODAY), 1)
        self.assertEqual(rd.count("| %s |" % TODAY), 1)
        self.assertIn("Sonnet", rs.splitlines()[0])
        self.assertIn("DeepSeek", rd.splitlines()[0])
        self.assertIn("| %s | %d | %.2f | %d |" % (TODAY, pd["calls"], pd["cost"], pd["proposed"]), rd)

    def test_no_key_value_in_deepseek_output(self):
        os.environ["DEEPSEEK_API_KEY"] = SECRET
        self.addCleanup(os.environ.pop, "DEEPSEEK_API_KEY", None)

        def boom(*a):
            raise RuntimeError("body " + SECRET)
        r, out = self.go(boom, provider="deepseek")
        self.assertNotIn(SECRET, out)
        for fn in (b.DS_STATE_PATH, b.DS_CAND_PATH):
            if os.path.exists(fn):
                self.assertNotIn(SECRET, open(fn, encoding="utf8").read())


class TwinConfig(unittest.TestCase):
    wf = os.path.join(ROOT, ".github", "workflows", "list-builder-deepseek.yml")

    def test_workflow(self):
        t = open(self.wf, encoding="utf8").read()
        sonnet = open(os.path.join(ROOT, ".github", "workflows", "list-builder.yml"), encoding="utf8").read()
        self.assertIn('cron: "53 4 * * *"', t)
        self.assertIn('cron: "53 16 * * *"', t)
        self.assertIn("group: list-builder\n", t)                                    # same group as the Sonnet job: never in parallel
        self.assertIn("group: list-builder\n", sonnet)
        self.assertIn("LIST_BUILDER_PROVIDER: deepseek", t)
        self.assertIn("DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}", t)
        self.assertIn("list-builder-deepseek: no key, skipped", t)
        self.assertIn('LIST_BUILDER_DEEPSEEK_DAY_USD_CAP: "1.00"', t)
        self.assertIn("dry_run:", t)
        save = t.split("- name: Save candidates and state", 1)[1]
        for f in ("directories_candidates_deepseek.json", "list_builder_state_deepseek.json", "needs_signup_deepseek.md", "list_builder_report_deepseek.md"):
            self.assertIn("data/" + f, save)
        self.assertNotIn("data/directories_candidates.json", save)                          # the DeepSeek job never adds or merges Sonnet's files
        self.assertNotIn("data/list_builder_state.json", save)
        self.assertNotIn("needs_signup_sonnet", save)
        sonnet_save = self.load_sonnet().split("- name: Save candidates and state", 1)[1]
        self.assertNotIn("_deepseek", sonnet_save)                                           # and the Sonnet job never touches DeepSeek's
        self.assertNotIn("ANTHROPIC_API_KEY", t)

    def load_sonnet(self):
        return open(os.path.join(ROOT, ".github", "workflows", "list-builder.yml"), encoding="utf8").read()

    def test_env_and_code_cap_agree(self):
        t = open(self.wf, encoding="utf8").read()
        self.assertEqual(float(re.search(r'LIST_BUILDER_DEEPSEEK_DAY_USD_CAP: "([\d.]+)"', t).group(1)), b.DEEPSEEK_DAY_CAP)


class StateMerge(Base):
    """Code QA 3a: after a push conflict the provider's own state is MERGED, never overwritten (no lost spend booking or cursor)."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, HERE)
        import merge_state as ms
        self.ms = ms

    def test_merge_keeps_the_larger_spend_cursor_and_union(self):
        ours = {"cursor": 40, "spent": {"2026-11-24": 2.10}, "rejected": ["a.com", "b.com"],
                "days": {"2026-11-24": {"added": 100, "runs": 3, "calls": 9, "by_category": {"x": 60, "y": 40}, "summary": "ours",
                                        "providers": {"claude": {"calls": 9, "cost": 2.1, "added": 100, "dead": 5}}}}, "last_summary": "ours"}
        theirs = {"cursor": 45, "spent": {"2026-11-24": 1.50, "2026-11-23": 2.0}, "rejected": ["b.com", "c.com"],
                  "days": {"2026-11-24": {"added": 60, "runs": 4, "calls": 5, "by_category": {"x": 70}, "summary": "theirs",
                                          "providers": {"claude": {"calls": 5, "cost": 1.5, "added": 60, "dead": 9}}}}}
        m = self.ms.merge_state(ours, theirs)
        self.assertEqual(m["cursor"], 45)
        self.assertEqual(m["spent"], {"2026-11-24": 2.10, "2026-11-23": 2.0})              # no booking is ever lost
        self.assertEqual(sorted(m["rejected"]), ["a.com", "b.com", "c.com"])
        d = m["days"]["2026-11-24"]
        self.assertEqual((d["added"], d["runs"], d["calls"]), (100, 4, 9))
        self.assertEqual(d["by_category"], {"x": 70, "y": 40})
        self.assertEqual(d["providers"]["claude"], {"calls": 9, "cost": 2.1, "added": 100, "dead": 9})
        self.assertEqual(d["summary"], "ours")                                              # we ran more calls that day

    def test_cli_merges_the_deepseek_state_in_place_and_rebuilds_its_own_sheets(self):
        json.dump({"sites": [{"name": "N", "domain": "n.com", "submit_url": "https://n.com/", "needs_signup": True, "proof": "FOLLOWED", "added": TODAY}]}, open(b.DS_CAND_PATH, "w"))
        o = os.path.join(self.dir, "o.json")
        json.dump({"cursor": 3, "spent": {TODAY: 1.0}, "days": {TODAY: {"calls": 1, "providers": {"deepseek": {"calls": 1}}}}}, open(o, "w"))
        json.dump({"cursor": 9, "spent": {TODAY: 0.5}, "days": {}}, open(b.DS_STATE_PATH, "w"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(self.ms.main(["x", o, b.DS_STATE_PATH]), 0)
        m = json.load(open(b.DS_STATE_PATH))
        self.assertEqual((m["cursor"], m["spent"][TODAY]), (9, 1.0))
        self.assertIn("| N | https://n.com/ |", open(b.DS_SIGNUP_PATH, encoding="utf8").read())
        self.assertIn(TODAY, open(b.DS_REPORT_PATH, encoding="utf8").read())
        self.assertIn(TODAY, open(b.COMPARE_PATH, encoding="utf8").read())
        self.assertFalse(os.path.exists(b.STATE_PATH))                                      # Sonnet's files are not created or touched
        self.assertFalse(os.path.exists(b.SIGNUP_PATH))


class WorkflowParity(unittest.TestCase):
    """Code QA 4 and 3e: the DeepSeek twin gets EXACTLY the Sonnet job's checkout, env and save step (except provider, key and cap)."""
    wfdir = os.path.join(ROOT, ".github", "workflows")

    def load(self, f):
        return open(os.path.join(self.wfdir, f), encoding="utf8").read()

    def build_env(self, t):
        block = t.split("- name: Build the list", 1)[1].split("run: |", 1)[0].split("env:", 1)[1]
        return {m.group(1): m.group(2) for m in re.finditer(r"^\s+([A-Z_]+): (.+?)\s*(?:#.*)?$", block, re.M)}

    def test_same_env_checkout_and_save_step(self):
        s, d = self.load("list-builder.yml"), self.load("list-builder-deepseek.yml")
        es, ed = self.build_env(s), self.build_env(d)
        es.pop("ANTHROPIC_API_KEY"), ed.pop("DEEPSEEK_API_KEY")
        self.assertEqual(es.pop("LIST_BUILDER_DAY_USD_CAP"), '"3.00"')
        self.assertEqual(ed.pop("LIST_BUILDER_DEEPSEEK_DAY_USD_CAP"), '"1.00"')
        self.assertEqual(ed.pop("LIST_BUILDER_PROVIDER"), "deepseek")
        self.assertEqual(es, ed)                                                            # every other variable (DRY_RUN, any KNOWN_FILES) is identical
        for part in ("- uses: actions/checkout@v4\n        with:\n          ref: main", 'python-version: "3.12"'):
            self.assertIn(part, s)
            self.assertIn(part, d)
        norm = lambda t: re.sub(r"_?(deepseek|sonnet)", "", t.split("- name: Save candidates and state", 1)[1])      # noqa: E731
        self.assertEqual(norm(s), norm(d))                                                  # the save step is identical except the file names of each provider
        self.assertEqual(s.split("- name: Unit tests", 1)[1].split("- name: Build", 1)[0], d.split("- name: Unit tests", 1)[1].split("- name: Build", 1)[0])

    def test_save_step_is_safe(self):
        for f in ("list-builder.yml", "list-builder-deepseek.yml"):
            t = self.load(f)
            for m in re.finditer(r"cp /tmp/ours_state.json", t):
                self.assertTrue(t[:m.start()].rstrip().endswith("else"), f)                   # 3a: never copy our state over theirs (only when theirs has no file)
            self.assertRegex(t, r"merge_state\.py /tmp/ours_state\.json data/list_builder_state(_deepseek)?\.json")
            self.assertNotIn("git add data/directories_candidates", t)                       # 3e: only existing files are added
            self.assertIn('if [ -f "$f" ]; then git add "$f"; fi', t)
            self.assertIn("data file unreadable, nothing saved", t)                          # 3b: a damaged file is never committed


class Misc(Base):
    def test_cap_env_parse_error_falls_back_to_the_default(self):
        os.environ["LB_TEST_CAP"] = "three dollars"
        self.addCleanup(os.environ.pop, "LB_TEST_CAP", None)
        self.assertEqual(b._env_float("LB_TEST_CAP", "3.00"), 3.0)
        os.environ["LB_TEST_CAP"] = ""
        self.assertEqual(b._env_float("LB_TEST_CAP", "1.00"), 1.0)
        os.environ["LB_TEST_CAP"] = "2.5"
        self.assertEqual(b._env_float("LB_TEST_CAP", "1.00"), 2.5)

    def test_writes_are_atomic_temp_then_rename(self):
        src = open(os.path.join(HERE, "builder.py"), encoding="utf8").read()
        self.assertEqual(src.count("os.replace(tmp, path)"), 2)                                # write_json and write_text
        b.write_text(os.path.join(self.dir, "x.md"), "hi")
        self.assertEqual(os.listdir(self.dir).count("x.md.tmp"), 0)

    def test_proof_loading_and_checking_print_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fp = b.load_follow_proof()
            noisy = lambda u: print("LEAK") or ("UNKNOWN", "")                                 # noqa: E731
            rows = [{"submit_url": "https://x-example.com/", "final_url": "https://x-example.com/", "proof": "UNCHECKED"}]
            b.run_proofs(rows, noisy)
        self.assertEqual(buf.getvalue(), "")
        self.assertTrue(fp is None or callable(fp))
        agent_src = open(os.path.join(ROOT, "scripts", "directory_agent", "agent.py"), encoding="utf8").read()
        self.assertFalse(re.search(r"^print\(", agent_src, re.M))                              # no top-level print in the module we import

    def test_other_robots_ignore_the_new_row_keys_and_merge_keeps_them(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts", "reports"))
        import merge_candidates as mc
        ours = {"generated": "2026-11-24", "sites": [{"domain": "d.com", "name": "D", "submit_url": "https://d.com/", "checked": "2026-11-24", "status": "TODO",
                                                      "found_by": "deepseek", "proof": "FOLLOWED", "needs_signup": True}]}
        theirs = {"generated": "2026-11-23", "sites": [{"domain": "d.com", "name": "D", "submit_url": "https://d.com/", "checked": "2026-11-24", "status": "TODO",
                                                        "found_by": "sonnet", "proof": "UNCHECKED", "needs_signup": False},
                                                       {"domain": "e.com", "name": "E", "submit_url": "https://e.com/", "checked": "2026-11-20", "status": "TODO"}]}
        m = mc.merge(ours, theirs)
        rows = {s["domain"]: s for s in m["sites"]}
        self.assertEqual(len(m["sites"]), 2)                                                   # one row per domain, nothing lost
        self.assertEqual(rows["d.com"]["found_by"], "deepseek")                                # same date, same status: OUR row wins, extra keys travel with the row
        self.assertEqual(rows["d.com"]["proof"], "FOLLOWED")
        self.assertNotIn("found_by", rows["e.com"])                                            # old rows without the keys stay valid
        for fn in ("scripts/directory_agent/agent.py", "scripts/reports/backlink_batch.py", "scripts/reports/hunter_ingest.py", "scripts/link_checker/check.py", "scripts/directory_verify/verify.py"):
            t = open(os.path.join(ROOT, fn), encoding="utf8").read()
            self.assertNotIn("found_by", t)                                                    # they never read or require the new keys
            self.assertNotIn("needs_signup", t)


class QA3Notes(Base):
    """Code QA round 3 notes (24 Nov 2026)."""

    def test_deepseek_starts_empty_only_when_its_file_does_not_exist(self):
        self.assertFalse(os.path.exists(b.DS_CAND_PATH))
        self.go(FakeDeepSeek([good(1, url="https://first-example.com/")]), provider="deepseek")
        self.assertEqual([s["domain"] for s in self.ds_cands()], ["first-example.com"])          # missing file: created by the run

    def test_deepseek_fails_closed_on_a_corrupt_file_and_does_not_touch_it(self):
        open(b.DS_CAND_PATH, "w").write("{ this is not json")
        ds = FakeDeepSeek([good(1)])
        r, out = self.go(ds, provider="deepseek")
        self.assertEqual(r["status"], "NO_DATA")
        self.assertIn("fail closed", out)
        self.assertEqual(ds.bodies, [])                                                          # no call, no spend
        self.assertEqual(open(b.DS_CAND_PATH).read(), "{ this is not json")                      # old rows never overwritten
        json.dump({"generated": "x"}, open(b.DS_CAND_PATH, "w"))                                 # valid JSON but no "sites" list: also fail closed
        self.assertEqual(self.go(FakeDeepSeek([good(1)]), provider="deepseek")[0]["status"], "NO_DATA")

    def test_sonnet_with_a_corrupt_deepseek_file_goes_on_and_warns(self):
        open(b.DS_CAND_PATH, "w").write("not json")
        r, out = self.go(lambda *a: answer([good(5, url="https://sonnetok-example.com/")]))
        self.assertEqual(r["status"], "DONE")
        self.assertIn("the DeepSeek candidates file is unreadable", out)
        self.assertNotIn("not json", out)                                                        # count-only line, never the content
        self.assertEqual(open(b.DS_CAND_PATH).read(), "not json")                                # not touched

    def test_missing_usage_keeps_the_reserve_as_the_cost(self):
        for prov, reserve in (("claude", b.RESERVE), ("deepseek", b.DS_RESERVE)):
            for usage in (None, {}):
                st = {"spent": {}}
                m = b.Meter(st, TODAY, provider=prov)
                m.book()
                m.settle(usage)
                self.assertAlmostEqual(m.spent, reserve, places=4, msg=(prov, usage))
            st = {"spent": {}}
            m = b.Meter(st, TODAY, provider=prov)
            m.book()
            m.settle({"input_tokens": 10, "output_tokens": 10, "prompt_tokens": 10, "completion_tokens": 10})
            self.assertLess(m.spent, reserve)                                                    # a real usage block still replaces the reserve
        # end to end: an answer with no usage block
        self.go(lambda *a: {"content": [{"type": "text", "text": "LIST DONE\nCOUNT 0"}], "stop_reason": "end_turn"})
        self.assertGreaterEqual(self.state()["spent"][TODAY], b.RESERVE)
        self.go(lambda *a: {"choices": [{"message": {"content": "LIST DONE\nCOUNT 0"}}]}, provider="deepseek")
        self.assertGreaterEqual(self.ds_state()["spent"][TODAY], b.DS_RESERVE)

    def test_proof_coverage_default_and_unchecked_rows_on_the_signup_sheet(self):
        self.assertEqual(b.PROOF_MAX_FETCHES, 120)
        sites = [{"name": "Un", "domain": "u.com", "submit_url": "https://u.com/", "category": "x", "found_by": "sonnet", "proof": "UNCHECKED", "needs_signup": True, "added": TODAY},
                 {"name": "No", "domain": "n.com", "submit_url": "https://n.com/", "category": "x", "found_by": "sonnet", "proof": "NOFOLLOW_ONLY", "needs_signup": True, "added": TODAY}]
        b.write_needs_signup(sites)
        t = open(b.SIGNUP_PATH, encoding="utf8").read()
        self.assertIn("| Un | https://u.com/ | x | sonnet | UNCHECKED (proof not checked yet) |", t)
        self.assertNotIn("| No |", t)

    def test_most_rows_get_a_verdict_with_the_new_default(self):
        counter = iter(range(2000, 3000))
        calls = []
        self.go(lambda *a: answer([good(next(counter)) for _ in range(8)]), proof=lambda u: calls.append(u) or ("UNKNOWN", ""))
        rows = [s for s in self.cands() if s.get("added") == TODAY]
        self.assertGreater(len(rows), 50)
        self.assertEqual(len(calls), len(rows))                                                  # under 120 rows: every one is checked
        self.assertEqual({s["proof"] for s in rows}, {"UNKNOWN"})

    def test_agent_module_loads_without_browser_use_so_proof_checks_run(self):
        import subprocess
        code = ("import sys; sys.modules['browser_use'] = None; sys.modules['pydantic'] = None; sys.path.insert(0, %r); import builder\n"
                "fp = builder.load_follow_proof(); assert callable(fp), 'follow_proof not loaded'\n"
                "v = fp('http://127.0.0.1:9/')[0]; assert v in ('FOLLOWED','NOFOLLOW_ONLY','NOINDEX','UNKNOWN'), v\n"
                "print('LOADED')") % HERE
        p = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertEqual(p.stdout.strip(), "LOADED")                                             # nothing else printed

    def test_stale_target_page_comments_are_fixed(self):
        wf = open(os.path.join(ROOT, ".github", "workflows", "backlink-batch.yml"), encoding="utf8").read()
        self.assertNotIn("rotates over 5 target pages", wf)
        self.assertIn("17 target pages", wf)
        self.assertNotIn("the 5 target pages", open(os.path.join(ROOT, "scripts", "reports", "backlink_batch.py"), encoding="utf8").read())


class KnownSites(unittest.TestCase):
    def test_known_sites_domains_are_deduped(self):
        keys = b.known_keys({"sites": []})
        for dom in ("awesomeindie.com", "apilist.fun", "saashub.com"):
            self.assertIn(dom, keys)


if __name__ == "__main__":
    unittest.main()
