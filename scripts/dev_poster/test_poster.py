"""Offline tests for the DEV poster. No network, no keys. Run: python3 scripts/dev_poster/test_poster.py"""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["DEV_OPS_DIR"] = tempfile.mkdtemp()
os.environ["DRY_RUN"] = "true"
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
        self.assertEqual(P.run(post_ai=post_ai, post_dev=lambda *a, **k: called.append(1), getter=fake_get, sleeper=lambda s: None), "FAIL")
        self.assertEqual(called, [])
        self.assertTrue(any(f.endswith(".md") for f in os.listdir(P.DRAFT_DIR)))

    def test_unsourced_article_not_published(self):
        post_ai, _ = fake_ai_factory(good_body() + "\n\nWe saw 900% growth.\n")
        P.DRY = False
        os.environ["DEV_API_KEY"] = "SECRET-KEY-VALUE"
        called = []
        self.assertEqual(P.run(post_ai=post_ai, post_dev=lambda *a, **k: called.append(1), getter=fake_get, sleeper=lambda s: None), "FAIL")
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
            res = P.run(post_ai=post_ai, post_dev=post_dev, getter=getter, sleeper=lambda s: None)
        self.assertEqual(res, "PUBLISHED")
        self.assertEqual(sent["url"], "https://dev.to/api/articles")
        self.assertEqual(sent["headers"]["accept"], "application/vnd.forem.api-v1+json")
        self.assertNotIn("canonical_url", sent["body"]["article"])
        self.assertEqual(len(sent["body"]["article"]["tags"]), 4)
        st = P.read_json(P.STATE_PATH, {})
        self.assertEqual(st["posts"][0]["rel"], "followed")
        self.assertNotIn("SECRET-KEY-VALUE", buf.getvalue() + open(P.LOG_PATH, encoding="utf8").read())
        # second run the same day does nothing
        self.assertEqual(P.run(post_ai=post_ai, post_dev=post_dev, getter=getter, sleeper=lambda s: None), "ALREADY_DONE")

    def test_cost_cap_and_estimate(self):
        post_ai, _ = fake_ai_factory(good_body())
        m = P.Meter()
        P.anthropic(P.WRITER, "s", "u", m, post=post_ai)
        self.assertLess(m.cost, P.USD_CAP)


if __name__ == "__main__":
    unittest.main(verbosity=1)
