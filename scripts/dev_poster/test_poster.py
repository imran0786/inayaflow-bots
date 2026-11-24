"""Offline tests for the DEV poster. No network, no keys. Run: python3 scripts/dev_poster/test_poster.py"""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["DEV_OPS_DIR"] = tempfile.mkdtemp()
os.environ["DRY_RUN"] = "true"
os.environ["DEV_STATE_OK"] = "true"
os.environ["DEV_POSTER_LIVE"] = "true"
import poster as P  # noqa: E402
REAL_PICK = P.pick_page

URL = "https://inayaflow.ai/tools/tiktok-hashtag-generator"
SRC1, SRC2 = "https://support.example.org/a", "https://research.example.org/b"
PAGE = "Free TikTok hashtag generator. Type a topic and get up to 5 hashtags. No account needed. " * 4
SRC_TEXT = {SRC1: "TikTok allows 5 hashtags per post. In 2025 the limit was 100 characters for captions of 4000 length.", SRC2: "Studies found 12% more reach for tidy captions."}
FILLER = ("Creators often ask how to plan a caption before they film. We usually start by listing the three words a viewer would type into search, "
          "then we cut everything else. A short caption keeps the eye on the video and leaves room for the tags. ")


def good_body():
    para = FILLER * 1
    parts = ["## Start with the viewer\n\n" + para * 2,
             "TikTok allows [5 hashtags per post](%s) so pick them with care. " % SRC1 + para,
             "One study found [12%% more reach](%s) for tidy captions. " % SRC2 + para,
             "When you need ideas fast, a [InayaFlow's free hashtag generator](%s) can turn a topic into a short list. " % URL + para,
             "## A simple routine\n\n" + para * 4, para * 4]
    return "\n\n".join(parts)


def art(**kw):
    a = {"title": "How to plan TikTok captions", "description": "d", "tags": ["tiktok", "socialmedia", "webdev", "productivity"],
         "sources": [SRC1, SRC2], "body": good_body()}
    a.update(kw)
    return a


def probs(a):
    return P.check_article(a, URL, PAGE, SRC_TEXT)


class Gate(unittest.TestCase):
    def test_good_passes(self):
        a = art()
        wc = len(P.words(a["body"]))
        self.assertTrue(P.MIN_WORDS <= wc <= P.MAX_WORDS, wc)
        self.assertEqual(probs(a), [])

    def test_unsourced_number_refused(self):
        a = art(body=good_body() + "\n\nMost creators see 47% growth in a month.\n")
        self.assertTrue(any("47" in x for x in probs(a)))

    def test_number_not_in_source_refused(self):
        a = art(body=good_body().replace("12% more reach", "63% more reach"))
        self.assertTrue(any("63" in x and "not found" in x for x in probs(a)))

    def test_missing_link_refused(self):
        a = art(body=good_body().replace("[InayaFlow's free hashtag generator](%s)" % URL, "a free hashtag generator"))
        self.assertTrue(any("target link missing" in x for x in probs(a)))

    def test_repeated_or_extra_links_refused(self):
        b = good_body() + "\n\nAlso [here](%s) and [there](https://inayaflow.ai/tools) and [again](https://inayaflow.ai/blog/x).\n" % URL
        self.assertTrue(any("more than 2" in x or "repeated" in x for x in probs(art(body=b))))

    def test_overlap_refused(self):
        copied = "Type a topic and get up to 5 hashtags and no account needed at all."
        a = art(body=good_body() + "\n\n" + copied)
        self.assertTrue(any("overlap" in x for x in probs(a)))

    def test_banned_handle_first_person_title(self):
        self.assertTrue(any("banned" in x for x in probs(art(body=good_body() + "\n\nThis is a game-changer.\n"))))
        self.assertTrue(any("@handle" in x for x in probs(art(body=good_body() + "\n\nSee @somecreator.\n"))))
        self.assertTrue(any("first person" in x for x in probs(art(body=good_body() + "\n\nI think so.\n"))))
        self.assertTrue(any("title" in x for x in probs(art(title="x" * 71))))

    def test_unopened_source_refused(self):
        P_ = P.check_article(art(), URL, PAGE, {SRC1: "", SRC2: SRC_TEXT[SRC2]})
        self.assertTrue(any("could not be opened" in x for x in P_))

    def test_link_rel(self):
        self.assertEqual(P.link_rel('<a href="%s" rel="noopener noreferrer">x</a>' % URL, URL), "followed")
        self.assertEqual(P.link_rel('<a href="%s" rel="nofollow ugc">x</a>' % URL, URL), "nofollow")
        self.assertEqual(P.link_rel("<a href='z'>", URL), "missing")


class Rotation(unittest.TestCase):
    def test_never_repeats_within_14_days(self):
        state = {"posts": []}
        d0 = __import__("datetime").date(2026, 12, 1)
        seen = {}
        for i in range(60):
            day = (d0 + __import__("datetime").timedelta(days=i)).isoformat()
            p = REAL_PICK(state, day)
            self.assertIsNotNone(p)
            if p[0] in seen:
                self.assertGreaterEqual(i - seen[p[0]], P.REST_DAYS)
            seen[p[0]] = i
            state["posts"].append({"slug": p[0], "date": day})

    def test_all_resting_returns_none(self):
        state = {"posts": [{"slug": p[0], "date": "2026-12-01"} for p in P.PAGES]}
        self.assertIsNone(REAL_PICK(state, "2026-12-05"))

    def test_nine_tools_and_hubs_present(self):
        self.assertEqual(len([p for p in P.PAGES if p[2] == "tool"]), 9)
        self.assertTrue(any(p[2] == "guide" for p in P.PAGES))


def fake_ai_factory(body, verdict="PASS"):
    text = "TITLE: How to plan TikTok captions\nDESCRIPTION: d\nTAGS: tiktok,socialmedia,webdev,productivity\nSOURCES: %s | %s\n===BODY===\n%s" % (SRC1, SRC2, body)
    calls = []

    def post(url, b, h):
        calls.append(b["model"])
        if b["model"] == P.QA_MODEL:
            return {"content": [{"type": "text", "text": json.dumps({"verdict": verdict, "reasons": ["bad"] if verdict == "FAIL" else []})}], "usage": {"input_tokens": 4000, "output_tokens": 50}}
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 20000, "output_tokens": 2000, "server_tool_use": {"web_search_requests": 3}}}
    return post, calls


