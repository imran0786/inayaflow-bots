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
             "When you need ideas fast, a [free hashtag generator](%s) can turn a topic into a short list. " % URL + para,
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
        a = art(body=good_body().replace("[free hashtag generator](%s)" % URL, "a free hashtag generator"))
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
        os.environ["DEV_POSTER_LIVE"] = "true"
        os.environ["DEV_API_KEY"] = "SECRET-KEY-VALUE"
        P.DRY = False

    def tearDown(self):
        P.pick_page = REAL_PICK                                   # never leak the patched picker into other tests
        P.DRY = True
        os.environ["DEV_STATE_OK"] = "true"
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
            return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 120000, "output_tokens": 6000}}   # ~$0.30 > RETRY_BELOW 0.28
        self.assertEqual(run_quiet(post_ai=post), "FAIL")
        self.assertEqual(len(writers), 1)                         # 0.30 spent: a second writer call (0.30 reserve) + the QA reserve would pass 0.60

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


class CodeChecks(unittest.TestCase):
    def test_our_link_in_the_first_or_the_last_paragraph_is_refused(self):
        link = "a [free hashtag generator](%s) helps" % URL
        paras = good_body().split("\n\n")
        link_para = [p for p in paras if URL in p][0]
        rest = [p for p in paras if URL not in p]
        first = "\n\n".join([link_para] + rest)
        last = "\n\n".join(rest + [link_para])
        self.assertTrue(any("first or the last paragraph" in x for x in probs(art(body=first))))
        self.assertTrue(any("first or the last paragraph" in x for x in probs(art(body=last))))
        self.assertFalse(any("first or the last paragraph" in x for x in probs(art())))

    def test_inayaflow_name_more_than_twice_is_refused(self):
        b = good_body().replace("pick them with care.", "pick them with care. InayaFlow says so. InayaFlow again. InayaFlow once more.")
        self.assertTrue(any("InayaFlow name" in x for x in probs(art(body=b))))
        two = good_body().replace("pick them with care.", "pick them with care. InayaFlow says so. InayaFlow again.")
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
