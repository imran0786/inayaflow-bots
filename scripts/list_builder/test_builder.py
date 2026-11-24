"""Offline tests for the list-builder robot (24 Nov 2026). No network, no secrets."""
import contextlib, io, json, os, re, shutil, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..")
sys.path.insert(0, HERE)
import builder as b

TODAY = "2026-11-24"
SECRET = "sk-ant-TESTSECRET-12345"
ROW_KEYS = {"name", "domain", "submit_url", "source", "their_category", "their_auto_flag", "their_notes", "their_status", "http", "final_url",
            "free_check", "captcha_words_on_page", "has_form_on_page", "quality", "usable", "followed", "checked", "status", "category", "added", "source_query"}


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
        self.saved = (b.DATA, b.CAND_PATH, b.STATE_PATH, b.DRY)
        b.DATA = self.dir
        b.CAND_PATH = os.path.join(self.dir, "directories_candidates.json")
        b.STATE_PATH = os.path.join(self.dir, "list_builder_state.json")
        b.DRY = False
        self.write_cands([{"name": "Old", "domain": "old-example.com", "submit_url": "https://old-example.com/submit", "final_url": "https://www.old-example.com/",
                           "status": "TODO", "checked": "2026-11-20"}])

    def tearDown(self):
        b.DATA, b.CAND_PATH, b.STATE_PATH, b.DRY = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_cands(self, sites):
        json.dump({"generated": "2026-11-23", "sites": sites}, open(b.CAND_PATH, "w", encoding="utf8"))

    def cands(self):
        return json.load(open(b.CAND_PATH, encoding="utf8"))["sites"]

    def state(self):
        return json.load(open(b.STATE_PATH, encoding="utf8"))

    def go(self, post, getter=alive, **kw):
        buf = io.StringIO()
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
        m = re.match(r"^list-builder: \+(\d+) new \(total (\d+); by category (.+)\)$", line)
        self.assertTrue(m, line)
        self.assertEqual(int(m.group(1)), len(r["rows"]))
        self.assertEqual(int(m.group(2)), len(self.cands()))
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


if __name__ == "__main__":
    unittest.main()