def none_published(url, headers):
    return []


def fake_get(url, timeout=15, limit=0):
    if url == SRC1:
        return 200, url, "<p>%s</p>" % SRC_TEXT[SRC1]
    if url == SRC2:
        return 200, url, "<p>%s</p>" % SRC_TEXT[SRC2]
    return 200, url, "<html><body><p>%s</p></body></html>" % PAGE


class Flow(unittest.TestCase):
    def setUp(self):
        P.pick_page = lambda s, d, pages=P.PAGES: P.PAGES[3]   # hashtag-generator = URL used by the fake article
        for f in (P.STATE_PATH,):
            if os.path.exists(f):
                os.remove(f)
        os.environ["DEV_STATE_OK"] = "true"
        os.environ["DEV_FIRST_RUN"] = "true"          # tests start from an empty state: that is only allowed on a first run
        os.environ["DEV_POSTER_LIVE"] = "true"
        os.environ["DEV_API_KEY"] = "SECRET-KEY-VALUE"
        P.DRY = False

    def tearDown(self):
        P.pick_page = REAL_PICK                                   # never leak the patched picker into other tests
        P.DRY = True
        os.environ["DEV_STATE_OK"] = "true"
        os.environ["DEV_FIRST_RUN"] = "true"
        os.environ["DEV_POSTER_LIVE"] = "true"

    def test_dry_run_never_calls_publish(self):
        post_ai, calls = fake_ai_factory(good_body())

        def boom(*a, **k):
            raise AssertionError("publish endpoint called in a dry run")
        os.environ["DEV_API_KEY"] = "SECRET-KEY-VALUE"
        P.DRY = True
        self.assertEqual(P.run(post_ai=post_ai, post_dev=boom, getter=fake_get, sleeper=lambda s: None), "DRY_OK")
        self.assertEqual(P.read_json(P.STATE_PATH, {"posts": []})["posts"], [])

    def test_qa_fail_not_published_and_draft_saved(self):
        post_ai, _ = fake_ai_factory(good_body(), verdict="FAIL")
        P.DRY = False
        os.environ["DEV_API_KEY"] = "SECRET-KEY-VALUE"
        called = []
        self.assertEqual(P.run(post_ai=post_ai, post_dev=lambda *a, **k: called.append(1), getter=fake_get, sleeper=lambda s: None, lister=none_published), "FAIL")
        self.assertEqual(called, [])
        self.assertTrue(any(f.endswith(".md") for f in os.listdir(P.DRAFT_DIR)))

    def test_unsourced_article_not_published(self):
        post_ai, _ = fake_ai_factory(good_body() + "\n\nWe saw 900% growth.\n")
        P.DRY = False
        os.environ["DEV_API_KEY"] = "SECRET-KEY-VALUE"
        called = []
        self.assertEqual(P.run(post_ai=post_ai, post_dev=lambda *a, **k: called.append(1), getter=fake_get, sleeper=lambda s: None, lister=none_published), "FAIL")
        self.assertEqual(called, [])

    def test_publish_payload_records_link_and_no_secret_printed(self):
        post_ai, _ = fake_ai_factory(good_body())
        P.DRY = False
        os.environ["DEV_API_KEY"] = "SECRET-KEY-VALUE"
        sent = {}

        def post_dev(url, body, headers):
            sent.update(url=url, body=body, headers=headers)
            return {"url": "https://dev.to/inayaflow/x-1", "id": 7}

        def getter(url, **k):
            if url == "https://dev.to/inayaflow/x-1":
                return 200, url, '<a href="%s" rel="noopener noreferrer">g</a>' % URL
            return fake_get(url)
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = P.run(post_ai=post_ai, post_dev=post_dev, getter=getter, sleeper=lambda s: None, lister=none_published)
        self.assertEqual(res, "PUBLISHED")
        self.assertEqual(sent["url"], "https://dev.to/api/articles")
        self.assertEqual(sent["headers"]["accept"], "application/vnd.forem.api-v1+json")
        self.assertNotIn("canonical_url", sent["body"]["article"])
        self.assertEqual(len(sent["body"]["article"]["tags"]), 4)
        st = P.read_json(P.STATE_PATH, {})
        self.assertEqual(st["posts"][0]["rel"], "followed")
        self.assertNotIn("SECRET-KEY-VALUE", buf.getvalue() + open(P.LOG_PATH, encoding="utf8").read())
        # second run the same day does nothing
        self.assertEqual(P.run(post_ai=post_ai, post_dev=post_dev, getter=getter, sleeper=lambda s: None, lister=none_published), "ALREADY_DONE")

    def test_cost_cap_and_estimate(self):
        post_ai, _ = fake_ai_factory(good_body())
        m = P.Meter()
        P.anthropic(P.WRITER, "s", "u", m, post=post_ai)
        self.assertLess(m.cost, P.USD_CAP)


import datetime as _dt, urllib.error as _ue, io as _io, contextlib as _cl


def today():
    return P.utc_today()


def run_quiet(**kw):
    kw.setdefault("sleeper", lambda s: None)
    kw.setdefault("getter", fake_get)
    kw.setdefault("lister", none_published)
    with _cl.redirect_stdout(_io.StringIO()):
        return P.run(**kw)


class FlowFailures(unittest.TestCase):
    """QA round 3/4 gaps: state missing, crash after the POST, DEV errors, caps, retries, switch."""

    def setUp(self):
        Flow.setUp(self)
        for d in (P.DRAFT_DIR,):
            if os.path.isdir(d):
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))

    def tearDown(self):
        Flow.tearDown(self)

    def test_state_not_restored_means_no_run_and_no_money(self):
        os.environ["DEV_STATE_OK"] = "false"
        post_ai, calls = fake_ai_factory(good_body())
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=lambda *a, **k: self.fail("published")), "NO_STATE")
        self.assertEqual(calls, [])

    def test_a_call_that_raised_is_charged_its_reserve_and_saved(self):
        def timeout(url, b, h):
            raise TimeoutError("read timed out")                   # may or may not have been billed
        self.assertEqual(run_quiet(post_ai=timeout), "FAIL")
        st = P.read_json(P.STATE_PATH, {})
        self.assertGreaterEqual(st["spent"][today()], P.WRITER_RESERVE)       # the writer call's reserve is booked
        m = P.Meter()
        with self.assertRaises(TimeoutError):
            P.anthropic(P.QA_MODEL, "s", "u", m, post=timeout)
        self.assertEqual((m.cost, m.calls), (P.QA_RESERVE, 1))
        # repeated timeouts cannot spend past the day cap: the cost check sees the booked reserves
        P.write_json(P.STATE_PATH, {"posts": [], "attempts": {}, "spent": {today(): 0.25}})
        self.assertEqual(run_quiet(post_ai=timeout), "FAIL")
        self.assertLessEqual(P.read_json(P.STATE_PATH, {})["spent"][today()], P.DAY_CAP)

    def test_state_file_missing_after_the_branch_exists_stops_the_run(self):
        os.environ.pop("DEV_FIRST_RUN", None)
        post_ai, calls = fake_ai_factory(good_body())
        if os.path.exists(P.STATE_PATH):
            os.remove(P.STATE_PATH)
        self.assertEqual(run_quiet(post_ai=post_ai), "NO_STATE")
        self.assertEqual(calls, [])

    def test_state_file_unreadable_or_wrong_shape_stops_the_run(self):
        os.environ.pop("DEV_FIRST_RUN", None)
        post_ai, calls = fake_ai_factory(good_body())
        for content in ("{not json", "[1, 2]", '{"posts": "x"}', ""):
            open(P.STATE_PATH, "w").write(content)
            self.assertEqual(run_quiet(post_ai=post_ai), "NO_STATE", content)
        self.assertEqual(calls, [])
        # a damaged file is NOT replaced by an empty one
        self.assertEqual(open(P.STATE_PATH).read(), "")

    def test_first_run_flag_alone_lets_an_empty_state_start(self):
        os.environ["DEV_FIRST_RUN"] = "true"
        if os.path.exists(P.STATE_PATH):
            os.remove(P.STATE_PATH)
        P.DRY = True
        post_ai, _ = fake_ai_factory(good_body())
        self.assertEqual(run_quiet(post_ai=post_ai), "DRY_OK")

    def test_workflow_separates_absent_branch_from_damaged_state(self):
        w = open(os.path.join(HERE, "..", "..", ".github", "workflows", "dev-poster.yml"), encoding="utf8").read()
        self.assertEqual(w.count("DEV_FIRST_RUN=true"), 1)         # only the "branch absent" arm
        self.assertNotIn("git ls-remote", w)                           # the plain clone is the check (ls-remote failed on the first real run)
        arm_ok = w.split('if [ "$rc" = "0" ]; then')[1].split('elif [ "$rc" = "10" ]')[0]
        self.assertIn("json.load(open('/tmp/logs/ops/dev_poster_state.json'))", arm_ok)      # a restored branch must carry a readable state file
        self.assertIn("state file is missing or unreadable: no run", arm_ok)
        self.assertNotIn("DEV_FIRST_RUN", arm_ok)

    def test_a_killed_run_has_its_spend_and_its_dry_count_saved(self):
        P.DRY = True
        first = {"content": [{"type": "text", "text": "x"}], "stop_reason": "pause_turn", "usage": {"input_tokens": 40000, "output_tokens": 3000}}     # ~$0.11
        calls = []

        def post(url, b, h):
            calls.append(1)
            if len(calls) == 1:
                return first
            raise KeyboardInterrupt()                              # the runner kills the process during the 2nd writer call
        with self.assertRaises(KeyboardInterrupt):
            run_quiet(post_ai=post)
        st = P.read_json(P.STATE_PATH, {})
        self.assertEqual(st["dry"][today()], 1)                    # counted BEFORE the first model call
        self.assertGreaterEqual(st["spent"][today()], 0.11 + P.WRITER_RESERVE - 0.01)      # 1st call metered + the 2nd call's reserve already booked
        self.assertLessEqual(st["spent"][today()], P.DAY_CAP)
        # three kills in a row cannot spend past the day: the next run starts from what the file says
        for _ in range(2):
            try:
                run_quiet(post_ai=post)
            except KeyboardInterrupt:
                pass
        self.assertLessEqual(P.read_json(P.STATE_PATH, {})["spent"][today()], P.DAY_CAP)

    def test_killed_during_the_very_first_call_still_books_the_reserve_and_the_attempt(self):
        def post(url, b, h):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            run_quiet(post_ai=post)                                 # live run (P.DRY False in Flow.setUp)
        st = P.read_json(P.STATE_PATH, {})
        self.assertEqual(st["attempts"][today()], 1)
        self.assertGreaterEqual(st["spent"][today()], P.WRITER_RESERVE)

    def test_the_metered_cost_replaces_the_reserve_after_a_good_call(self):
        m = P.Meter()
        post_ai, _ = fake_ai_factory(good_body())
        seen = []
        m.hook = lambda: seen.append(round(m.cost, 4))
        P.anthropic(P.WRITER, "s", "u", m, post=post_ai)
        self.assertEqual(seen[0], P.WRITER_RESERVE)                 # booked before the request
        self.assertLess(seen[-1], P.WRITER_RESERVE)                 # then replaced by the real (smaller) cost
        self.assertEqual(m.calls, 1)

    def test_job_time_fits_the_worst_writer_path_and_the_save_step(self):
        w = open(os.path.join(HERE, "..", "..", ".github", "workflows", "dev-poster.yml"), encoding="utf8").read()
        import re as _re
        job = int(_re.search(r"timeout-minutes: (\d+)\s+# worst writer", w).group(1))
        step = int(_re.search(r"timeout-minutes: (\d+)\s+# shorter than the job", w).group(1))
        worst_writer_min = 6 * 240 / 60                           # 6 requests x the 240 s socket timeout in _post_json
        self.assertGreater(step, worst_writer_min + 5)             # + QA, page and source fetches
        self.assertGreaterEqual(job - step, 5)                     # the save step always has time

    def test_save_step_retries_and_never_creates_an_orphan_when_the_branch_exists(self):
        w = open(os.path.join(HERE, "..", "..", ".github", "workflows", "dev-poster.yml"), encoding="utf8").read()
        save = w.split("Save state, log and drafts")[1]
        arm_exists = save.split('if [ "$rc" = "0" ]; then')[1].split('elif [ "$rc" = "10" ]')[0]
        self.assertNotIn("--orphan", arm_exists)
        arm_absent = save.split('elif [ "$rc" = "10" ]; then')[1].split("else")[0]
        self.assertIn("--orphan", arm_absent)                       # only when the clone worked and the branch is absent
        arm_bad = save.split('elif [ "$rc" = "10" ]; then')[1].split("\n          else\n")[1]
        self.assertIn("exit 1", arm_bad)
        self.assertEqual(save.count("--orphan"), 1)
        self.assertIn("for i in 1 2 3 4; do", save)
        self.assertIn("git pull -q --rebase origin ops-dev-poster", save)
        self.assertIn('[ "$ok" = "1" ] ||', save)
        self.assertIn("NOT saving", save)

    def test_no_dev_key_stops_before_any_spend(self):
        os.environ.pop("DEV_API_KEY", None)
        post_ai, calls = fake_ai_factory(good_body())
        self.assertEqual(run_quiet(post_ai=post_ai), "NO_KEY")
        self.assertEqual(calls, [])

    def test_dev_says_we_already_published_today(self):
        post_ai, calls = fake_ai_factory(good_body())
        listing = [{"title": "x", "url": "u", "published_at": today() + "T08:00:00Z"}]
        self.assertEqual(run_quiet(post_ai=post_ai, lister=lambda u, h: listing), "ALREADY_DONE")
        self.assertEqual(calls, [])                                # state was empty: only DEV's own answer stopped it

    def test_dev_listing_unreachable_fails_closed(self):
        post_ai, calls = fake_ai_factory(good_body())

        def boom(u, h):
            raise OSError("down")
        self.assertEqual(run_quiet(post_ai=post_ai, lister=boom), "DEV_CHECK_FAILED")
        self.assertEqual(calls, [])

    def test_crash_after_the_post_leaves_a_pending_marker_that_blocks_a_second_article(self):
        post_ai, calls = fake_ai_factory(good_body())

        def timeout(*a, **k):
            raise TimeoutError("read timed out")                  # the article MAY have been created
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=timeout), "DEV_UNKNOWN")
        st = P.read_json(P.STATE_PATH, {})
        self.assertEqual(st["pending"]["title"], "How to plan TikTok captions")
        n = len(calls)
        # same day again, DEV does not list it (yet): never write a second article
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=lambda *a, **k: self.fail("second post")), "PENDING_UNKNOWN")
        self.assertEqual(len(calls), n)
        # DEV lists it: the pending marker becomes a recorded post
        listing = [{"title": "How to plan TikTok captions", "url": "https://dev.to/inayaflow/x", "id": 9, "published_at": today() + "T10:00:00Z"}]
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=lambda *a, **k: self.fail("second post"), lister=lambda u, h: listing), "ALREADY_DONE")
        st = P.read_json(P.STATE_PATH, {})
        self.assertNotIn("pending", st)
        self.assertEqual(st["posts"][-1]["url"], "https://dev.to/inayaflow/x")

    def test_old_pending_marker_that_dev_never_saw_is_cleared(self):
        old = {"date": "2020-01-01", "slug": "s", "title": "T", "target": URL}
        P.write_json(P.STATE_PATH, {"posts": [], "attempts": {}, "pending": old})
        self.assertEqual(P.reconcile_pending(P.read_json(P.STATE_PATH, {}), [], today()), "cleared")
        self.assertEqual(P.reconcile_pending({"posts": [], "pending": dict(old, date=today())}, [], today()), "unknown")
        self.assertIsNone(P.reconcile_pending({"posts": []}, [], today()))

    def test_dev_429_clears_the_marker_and_two_failures_stop_the_day(self):
        post_ai, calls = fake_ai_factory(good_body())

        def refuse(*a, **k):
            raise _ue.HTTPError("https://dev.to/api/articles", 429, "Too Many Requests", {}, None)
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=refuse), "DEV_ERROR")
        self.assertNotIn("pending", P.read_json(P.STATE_PATH, {}))
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=refuse), "DEV_ERROR")
        n = len(calls)
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=refuse), "STOPPED")
        self.assertEqual(len(calls), n)                           # the third attempt made no model call
        self.assertLessEqual(P.read_json(P.STATE_PATH, {})["spent"][today()], P.DAY_CAP)

    def test_manual_run_without_the_repository_switch_never_publishes(self):
        os.environ["DEV_POSTER_LIVE"] = "false"
        post_ai, _ = fake_ai_factory(good_body())
        self.assertEqual(run_quiet(post_ai=post_ai, post_dev=lambda *a, **k: self.fail("published without the switch")), "DRY_OK")

    def test_dry_runs_are_limited_to_two_a_day(self):
        P.DRY = True
        post_ai, calls = fake_ai_factory(good_body())
        self.assertEqual(run_quiet(post_ai=post_ai), "DRY_OK")
        self.assertEqual(run_quiet(post_ai=post_ai), "DRY_OK")
        n = len(calls)
        self.assertEqual(run_quiet(post_ai=post_ai), "DRY_LIMIT")
        self.assertEqual(len(calls), n)

    def test_the_day_cap_is_shared_by_all_runs_and_never_exceeded(self):
        self.assertEqual(P.DAY_CAP, 0.60)
        self.assertLessEqual(P.WRITER_RESERVE + P.QA_RESERVE, P.DAY_CAP)
        P.write_json(P.STATE_PATH, {"posts": [], "attempts": {}, "spent": {today(): 0.45}})      # earlier runs today spent 0.45
        post_ai, calls = fake_ai_factory(good_body())
        self.assertEqual(run_quiet(post_ai=post_ai), "DAY_CAP")
        self.assertEqual(calls, [])
        P.write_json(P.STATE_PATH, {"posts": [], "attempts": {}})
        run_quiet(post_ai=post_ai, post_dev=lambda *a, **k: {"url": "https://dev.to/inayaflow/y", "id": 1})
        sp = P.read_json(P.STATE_PATH, {})["spent"][today()]
        self.assertTrue(0 < sp <= P.DAY_CAP, sp)

    def test_pause_turn_loop_stops_at_the_cap_before_the_call_that_would_pass_it(self):
        def post(url, b, h):
            return {"content": [{"type": "text", "text": "x"}], "stop_reason": "pause_turn", "usage": {"input_tokens": 50000, "output_tokens": 5000}}   # ~$0.15 a call
        self.assertEqual(run_quiet(post_ai=post), "FAIL")
        st = P.read_json(P.STATE_PATH, {})
        self.assertLessEqual(st["spent"][today()], P.DAY_CAP)
        m = P.Meter(cap=0.2)
        with self.assertRaises(P.CapReached):
            P.anthropic(P.WRITER, "s", "u", m, post=post)         # 0 + 0.32 reserve > 0.2: not even the first call
        self.assertEqual(m.calls, 0)

    def test_refusal_is_a_clean_fail(self):
        def post(url, b, h):
            return {"content": [], "stop_reason": "refusal", "usage": {"input_tokens": 100, "output_tokens": 1}}
        self.assertEqual(run_quiet(post_ai=post, post_dev=lambda *a, **k: self.fail("published")), "FAIL")

    def test_writer_retry_fixes_a_bad_first_draft(self):
        bad = good_body() + "\n\nWe saw 900% growth.\n"
        good_text = "TITLE: How to plan TikTok captions\nDESCRIPTION: d\nTAGS: tiktok,socialmedia,webdev,productivity\nSOURCES: %s | %s\n===BODY===\n%s"
        seq = [good_text % (SRC1, SRC2, bad), good_text % (SRC1, SRC2, good_body())]
        writers = []

        def post(url, b, h):
            if b["model"] == P.QA_MODEL:
                return {"content": [{"type": "text", "text": json.dumps({"verdict": "PASS", "reasons": []})}], "usage": {"input_tokens": 4000, "output_tokens": 50}}
            writers.append(1)
            return {"content": [{"type": "text", "text": seq.pop(0)}], "stop_reason": "end_turn", "usage": {"input_tokens": 10000, "output_tokens": 1500}}
        P.DRY = True
        self.assertEqual(run_quiet(post_ai=post), "DRY_OK")
        self.assertEqual(len(writers), 2)

    def test_no_second_writer_try_when_the_money_is_not_there(self):
        bad = good_body() + "\n\nWe saw 900% growth.\n"
        text = "TITLE: How to plan TikTok captions\nDESCRIPTION: d\nTAGS: tiktok,socialmedia,webdev,productivity\nSOURCES: %s | %s\n===BODY===\n%s" % (SRC1, SRC2, bad)
        writers = []

        def post(url, b, h):
            if b["model"] == P.QA_MODEL:
                self.fail("QA must not run on a draft that failed the offline gate")
            writers.append(1)
            return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 190000, "output_tokens": 6000}}   # ~$0.44 > RETRY_BELOW (0.60 - 0.20 - 0.02 = 0.38)
        self.assertEqual(run_quiet(post_ai=post), "FAIL")
        self.assertEqual(len(writers), 1)                         # 0.44 spent: a second writer call (0.20 reserve) + the QA reserve would pass 0.60

    def test_page_not_readable_stops_before_any_spend(self):
        post_ai, calls = fake_ai_factory(good_body())
        self.assertEqual(run_quiet(post_ai=post_ai, getter=lambda u, **k: (500, u, "")), "PAGE_DOWN")
        self.assertEqual(calls, [])

    def test_workflow_needs_the_switch_for_every_kind_of_run(self):
        w = open(os.path.join(HERE, "..", "..", ".github", "workflows", "dev-poster.yml"), encoding="utf8").read()
        line = [l for l in w.splitlines() if l.strip().startswith("DRY_RUN:")][0]
        self.assertTrue(line.split("DRY_RUN:")[1].strip().startswith("${{ vars.DEV_POSTER_LIVE == 'true' &&"), line)
        self.assertTrue(line.rstrip().endswith("|| 'true' }}"))
        self.assertIn("DEV_POSTER_LIVE: ${{ vars.DEV_POSTER_LIVE }}", w)
        self.assertEqual(w.count("DEV_STATE_OK=true"), 2)         # restored, or first run (branch really absent); unreachable = not set
        self.assertIn('DEV_DAY_USD_CAP: "0.60"', w)
        self.assertIn("group: dev-poster", w)


def only_src1_body():
    """A body whose number (4000 characters) is printed ONLY on SRC1, so a thin SRC1 text makes the number unfindable."""
    return good_body().replace("[5 hashtags per post](%s)" % SRC1, "[4000 characters per caption](%s)" % SRC1)


class WriterAndGateFixes(unittest.TestCase):
    """23 Nov dry run 2 (cost $0.433, 6 problems): writer / gate mismatches."""

    def setUp(self):
        Flow.setUp(self)

    def tearDown(self):
        Flow.tearDown(self)

    # (b) numbers about OUR tool are checked against OUR whole page, not only the first 6000 characters the writer sees
    def test_numbers_in_the_paragraph_that_links_our_page_are_checked_against_our_whole_page(self):
        short = PAGE                                                             # what the prompt shows
        full = PAGE + " Your result is kept for 24 hours and the history covers 30 days."
        b = good_body().replace("can turn a topic into a short list. ", "can turn a topic into a short list kept for 24 hours and for 30 days. ")
        self.assertTrue(any("not found" in x for x in P.check_article(art(body=b), URL, short, SRC_TEXT)))
        self.assertEqual([x for x in P.check_article(art(body=b), URL, short, SRC_TEXT, page_full=full) if "not found" in x], [])

    # (c) first person, rhetorical too
    def test_first_person_including_rhetorical_lines_is_refused(self):
        for line in ("Am I shadowbanned?", "Why am I seeing fewer views?", "It's mine now.", "I'd say so.", "We asked myself that."):
            self.assertTrue(any("first person" in x for x in probs(art(body=good_body() + "\n\n" + line + "\n"))), line)
        self.assertFalse(any("first person" in x for x in probs(art(body=good_body() + "\n\nIs an account shadowbanned?\n"))))

    # (d) the overlap problem names the phrase so the retry can fix it, and it looks in the whole page
    def test_overlap_names_the_phrase_and_uses_the_whole_page(self):
        copied = "Your result is kept for exactly this long and no longer than that."
        full = PAGE + " " + copied
        b = good_body() + "\n\n" + copied
        self.assertEqual([x for x in P.check_article(art(body=b), URL, PAGE, SRC_TEXT) if "overlap" in x], [])         # not in the 6000 shown
        got = [x for x in P.check_article(art(body=b), URL, PAGE, SRC_TEXT, page_full=full) if "overlap" in x]
        self.assertEqual(len(got), 1)
        self.assertIn("your result is kept for exactly this", got[0])

    # (a) what the writer's own fetch tool returned counts as a source text
    def test_harvest_fetched_reads_the_writers_fetch_results(self):
        blocks = [{"type": "text", "text": "x"},
                  {"type": "web_fetch_tool_result", "tool_use_id": "t", "content": {"type": "web_fetch_result", "url": SRC1,
                   "content": {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": SRC_TEXT[SRC1]}}}},
                  {"type": "web_fetch_tool_result", "content": {"type": "web_fetch_tool_error", "error_code": "url_not_accessible"}},
                  {"type": "web_fetch_tool_result", "content": {"url": SRC2, "content": {"source": {"data": "   "}}}}]
        out = {}
        P.harvest_fetched(blocks, out)
        self.assertEqual(out, {SRC1: SRC_TEXT[SRC1]})
        P.harvest_fetched(None, out)
        P.harvest_fetched([{"type": "web_fetch_tool_result", "content": "weird"}], out)

    def fetch_block(self, url, text):
        return {"type": "web_fetch_tool_result", "content": {"type": "web_fetch_result", "url": url,
                "content": {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": text}}}}

    def test_a_source_our_fetcher_cannot_read_is_accepted_when_the_writers_fetch_read_it(self):
        P.DRY = True
        text = "TITLE: How to plan TikTok captions\nDESCRIPTION: d\nTAGS: tiktok,socialmedia,webdev,productivity\nSOURCES: %s | %s\n===BODY===\n%s" % (SRC1, SRC2, only_src1_body())

        def post(url, b, h):
            if b["model"] == P.QA_MODEL:
                return {"content": [{"type": "text", "text": json.dumps({"verdict": "PASS", "reasons": []})}], "usage": {"input_tokens": 4000, "output_tokens": 50}}
            return {"content": [self.fetch_block(SRC1, SRC_TEXT[SRC1]), {"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 20000, "output_tokens": 2000}}

        def thin_getter(u, **k):
            if u == SRC1:
                return 200, u, "<html><body>TikTok Support</body></html>"            # what tiktok.com/support returns to our fetcher
            return fake_get(u)
        self.assertEqual(run_quiet(post_ai=post, getter=thin_getter), "DRY_OK")
        # without the writer's fetched text the same article fails (the old behaviour)
        P.write_json(P.STATE_PATH, {"posts": [], "attempts": {}})

        def post_no_fetch(url, b, h):
            r = post(url, b, h)
            r["content"] = [x for x in r["content"] if x["type"] == "text"]
            return r
        self.assertEqual(run_quiet(post_ai=post_no_fetch, getter=thin_getter), "FAIL")

    def test_the_retry_tells_the_writer_which_pages_could_not_be_read(self):
        P.DRY = True
        text = "TITLE: How to plan TikTok captions\nDESCRIPTION: d\nTAGS: tiktok,socialmedia,webdev,productivity\nSOURCES: %s | %s\n===BODY===\n%s" % (SRC1, SRC2, only_src1_body())
        prompts = []

        def post(url, b, h):
            if b["model"] == P.QA_MODEL:
                return {"content": [{"type": "text", "text": json.dumps({"verdict": "FAIL", "reasons": ["x"]})}], "usage": {"input_tokens": 100, "output_tokens": 10}}
            prompts.append(b["messages"][0]["content"])
            return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 20000, "output_tokens": 2000}}

        def thin_getter(u, **k):
            if u == SRC1:
                return 200, u, "<html><body>TikTok Support</body></html>"
            if u == SRC2:
                return 200, u, "<p>%s %s</p>" % (SRC_TEXT[SRC2], "More plain text about the study. " * 20)          # a readable page: >= 300 characters
            return fake_get(u)
        run_quiet(post_ai=post, getter=thin_getter)
        self.assertEqual(len(prompts), 2)
        self.assertNotIn("could NOT be read", prompts[0])
        self.assertIn("could NOT be read", prompts[1])
        self.assertIn(SRC1, prompts[1].split("could NOT be read")[1])
        self.assertNotIn(SRC2, prompts[1].split("could NOT be read")[1])

    def test_the_prompt_carries_the_new_rules(self):
        s = P.WRITER_SYSTEM
        for must in ("CITE ONLY WHAT YOU READ", "tiktok.com/support", "never copy 7 or more words", "PAGE TEXT", "myself anywhere", "am I shadowbanned"):
            self.assertIn(must, s)

    def test_a_writer_run_is_cheaper_and_a_retry_fits_the_day(self):
        self.assertEqual((P.MAX_SEARCHES, P.MAX_FETCHES, P.FETCH_TOKENS, P.WRITER_MAX_TOKENS), (3, 3, 6000, 3500))
        sent = []

        def post(url, b, h):
            sent.append(b)
            return {"content": [{"type": "text", "text": "x"}], "stop_reason": "end_turn", "usage": {"input_tokens": 40000, "output_tokens": 2500, "server_tool_use": {"web_search_requests": 3}}}
        m = P.Meter()
        P.run_writer("s", "u", m, post=post)
        tools = {t["name"]: t for t in sent[0]["tools"]}
        self.assertEqual((tools["web_search"]["max_uses"], tools["web_fetch"]["max_uses"], tools["web_fetch"]["max_content_tokens"]), (3, 3, 6000))
        self.assertEqual(sent[0]["max_tokens"], 3500)
        self.assertLessEqual(m.cost, 0.30)                                         # a realistic big attempt: 40k in, 2.5k out, 3 searches = $0.135
        self.assertLessEqual(P.WRITER_RESERVE, 0.20)
        # two such attempts and the QA call fit inside the $0.60 day
        self.assertLessEqual(2 * m.cost + P.QA_RESERVE, P.DAY_CAP)
        self.assertGreaterEqual(P.RETRY_BELOW, 0.35)


class StateBranchScript(unittest.TestCase):
    """scripts/dev_poster/state_branch.sh run for real against local git remotes (file:// URLs): the first real run failed with 'unreachable'."""

    SH = os.path.join(HERE, "state_branch.sh")

    def run_sh(self, url, dest, branch="ops-dev-poster"):
        import subprocess, shutil
        bash = shutil.which("bash")
        r = subprocess.run([bash, self.SH, url, dest, branch], capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout.strip(), r.stderr

    def make_remote(self, with_branch):
        import subprocess
        base = tempfile.mkdtemp()
        remote = os.path.join(base, "r.git")
        g = lambda *a, cwd=None: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)
        g("init", "-q", "--bare", "-b", "main", remote)
        w = os.path.join(base, "w")
        g("clone", "-q", remote, w)
        g("config", "user.name", "t", cwd=w); g("config", "user.email", "t@example.com", cwd=w)
        open(os.path.join(w, "README"), "w").write("code repo")
        g("add", "-A", cwd=w); g("commit", "-q", "-m", "c", cwd=w); g("push", "-q", "origin", "HEAD:main", cwd=w)
        if with_branch:
            g("checkout", "-q", "--orphan", "ops-dev-poster", cwd=w)
            g("rm", "-rqf", ".", cwd=w)
            os.makedirs(os.path.join(w, "ops"))
            json.dump({"posts": [], "attempts": {}}, open(os.path.join(w, "ops", "dev_poster_state.json"), "w"))
            g("add", "-A", cwd=w); g("commit", "-q", "-m", "state", cwd=w); g("push", "-q", "origin", "ops-dev-poster", cwd=w)
        return "file://" + remote.replace("\\", "/"), base

    def test_branch_absent_means_first_run_is_allowed(self):
        url, base = self.make_remote(with_branch=False)
        rc, out, err = self.run_sh(url, os.path.join(base, "dest"))
        self.assertEqual((rc, out), (10, "ABSENT"))
        self.assertTrue(os.path.isdir(os.path.join(base, "dest", ".git")))        # the repo WAS reached: the save step can create the orphan branch in it

    def test_branch_present_is_checked_out(self):
        url, base = self.make_remote(with_branch=True)
        rc, out, err = self.run_sh(url, os.path.join(base, "dest"))
        self.assertEqual((rc, out), (0, "EXISTS"))
        st = json.load(open(os.path.join(base, "dest", "ops", "dev_poster_state.json")))
        self.assertEqual(st["posts"], [])

    def test_real_unreachability_stays_fail_closed(self):
        base = tempfile.mkdtemp()
        rc, out, err = self.run_sh("file://" + os.path.join(base, "does-not-exist.git").replace("\\", "/"), os.path.join(base, "dest"))
        self.assertEqual((rc, out), (20, "UNREACHABLE"))
        self.assertNotIn("file://", out + err)                                       # nothing about the URL is printed

    def test_empty_repository_counts_as_absent(self):
        import subprocess
        base = tempfile.mkdtemp()
        remote = os.path.join(base, "e.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        rc, out, err = self.run_sh("file://" + remote.replace("\\", "/"), os.path.join(base, "dest"))
        self.assertEqual((rc, out), (10, "ABSENT"))

    def test_both_workflow_steps_use_the_script_and_the_exit_codes(self):
        w = open(os.path.join(HERE, "..", "..", ".github", "workflows", "dev-poster.yml"), encoding="utf8").read()
        self.assertEqual(w.count("bash scripts/dev_poster/state_branch.sh"), 2)     # restore step and save step
        self.assertEqual(w.count('"$rc" = "10"'), 2)                                 # ABSENT arm in both
        self.assertIn("git clone", open(self.SH, encoding="utf8").read())


class CodeChecks(unittest.TestCase):
    def test_our_link_in_the_first_or_the_last_paragraph_is_refused(self):
        link = "a [InayaFlow's free hashtag generator](%s) helps" % URL
        paras = good_body().split("\n\n")
        link_para = [p for p in paras if URL in p][0]
        rest = [p for p in paras if URL not in p]
        first = "\n\n".join([link_para] + rest)
        last = "\n\n".join(rest + [link_para])
        self.assertTrue(any("first or the last paragraph" in x for x in probs(art(body=first))))
        self.assertTrue(any("first or the last paragraph" in x for x in probs(art(body=last))))
        self.assertFalse(any("first or the last paragraph" in x for x in probs(art())))

    def test_inayaflow_name_more_than_twice_is_refused(self):
        b = good_body().replace("pick them with care.", "pick them with care. InayaFlow says so. InayaFlow again.")
        self.assertTrue(any("InayaFlow name" in x for x in probs(art(body=b))))
        two = good_body().replace("pick them with care.", "pick them with care. InayaFlow says so.")
        self.assertFalse(any("InayaFlow name" in x for x in probs(art(body=two))))

    def test_similarity_to_our_earlier_articles(self):
        past = P.shingles(good_body())
        self.assertTrue(any("too similar" in x for x in P.check_article(art(), URL, PAGE, SRC_TEXT, past_shingles=past)))
        other = P.shingles("Completely different words about gardening tomatoes and watering schedules in a greenhouse during the long summer months " * 20)
        self.assertFalse(any("too similar" in x for x in P.check_article(art(), URL, PAGE, SRC_TEXT, past_shingles=other)))
        self.assertEqual(P.check_article(art(), URL, PAGE, SRC_TEXT, past_shingles=None), [])

    def test_published_articles_keep_their_shingles_for_the_next_check(self):
        post_ai, _ = fake_ai_factory(good_body())
        Flow.setUp(self)
        try:
            run_quiet(post_ai=post_ai, post_dev=lambda *a, **k: {"url": "https://dev.to/inayaflow/z", "id": 3})
            st = P.read_json(P.STATE_PATH, {})
            self.assertEqual(len(st["shingles"]), 1)
            self.assertGreater(len(st["shingles"][0]), 100)
        finally:
            Flow.tearDown(self)


class AnchorMix(unittest.TestCase):
    """Link text mix (RULEBOOK 9a): 10-post cycle per target page."""
    SLUG, URL = "shadowban-checker", "https://inayaflow.ai/tools/tiktok-shadowban-checker"
    A = P

    def info(self, n, slug=None, url=None):
        return self.A.anchor_info(slug or self.SLUG, url or self.URL, n)

    def test_cycle_counts_and_no_repeat(self):
        types = [self.A.anchor_type(n, self.SLUG) for n in range(10)]
        self.assertEqual(types, ["brand", "plain", "address", "partial", "brand", "address", "plain", "partial", "brand", "exact"])
        self.assertEqual({t: types.count(t) for t in set(types)}, {"brand": 3, "address": 2, "plain": 2, "partial": 2, "exact": 1})
        seq = [self.A.anchor_type(n, self.SLUG) for n in range(60)]
        self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])))        # also across the wrap exact -> brand

    def test_homepage_is_brand_or_address_only(self):
        seq = [self.A.anchor_type(n, "homepage") for n in range(30)]
        self.assertEqual(set(seq), {"brand", "address"})
        self.assertTrue(all(a != b for a, b in zip(seq, seq[1:])))

    def test_cursor_counts_only_booked_posts_of_that_target(self):
        st = {"posts": [{"slug": self.SLUG, "platform": "wordpress"}, {"slug": self.SLUG, "platform": "blogger"}, {"slug": "money-calculator"}], "pending": {"notion": {"slug": self.SLUG}}}
        self.assertEqual(self.A.anchor_count(st, self.SLUG), 2)          # a pending marker alone does not move it
        self.assertEqual(self.A.anchor_count(st, "homepage"), 0)
        self.assertEqual(self.A.anchor_info(self.SLUG, self.URL, self.A.anchor_count(st, self.SLUG))["type"], "address")

    def test_keywords_for_every_page_and_fallback(self):
        for pg in self.A.PAGES:
            self.assertTrue(self.A.anchor_keyword(pg[0], pg[1]))
            self.assertIn(pg[0], self.A.ANCHOR_KEYWORDS)
        self.assertEqual(self.A.anchor_keyword("x", "https://inayaflow.ai/tools/some-new-thing"), "some new thing")
        self.assertEqual(self.info(0)["keyword"], "TikTok shadowban checker")

    def test_brand(self):
        i = self.info(0)
        self.assertEqual(i["type"], "brand")
        self.assertEqual(self.A.anchor_problems("InayaFlow", i), [])
        self.assertEqual(self.A.anchor_problems("InayaFlow's free checker", i), [])
        self.assertTrue(self.A.anchor_problems("this free checker", i))

    def test_address(self):
        i = self.info(2)
        self.assertEqual(i["type"], "address")
        self.assertEqual(self.A.anchor_problems("inayaflow.ai/tools/tiktok-shadowban-checker", i), [])
        self.assertEqual(self.A.anchor_problems("inayaflow.ai/tools/tiktok-shadowban-checker/", i), [])
        self.assertTrue(self.A.anchor_problems("https://inayaflow.ai/tools/tiktok-shadowban-checker", i))
        self.assertTrue(self.A.anchor_problems("inayaflow.ai", i))
        self.assertTrue(self.A.anchor_problems("InayaFlow", i))

    def test_plain(self):
        i = self.info(1)
        self.assertEqual(i["type"], "plain")
        self.assertEqual(self.A.anchor_problems("this free checker", i), [])
        self.assertEqual(self.A.anchor_problems("a free tool for it", i), [])
        self.assertTrue(self.A.anchor_problems("InayaFlow's free tool", i))            # brand
        self.assertTrue(self.A.anchor_problems("TikTok shadowban checker", i))         # keyword
        self.assertTrue(self.A.anchor_problems("the shadowban checker", i))            # half or more of the keyword words

    def test_partial(self):
        i = self.info(3)
        self.assertEqual(i["type"], "partial")
        self.assertEqual(self.A.anchor_problems("check whether your TikTok is shadowbanned", i), [])
        self.assertEqual(self.A.anchor_problems("a free shadowban test", i), [])
        self.assertTrue(self.A.anchor_problems("TikTok shadowban checker", i))         # all the words = exact
        self.assertTrue(self.A.anchor_problems("the best TikTok shadowban checker online", i))   # contains all the words
        self.assertTrue(self.A.anchor_problems("a free tool for it", i))               # shares none
        self.assertTrue(self.A.anchor_problems("InayaFlow shadowban test", i))         # brand

    def test_exact(self):
        i = self.info(9)
        self.assertEqual(i["type"], "exact")
        self.assertEqual(self.A.anchor_problems("TikTok shadowban checker", i), [])
        self.assertEqual(self.A.anchor_problems("tiktok Shadowban Checker", i), [])
        self.assertTrue(self.A.anchor_problems("a TikTok shadowban checker", i))
        self.assertTrue(self.A.anchor_problems("InayaFlow", i))

    def test_prompt_carries_type_and_examples(self):
        for n, word in ((0, "BRAND"), (1, "PLAIN WORDS"), (2, "inayaflow.ai/tools/tiktok-shadowban-checker"), (3, "PARTIAL KEYWORD"), (9, "EXACT KEYWORD")):
            self.assertIn(word, self.A.anchor_prompt(self.info(n)))
        self.assertIn("Examples:", self.A.anchor_prompt(self.info(0)))

    def test_check_article_enforces_the_type(self):
        def with_text(txt):
            b = good_body().replace("[InayaFlow's free hashtag generator](%s)" % URL, "[%s](%s)" % (txt, URL))
            return art(body=b)
        hg = P.anchor_info("hashtag-generator", URL, 2)        # address
        ok = P.check_article(with_text("inayaflow.ai/tools/tiktok-hashtag-generator"), URL, PAGE, SRC_TEXT, anchor=hg)
        self.assertEqual([x for x in ok if "link text" in x or "bare" in x], [])
        bad = P.check_article(with_text("a free hashtag generator"), URL, PAGE, SRC_TEXT, anchor=hg)
        self.assertTrue(any("link text" in x for x in bad))
        brand = P.anchor_info("hashtag-generator", URL, 0)
        self.assertEqual([x for x in P.check_article(with_text("InayaFlow"), URL, PAGE, SRC_TEXT, anchor=brand) if "link text" in x], [])
        self.assertTrue(any("link text" in x for x in P.check_article(with_text("InayaFlow"), URL, PAGE, SRC_TEXT, anchor=P.anchor_info("hashtag-generator", URL, 9))))
        # bare unlinked address stays banned even when the type is address
        b = good_body().replace("[InayaFlow's free hashtag generator](%s)" % URL, "[inayaflow.ai/tools/tiktok-hashtag-generator](%s)" % URL) + "\n\nSee inayaflow.ai too.\n"
        self.assertTrue(any("bare" in x for x in P.check_article(art(body=b), URL, PAGE, SRC_TEXT, anchor=hg)))
        # without an anchor the old behaviour is unchanged
        self.assertEqual(P.check_article(art(), URL, PAGE, SRC_TEXT), [])

    def test_prompt_has_the_link_text_line(self):
        self.assertIn("[LINK TEXT](URL)", P.WRITER_SYSTEM)
        self.assertNotIn("Never write the bare text inayaflow.ai.", P.WRITER_SYSTEM)


if __name__ == "__main__":
    unittest.main(verbosity=1)
