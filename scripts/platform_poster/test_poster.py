"""Offline tests for the platform poster. No network, no keys. Run: python3 scripts/platform_poster/test_poster.py"""
import contextlib, io, json, os, re, sys, tempfile, unittest, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["PLATFORM_OPS_DIR"] = tempfile.mkdtemp()
os.environ["DRY_RUN"] = "true"
os.environ["PLATFORM_STATE_OK"] = "true"
os.environ["PLATFORM_POSTER_LIVE"] = "true"
import poster as P  # noqa: E402

P.DAY_CAP = 0.90
D = P.D
SECRET = "sekret-token-VALUE-123"
for k in ("WPCOM_CLIENT_ID", "WPCOM_CLIENT_SECRET", "WPCOM_APP_PASSWORD", "WPCOM_USERNAME", "BLOGGER_CLIENT_ID", "BLOGGER_CLIENT_SECRET", "BLOGGER_REFRESH_TOKEN", "NOTION_TOKEN"):
    os.environ[k] = SECRET + k
SRC = "https://research.example.org/study"
SRC_TEXT = "A study of captions found 12% more reach for tidy captions. " * 8
SITE_TEXT = "Free TikTok tool page with plain explanations of how the checker works and what it needs. " * 6


def filler(tag, n):
    """n words that cannot repeat a 7-word run in any other platform's text: letters only, tag-specific."""
    def w(i):
        s, x = "", i + 1
        while x:
            s += chr(97 + x % 26)
            x //= 26
        return tag + s
    return " ".join(w(i) for i in range(n))


def body_for(platform, target, words=420, with_source=False, extra=""):
    para = filler(platform, words // 3)
    src = ("A study of captions found [12%% more reach](%s) for tidy captions. " % SRC) if with_source else ""
    return "## Start here\n\n%s\n\n%s%s [InayaFlow's free checker](%s) %s\n\n- %s\n\n%s\n" % (
        para, src, filler(platform + "q", 4), target, filler(platform + "m", 40), filler(platform + "l", 20), filler(platform + "z", words // 3) + extra)


def probs(art, target, platform="wordpress", **kw):
    return P.check_post(art, target, platform, kw.pop("page_full", SITE_TEXT), kw.pop("source_texts", {}), **kw)


T = "https://inayaflow.ai/tools/tiktok-hashtag-generator"


def art(**kw):
    a = {"title": "A title", "tags": [], "sources": [], "body": body_for("wordpress", T)}
    a.update(kw)
    return a


class Gate(unittest.TestCase):
    def test_good_passes(self):
        self.assertEqual(probs(art(), T), [])

    def test_good_with_source_passes(self):
        a = art(sources=[SRC], body=body_for("wordpress", T, with_source=True))
        self.assertEqual(probs(a, T, source_texts={SRC: SRC_TEXT}), [])

    def test_wrong_or_missing_or_double_link(self):
        self.assertTrue(probs(art(body=body_for("wordpress", "https://inayaflow.ai/tools/other")), T))
        self.assertTrue(probs(art(body=body_for("wordpress", T).replace("[InayaFlow's free checker](%s)" % T, "a page")), T))
        self.assertTrue(probs(art(body=body_for("wordpress", T, extra=" See [more](https://inayaflow.ai/blog/x).")), T))

    def test_homepage_target_slash(self):
        h = "https://inayaflow.ai/"
        self.assertEqual(probs(art(body=body_for("wordpress", h)), h), [])

    def test_bare_domain_refused(self):
        self.assertTrue(any("bare" in x for x in probs(art(body=body_for("wordpress", T, extra=" Visit inayaflow.ai today.")), T)))

    def test_unsourced_number_and_not_in_source(self):
        self.assertTrue(any("47" in x for x in probs(art(body=body_for("wordpress", T, extra=" Creators see 47% growth.")), T)))
        a = art(sources=[SRC], body=body_for("wordpress", T, with_source=True).replace("12% more", "63% more"))
        self.assertTrue(any("63" in x for x in probs(a, T, source_texts={SRC: SRC_TEXT})))

    def test_source_must_be_listed_and_opened(self):
        a = art(sources=[], body=body_for("wordpress", T, with_source=True))
        self.assertTrue(probs(a, T, source_texts={SRC: SRC_TEXT}))
        a = art(sources=[SRC], body=body_for("wordpress", T, with_source=True))
        self.assertTrue(probs(a, T, source_texts={SRC: ""}))

    def test_first_person_genie_banned_handle(self):
        self.assertTrue(any("first person" in x for x in probs(art(body=body_for("wordpress", T, extra=" I think so.")), T)))
        self.assertTrue(any("banned" in x for x in probs(art(body=body_for("wordpress", T, extra=" Genie does this.")), T)))
        self.assertTrue(any("banned" in x for x in probs(art(body=body_for("wordpress", T, extra=" Let us dive into it.")), T)))
        self.assertTrue(any("@handle" in x for x in probs(art(body=body_for("wordpress", T, extra=" Ask @someone.")), T)))

    def test_word_range_per_platform(self):
        self.assertTrue(any("words" in x for x in probs(art(body=body_for("notion", T, words=900)), T, platform="notion")))
        self.assertTrue(any("words" in x for x in probs(art(body=body_for("wordpress", T, words=60)), T)))

    def test_overlap_with_site_other_platform_and_past(self):
        run7 = "one two three four five six seven eight nine ten"
        a = art(body=body_for("wordpress", T, extra=" " + run7))
        self.assertTrue(any("our site" in x for x in probs(a, T, site_corpus="x " + run7 + " y")))
        self.assertTrue(any("today's blogger" in x for x in probs(a, T, others=[("blogger", "q " + run7 + " q")])))
        self.assertTrue(any("earlier" in x for x in probs(a, T, past_shingles=D.shingles(a["body"]))))

    def test_site_overlap_needs_ten_words_but_other_platform_stays_seven(self):
        run8 = "one two three four five six seven eight"
        run10 = run8 + " nine ten"
        a8 = art(body=body_for("wordpress", T, extra=" " + run8))
        self.assertFalse(any("our site" in x for x in probs(a8, T, site_corpus="x " + run8 + " y")))
        a10 = art(body=body_for("wordpress", T, extra=" " + run10))
        self.assertTrue(any("10-word overlap with our site" in x for x in probs(a10, T, site_corpus="x " + run10 + " y")))
        self.assertTrue(any("today's blogger" in x for x in probs(a8, T, others=[("blogger", "q " + run8 + " q")])))

    def test_quoted_first_person_is_ignored_unquoted_still_fails(self):
        q = ' Readers ask "How much will TikTok pay me for my views?" and the answer varies.'
        self.assertFalse(any("first person" in x for x in probs(art(body=body_for("wordpress", T, extra=q)), T)))
        q2 = ' Readers ask “How much will TikTok pay me?” and the answer varies.'
        self.assertFalse(any("first person" in x for x in probs(art(body=body_for("wordpress", T, extra=q2)), T)))
        self.assertTrue(any("first person" in x for x in probs(art(body=body_for("wordpress", T, extra=' "Quoted." I think so.')), T)))

    def test_qa_reply_with_fence_or_prose_is_understood_and_garbage_retried_once(self):
        ok = '{"verdict":"PASS","reasons":[]}'
        self.assertEqual(D.qa_json("Here you go:\n```json\n" + ok + "\n```\nDone")["verdict"], "PASS")
        self.assertEqual(D.qa_json('note {not json} then ' + ok)["verdict"], "PASS")
        self.assertIsNone(D.qa_json("no json at all"))
        replies = ["sorry, cannot say", "```json\n" + ok + "\n```"]
        n = []

        def post(url, b, h):
            n.append(1)
            return {"content": [{"type": "text", "text": replies[len(n) - 1] if len(n) <= 2 else "x"}], "usage": {"input_tokens": 10, "output_tokens": 5}}
        meter = D.Meter(cap=5.0)
        r = D.qa_gate(art(), T, {}, "page", meter, post=post)
        self.assertEqual(r, (True, []))
        self.assertEqual(len(n), 2)
        n.clear(); replies[:] = ["bad", "worse"]
        self.assertEqual(D.qa_gate(art(), T, {}, "page", meter, post=post), (False, ["QA answer not understood"]))
        self.assertEqual(len(n), 2)

    def test_link_at_end_refused(self):
        b = "## H\n\n%s\n\n%s\n\n%s\n\nLast: [page](%s)" % (filler("wordpress", 100), filler("wordpressa", 100), filler("wordpressb", 100), T)
        self.assertTrue(any("first or last" in x for x in probs(art(body=b), T)))


class Convert(unittest.TestCase):
    MD = "## Head\n\nPara with [a link](https://inayaflow.ai/tools/x) and **bold** & <b>.\n\n- one\n- two [src](https://e.org/a)\n\n1. first\n2. second\n"

    def test_html_plain_anchor_no_rel(self):
        h = P.to_html(self.MD)
        self.assertIn('<a href="https://inayaflow.ai/tools/x">a link</a>', h)
        self.assertNotIn("rel=", h)
        self.assertNotIn("nofollow", h)
        self.assertIn("<h2>Head</h2>", h)
        self.assertIn("<ul><li>one</li>", h)
        self.assertIn("<ol><li>first</li>", h)
        self.assertIn("&amp; &lt;b&gt;", h)

    def test_notion_blocks_links_lists(self):
        bl = P.to_notion_blocks(self.MD)
        types = [b["type"] for b in bl]
        self.assertEqual(types, ["heading_2", "paragraph", "bulleted_list_item", "bulleted_list_item", "numbered_list_item", "numbered_list_item"])
        rt = bl[1]["paragraph"]["rich_text"]
        self.assertEqual(rt[1]["text"]["link"]["url"], "https://inayaflow.ai/tools/x")
        self.assertTrue(rt[3]["annotations"]["bold"])

    def test_notion_long_text_chunked(self):
        rt = P.rich_text("x" * 4500)
        self.assertTrue(all(len(r["text"]["content"]) <= 2000 for r in rt))


class PushPriority(unittest.TestCase):
    TIER1 = ["guide-no-views", "guide-views-going-down", "guide-shadowban", "guide-hashtag-strategy", "shadowban-checker", "guide-captions", "analyze", "guide-hooks", "guide-how-many-views"]
    TIER2 = ["follower-count-checker", "guide-trending-hashtags", "hashtag-generator", "guide-growth-tool"]

    def setUp(self):
        self._p = P.D.PRIORITY_PATH

    def tearDown(self):
        P.D.PRIORITY_PATH = self._p

    def run_days(self, n):
        st, seen, days = {"posts": []}, {pl: [] for pl in P.PLATFORMS}, []
        for d in range(1, n + 1):
            day = "2026-12-%02d" % d
            p = P.pick_targets(st, day)
            days.append({k: v[0] for k, v in p.items()})
            for k, v in p.items():
                seen[k].append(v[0])
                st["posts"].append({"platform": k, "slug": v[0], "date": day})
        return days, seen

    def test_tier1_first_three_distinct_each_day(self):
        days, _ = self.run_days(9)
        for d in days:
            self.assertEqual(len(set(d.values())), 3)
            self.assertTrue(set(d.values()) <= set(self.TIER1))

    def test_each_platform_covers_all_tier1_before_repeating_then_tier2(self):
        _, seen = self.run_days(13)
        for pl, s in seen.items():
            self.assertEqual(sorted(s[:9]), sorted(self.TIER1))
            self.assertEqual(sorted(s[9:13]), sorted(self.TIER2))

    def test_new_pages_are_targets_with_anchor_keywords(self):
        slugs = {t[0] for t in P.TARGETS}
        for sl, kw in [("guide-no-views", "why is my TikTok getting no views"), ("guide-views-going-down", "why are my TikTok views going down"),
                       ("guide-hooks", "TikTok hooks"), ("guide-growth-tool", "TikTok growth tool"), ("analyze", "TikTok account analyzer")]:
            self.assertIn(sl, slugs)
            self.assertEqual(P.D.ANCHOR_KEYWORDS[sl], kw)
        self.assertEqual(next(t for t in P.TARGETS if t[0] == "analyze")[1], "https://inayaflow.ai/analyze")

    def test_unknown_slug_ignored_with_warning(self):
        import tempfile
        f = os.path.join(tempfile.mkdtemp(), "pp.json")
        json.dump({"tiers": [["nope-slug", "analyze"]]}, open(f, "w"))
        P.D.PRIORITY_PATH = f
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            p = P.pick_targets({"posts": []}, "2026-12-01")
        self.assertIn("nope-slug", out.getvalue())
        self.assertEqual(p["wordpress"][0], "analyze")

    def test_missing_file_old_behaviour(self):
        P.D.PRIORITY_PATH = os.path.join(tempfile_dir(), "does-not-exist.json")
        p = P.pick_targets({"posts": []}, "2026-12-01")
        self.assertEqual([v[0] for v in p.values()], [t[0] for t in P.TARGETS[:3]])
        P.D.PRIORITY_PATH = os.path.join(tempfile_dir(), "bad.json")
        open(P.D.PRIORITY_PATH, "w").write("{not json")
        self.assertEqual([v[0] for v in P.pick_targets({"posts": []}, "2026-12-01").values()], [t[0] for t in P.TARGETS[:3]])


def tempfile_dir():
    import tempfile
    return tempfile.mkdtemp()


class Pick(unittest.TestCase):
    def test_three_different_targets_and_rotation(self):
        p = P.pick_targets({"posts": []}, "2026-12-01")
        self.assertEqual(len({v[0] for v in p.values()}), 3)
        st = {"posts": [{"platform": k, "slug": v[0], "date": "2026-12-01"} for k, v in p.items()]}
        p2 = P.pick_targets(st, "2026-12-02")
        for k in p:
            self.assertNotEqual(p[k][0], p2[k][0])

    def test_week_covers_every_tool_and_homepage_eventually(self):
        st, seen = {"posts": []}, {pl: set() for pl in P.PLATFORMS}
        for d in range(1, len(P.TARGETS) + 1):
            p = P.pick_targets(st, "2026-12-%02d" % d)
            for k, v in p.items():
                seen[k].add(v[0])
                st["posts"].append({"platform": k, "slug": v[0], "date": "2026-12-%02d" % d})
        allseen = set().union(*seen.values())
        self.assertTrue({t[0] for t in P.TARGETS if t[2] == "tool"} <= allseen)
        for k in seen:
            self.assertEqual(len(seen[k]), len(P.TARGETS))

    def test_targets_have_9_tools_guides_home(self):
        kinds = [t[2] for t in P.TARGETS]
        self.assertEqual(kinds.count("tool"), 10)
        self.assertEqual(kinds.count("home"), 1)
        self.assertTrue(any(t[0] == "guide-hashtag-strategy" for t in P.TARGETS))


# ---------- end-to-end with fakes ----------
def fake_ai(calls, verdict="PASS", bodyfn=None):
    def post(url, b, h):
        calls.append(b["model"])
        if b["model"] == D.QA_MODEL:
            return {"content": [{"type": "text", "text": json.dumps({"verdict": verdict, "reasons": []})}], "usage": {"input_tokens": 4000, "output_tokens": 50}}
        user = b["messages"][0]["content"]
        tgt = re.search(r"TARGET PAGE: (\S+)", user).group(1)
        pl = re.search(r"Write today's (\w+) article", user).group(1).lower()
        body = (bodyfn or body_for)(pl, tgt)
        text = "TITLE: Title for %s\nSOURCES: none\n===BODY===\n%s" % (pl, body)
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 20000, "output_tokens": 2000, "server_tool_use": {"web_search_requests": 3}}}
    return post


def fake_get(url, timeout=15, limit=0):
    if url.startswith("https://inayaflow.ai"):
        return 200, url, "<html><body><p>%s</p></body></html>" % SITE_TEXT
    anchors = "".join('<a href="%s">x</a>' % t[1] for t in P.TARGETS)
    return 200, url, "<html><body><p>%s</p>%s</body></html>" % ("live page " * 50, anchors)


def fake_publishers(sent):
    def mk(pl):
        def pub(art):
            sent.append((pl, art["title"]))
            return {"url": "https://live.example.net/%s/post" % pl, "id": len(sent)}
        return pub
    return {pl: mk(pl) for pl in P.PLATFORMS}


def quiet(**kw):
    kw.setdefault("sleeper", lambda s: None)
    kw.setdefault("getter", fake_get)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = P.run(**kw)
    return res, buf.getvalue()


class Flow(unittest.TestCase):
    def setUp(self):
        for f in (P.STATE_PATH, P.LOG_PATH):
            if os.path.exists(f):
                os.remove(f)
        P.DRY = True
        P.DAY_CAP = 0.90
        os.environ["PLATFORM_STATE_OK"] = "true"
        os.environ["PLATFORM_FIRST_RUN"] = "true"
        os.environ["PLATFORM_POSTER_LIVE"] = "true"

    def test_no_state_means_no_run(self):
        os.environ["PLATFORM_STATE_OK"] = "false"
        calls = []
        res, _ = quiet(post_ai=fake_ai(calls))
        self.assertEqual((res, calls), ("NO_STATE", []))

    def test_dry_run_writes_three_drafts_publishes_nothing(self):
        calls, sent = [], []
        res, out = quiet(post_ai=fake_ai(calls), publishers=fake_publishers(sent))
        self.assertEqual(res, {"wordpress": "DRY_OK", "blogger": "DRY_OK", "notion": "DRY_OK"})
        self.assertEqual(sent, [])
        drafts = os.listdir(P.DRAFT_DIR)
        self.assertEqual(len([d for d in drafts if d.endswith("_DRYRUN.md")]), 3)
        st = json.load(open(P.STATE_PATH))
        self.assertEqual(st["posts"], [])
        self.assertGreater(st["spent"][P.D.utc_today()], 0)

    def test_switch_off_means_dry_even_when_dry_run_false(self):
        P.DRY = False
        os.environ["PLATFORM_POSTER_LIVE"] = "false"
        sent = []
        res, _ = quiet(post_ai=fake_ai([]), publishers=fake_publishers(sent))
        self.assertEqual(sent, [])
        self.assertEqual(set(res.values()), {"DRY_OK"})

    def test_dry_limit_two_a_day(self):
        quiet(post_ai=fake_ai([]), publishers=fake_publishers([]))
        quiet(post_ai=fake_ai([]), publishers=fake_publishers([]))
        os.environ["PLATFORM_FIRST_RUN"] = "false"
        res, _ = quiet(post_ai=fake_ai([]), publishers=fake_publishers([]))
        self.assertEqual(res, "DRY_LIMIT")

    def test_live_publishes_three_and_records(self):
        P.DRY = False
        sent = []
        res, out = quiet(post_ai=fake_ai([]), publishers=fake_publishers(sent))
        self.assertEqual(res, {"wordpress": "PUBLISHED", "blogger": "PUBLISHED", "notion": "PUBLISHED"})
        self.assertEqual([s[0] for s in sent], P.PLATFORMS)
        st = json.load(open(P.STATE_PATH))
        self.assertEqual(len(st["posts"]), 3)
        self.assertEqual(len({p["slug"] for p in st["posts"]}), 3)
        self.assertEqual(len({s[1] for s in sent}), 3)
        self.assertEqual(len(st["own_backlinks_new"]), 3)
        self.assertTrue(all(e["source"].startswith("https://") and e["target"].startswith("/") for e in st["own_backlinks_new"]))
        rels = {p["platform"]: p["rel"] for p in st["posts"]}
        self.assertEqual(rels["wordpress"], "followed")
        self.assertTrue(rels["notion"].startswith("unknown"))
        self.assertFalse(st.get("pending"))
        # second run the same day: nothing more
        os.environ["PLATFORM_FIRST_RUN"] = "false"
        res2, _ = quiet(post_ai=fake_ai([]), publishers=fake_publishers(sent))
        self.assertEqual(res2, "ALREADY_DONE")
        self.assertEqual(len(sent), 3)

    def test_empty_url_still_counts_as_done_no_second_post(self):
        P.DRY = False
        sent = []
        pubs = fake_publishers(sent)
        orig = pubs["blogger"]
        pubs["blogger"] = lambda art: dict(orig(art), url="")
        res, _ = quiet(post_ai=fake_ai([]), publishers=pubs)
        self.assertEqual(res["blogger"], "PUBLISHED")
        st = json.load(open(P.STATE_PATH))
        bp = [p for p in st["posts"] if p["platform"] == "blogger"][0]
        self.assertTrue(bp["url_missing"])
        self.assertEqual(len(st["own_backlinks_new"]), 2)
        os.environ["PLATFORM_FIRST_RUN"] = "false"
        res2, _ = quiet(post_ai=fake_ai([]), publishers=pubs)
        self.assertEqual(res2, "ALREADY_DONE")
        self.assertEqual(len(sent), 3)

    def test_crash_prints_one_safe_line(self):
        orig = P.run
        P.run = lambda: (_ for _ in ()).throw(RuntimeError("token=%s" % SECRET))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                with self.assertRaises(SystemExit):
                    P.main()
        finally:
            P.run = orig
        self.assertIn("CRASH RuntimeError at", buf.getvalue())
        self.assertNotIn(SECRET, buf.getvalue())

    def test_qa_fail_publishes_nothing_and_saves_drafts(self):
        P.DRY = False
        sent = []
        res, _ = quiet(post_ai=fake_ai([], verdict="FAIL"), publishers=fake_publishers(sent))
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertEqual(sent, [])
        self.assertTrue(any(d.endswith("_FAILED.md") for d in os.listdir(P.DRAFT_DIR)))

    def test_gate_fail_retries_once_then_fails(self):
        P.DRY = False
        calls, sent = [], []
        bad = lambda pl, tgt: body_for(pl, tgt, extra=" We saw 900% growth.")
        res, _ = quiet(post_ai=fake_ai(calls, bodyfn=bad), publishers=fake_publishers(sent))
        self.assertEqual(sent, [])
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertLessEqual(calls.count(D.WRITER), 6)

    def test_cap_stops_before_any_call(self):
        P.DAY_CAP = 0.02
        calls = []
        res, _ = quiet(post_ai=fake_ai(calls))
        self.assertEqual((res, calls), ("DAY_CAP", []))

    def test_daily_cap_never_exceeded(self):
        P.DRY = False
        P.DAY_CAP = 0.30                           # room for one article only
        calls, sent = [], []
        quiet(post_ai=fake_ai(calls), publishers=fake_publishers(sent))
        st = json.load(open(P.STATE_PATH))
        self.assertLessEqual(st["spent"][D.utc_today()], 0.30 + 1e-9)
        self.assertLess(len(sent), 3)

    def test_missing_keys_skip_platform(self):
        P.DRY = False
        old = os.environ.pop("NOTION_TOKEN")
        try:
            sent = []
            quiet(post_ai=fake_ai([]), publishers=fake_publishers(sent))
            self.assertEqual([s[0] for s in sent], ["wordpress", "blogger"])
        finally:
            os.environ["NOTION_TOKEN"] = old

    def test_publish_timeout_keeps_pending_and_blocks_repeat(self):
        P.DRY = False

        def boom(art):
            raise TimeoutError()
        pubs = fake_publishers([])
        pubs["wordpress"] = boom
        res, _ = quiet(post_ai=fake_ai([]), publishers=pubs)
        self.assertEqual(res["wordpress"], "UNKNOWN")
        st = json.load(open(P.STATE_PATH))
        self.assertIn("wordpress", st["pending"])
        os.environ["PLATFORM_FIRST_RUN"] = "false"
        sent = []
        quiet(post_ai=fake_ai([]), publishers=fake_publishers(sent))
        self.assertNotIn("wordpress", [s[0] for s in sent])

    def test_no_secret_in_output_log_state_or_drafts(self):
        P.DRY = False
        _, out = quiet(post_ai=fake_ai([]), publishers=fake_publishers([]))
        blob = out
        for root, _, files in os.walk(os.environ["PLATFORM_OPS_DIR"]):
            for f in files:
                blob += open(os.path.join(root, f), encoding="utf8").read()
        self.assertNotIn(SECRET, blob)


class Publishers(unittest.TestCase):
    ART = {"title": "T", "body": "## H\n\nText with [our page](https://inayaflow.ai/tools/x) here.\n\n- a\n- b\n"}

    def test_wordpress_flow(self):
        calls = []

        def req(url, body=None, headers=None, form=False):
            calls.append((url, body, headers, form))
            if "oauth2/token" in url:
                return {"access_token": "BEARER"}
            return {"link": "https://inayaflow.wordpress.com/2026/11/24/t/", "id": 5}
        r = P.publish_wordpress(self.ART, req=req)
        self.assertEqual(r["url"], "https://inayaflow.wordpress.com/2026/11/24/t/")
        tok = calls[0]
        self.assertTrue(tok[3])
        self.assertEqual(tok[1]["grant_type"], "password")
        self.assertEqual(tok[1]["username"], os.environ["WPCOM_USERNAME"])
        self.assertTrue(calls[1][0].endswith("/wp/v2/sites/inayaflow.wordpress.com/posts"))
        self.assertEqual(calls[1][2]["Authorization"], "Bearer BEARER")
        self.assertEqual(calls[1][1]["status"], "publish")
        self.assertNotIn("rel=", calls[1][1]["content"])

    def test_wordpress_falls_back_only_on_4xx(self):
        seen = []

        def req(url, body=None, headers=None, form=False):
            seen.append(url)
            if "oauth2/token" in url:
                return {"access_token": "B"}
            if "wp/v2" in url:
                raise urllib.error.HTTPError(url, 403, "no", {}, None)
            return {"URL": "https://inayaflow.wordpress.com/x/", "ID": 9}
        self.assertEqual(P.publish_wordpress(self.ART, req=req)["id"], 9)

        def req5(url, body=None, headers=None, form=False):
            if "oauth2/token" in url:
                return {"access_token": "B"}
            raise urllib.error.HTTPError(url, 500, "no", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            P.publish_wordpress(self.ART, req=req5)

    def test_blogger_flow(self):
        calls = []

        def req(url, body=None, headers=None, form=False):
            calls.append((url, body))
            if "oauth2.googleapis.com" in url:
                return {"access_token": "A"}
            if "byurl" in url:
                return {"id": "123"}
            return {"url": "https://inayaflow.blogspot.com/2026/11/t.html", "id": "p"}
        r = P.publish_blogger(self.ART, req=req)
        self.assertEqual(r["url"], "https://inayaflow.blogspot.com/2026/11/t.html")
        self.assertEqual(calls[0][1]["grant_type"], "refresh_token")
        self.assertTrue(calls[2][0].endswith("/blogs/123/posts"))
        self.assertIn('<a href="https://inayaflow.ai/tools/x">our page</a>', calls[2][1]["content"])

    def test_notion_flow(self):
        calls = []

        def req(url, body=None, headers=None, form=False):
            calls.append((url, body, headers))
            return {"id": "aaaabbbb-cccc-dddd-eeee-ffff00001111"}
        r = P.publish_notion(self.ART, req=req)
        self.assertEqual(r["url"], "https://breezy-split-a72.notion.site/aaaabbbbccccddddeeeeffff00001111")
        self.assertEqual(calls[0][1]["parent"], {"page_id": "420a26c43c8d80e1bb0bc861e6304200"})
        self.assertIn("Notion-Version", calls[0][2])
        self.assertEqual(calls[0][1]["children"][1]["paragraph"]["rich_text"][1]["text"]["link"]["url"], "https://inayaflow.ai/tools/x")


class Config(unittest.TestCase):
    def dry_expr(self):
        t = open(os.path.join(HERE, "..", "..", ".github", "workflows", "platform-poster.yml"), encoding="utf8").read()
        e = re.search(r"DRY_RUN: \$\{\{ (.+?) \}\}", t).group(1)
        py = e.replace("&&", " and ").replace("||", " or ").replace("vars.PLATFORM_POSTER_LIVE", "V").replace("github.event_name", "E").replace("github.event.inputs.dry_run", "I")
        return lambda V, E, I: eval(py, {"V": V, "E": E, "I": I})

    def test_dry_run_expression_truth_table(self):
        f = self.dry_expr()
        self.assertEqual(f("", "schedule", ""), "true")              # switch not set: always dry
        self.assertEqual(f("false", "schedule", ""), "true")
        self.assertEqual(f("", "workflow_dispatch", "false"), "true")
        self.assertEqual(f("true", "schedule", ""), "false")         # live + schedule: publish
        self.assertEqual(f("true", "workflow_dispatch", ""), "true")  # manual default: dry
        self.assertEqual(f("true", "workflow_dispatch", "true"), "true")
        self.assertEqual(f("true", "workflow_dispatch", "false"), "false")


    def test_workflow_switch_cron_and_no_secret_echo(self):
        t = open(os.path.join(HERE, "..", "..", ".github", "workflows", "platform-poster.yml"), encoding="utf8").read()
        self.assertIn('cron: "41 9 * * *"', t)
        self.assertIn("vars.PLATFORM_POSTER_LIVE == 'true'", t)
        self.assertIn("workflow_dispatch", t)
        self.assertIn("python3 scripts/platform_poster/poster.py 2>/dev/null", t)
        self.assertNotIn("echo \"$WPCOM", t)
        self.assertRegex(t, r'PLATFORM_DAY_USD_CAP: "0\.\d\d"')


class AnchorMix(unittest.TestCase):
    """Link text mix (RULEBOOK 9a): 10-post cycle per target page."""
    SLUG, URL = "shadowban-checker", "https://inayaflow.ai/tools/tiktok-shadowban-checker"
    A = P.D

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

    def test_check_post_enforces_the_type(self):
        def art_with(txt):
            b = body_for("wordpress", T).replace("[InayaFlow's free checker](%s)" % T, "[%s](%s)" % (txt, T))
            return art(body=b)
        addr = D.anchor_info("hashtag-generator", T, 2)
        self.assertEqual([x for x in probs(art_with("inayaflow.ai/tools/tiktok-hashtag-generator"), T, anchor=addr) if "link text" in x or "bare" in x], [])
        self.assertTrue(any("link text" in x for x in probs(art_with("free hashtag tool"), T, anchor=addr)))
        self.assertEqual([x for x in probs(art_with("InayaFlow"), T, anchor=D.anchor_info("hashtag-generator", T, 0)) if "link text" in x], [])
        self.assertTrue(any("link text" in x for x in probs(art_with("InayaFlow"), T, anchor=D.anchor_info("hashtag-generator", T, 9))))
        extra = body_for("wordpress", T, extra=" Visit inayaflow.ai today.").replace("[InayaFlow's free checker](%s)" % T, "[inayaflow.ai/tools/tiktok-hashtag-generator](%s)" % T)
        self.assertTrue(any("bare" in x for x in probs(art(body=extra), T, anchor=addr)))
        self.assertFalse(any("link text" in x for x in probs(art(), T)))        # no anchor given = old behaviour

    def test_prompt_has_the_link_text_line(self):
        s = P.writer_system("wordpress")
        self.assertIn("[LINK TEXT](URL)", s)
        self.assertNotIn("Never write the bare text inayaflow.ai.", s)

    def test_cursor_is_shared_across_platforms(self):
        st = {"posts": [{"platform": "wordpress", "slug": "homepage"}, {"platform": "blogger", "slug": "homepage"}]}
        self.assertEqual(D.anchor_type(D.anchor_count(st, "homepage"), "homepage"), "brand")


# ---------- repair of overlap / first person (24 Nov) ----------
COPY = "\n\nFree TikTok tool page with plain explanations of how the checker works and what it needs."
FIRST = "\n\nI think we should always check our hashtags before posting anything."


def ai_with_repair(calls, extra, repair_text, qa_raw=None):
    """Writer always returns a body carrying `extra`; the Haiku repair call answers with repair_text for sentence 1; QA passes (or answers qa_raw)."""
    base = fake_ai(calls, bodyfn=lambda pl, tgt: body_for(pl, tgt, extra=extra))

    def post(url, b, h):
        if b["model"] == D.QA_MODEL and "copy editor" in b["system"]:
            calls.append("repair")
            uniq = " " + filler("rr" + chr(97 + len(calls)), 12) + "."          # each platform's repair differs, so the articles do not overlap each other
            return {"content": [{"type": "text", "text": json.dumps({"1": repair_text[4:] if repair_text.startswith("RAW:") else repair_text.split(" ")[0] + uniq})}], "usage": {"input_tokens": 300, "output_tokens": 40}}
        if b["model"] == D.QA_MODEL and qa_raw is not None:
            calls.append(b["model"])
            return {"content": [{"type": "text", "text": qa_raw}], "usage": {"input_tokens": 4000, "output_tokens": 50}}
        return base(url, b, h)
    return post


class Repair(unittest.TestCase):
    def setUp(self):
        for f in (P.STATE_PATH, P.LOG_PATH):
            if os.path.exists(f):
                os.remove(f)
        P.DRY = False
        P.DAY_CAP = 0.90
        os.environ["PLATFORM_STATE_OK"] = "true"
        os.environ["PLATFORM_FIRST_RUN"] = "true"
        os.environ["PLATFORM_POSTER_LIVE"] = "true"

    def test_unit_gate_flags_the_copy(self):
        a = art(body=body_for("wordpress", T, extra=COPY))
        pr = probs(a, T)
        self.assertTrue(repairable(pr) if (repairable := P.repairable) else False)
        self.assertTrue(any("overlap with our site" in x for x in pr))

    def test_repairable_kinds(self):
        self.assertTrue(P.repairable(["10-word overlap with our site: 'x'", "first person singular (use 'we')"]))
        self.assertTrue(P.repairable(["10-word overlap with our site: 'x'", "number '5%' not found in the fetched sources"]))      # 26 Nov: an unsourced figure is dropped by a rewrite
        self.assertTrue(P.repairable(["banned phrase: take your", "names a mode/plan/tier that is not on our page: 'Starter Mode'"]))
        self.assertFalse(P.repairable(["10-word overlap with our site: 'x'", "words 34 outside 350-650"]))
        self.assertFalse(P.repairable(["need exactly one inayaflow.ai link and it must be the target (0 found)"]))
        self.assertFalse(P.repairable([]))

    def test_sentence_spans_skip_headings(self):
        b = "## Head one. Not a sentence\n\nOne here. Two here!\n- item one. item two"
        got = [b[x:y] for x, y in P.sentence_spans(b)]
        self.assertEqual(got, ["One here.", "Two here!", "- item one.", "item two"])

    def test_overlap_is_repaired_and_published(self):
        calls, sent = [], []
        res, out = quiet(post_ai=ai_with_repair(calls, COPY, "Our checker page explains in simple terms how it reads a profile."), publishers=fake_publishers(sent))
        self.assertEqual(res["wordpress"], "PUBLISHED")
        self.assertIn("repair wordpress problems 1 -> 0", out)
        self.assertEqual(calls.count("repair"), 3)                    # one repair per platform, no second writer call
        self.assertEqual(calls.count(D.WRITER), 3)
        self.assertEqual(len(sent), 3)

    def test_first_person_is_repaired(self):
        calls, sent = [], []
        res, out = quiet(post_ai=ai_with_repair(calls, FIRST, "We think everyone should check their hashtags before posting anything."), publishers=fake_publishers(sent))
        self.assertEqual(set(res.values()), {"PUBLISHED"})
        self.assertIn("problems 1 -> 0", out)

    def test_repair_changes_only_the_bad_sentence(self):
        meter = D.Meter(cap=0.9)
        a = art(body=body_for("wordpress", T, extra=COPY))
        fixed = P.repair_article(a, SITE_TEXT * 2, [], meter, post=ai_with_repair([], COPY, "Our checker page explains in simple terms how it reads a profile."))
        self.assertIsNotNone(fixed)
        self.assertEqual(fixed["body"].replace(re.search(r"Our rr[^\n]*", fixed["body"]).group(0), COPY.strip()), a["body"])
        self.assertEqual(fixed["title"], a["title"])

    def test_still_failing_after_the_repair_rounds_is_failed_as_before(self):
        calls, sent = [], []
        res, out = quiet(post_ai=ai_with_repair(calls, COPY, "RAW:Free TikTok tool page with plain explanations of how the checker works and what it needs."), publishers=fake_publishers(sent))
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertEqual(sent, [])
        self.assertEqual(calls.count("repair"), 6)                    # at most 2 rewrites per platform, shared by both writer attempts
        log = open(P.LOG_PATH, encoding="utf8").read()
        self.assertIn("overlap with our site", log)
        self.assertTrue([d for d in os.listdir(P.DRAFT_DIR) if d.endswith("_FAILED.md")])

    def test_repair_that_adds_a_stray_link_never_publishes_the_link(self):
        calls, bodies = [], []
        bad = "Read [more here](https://elsewhere.example.com/x) about it."          # a stray external link: code turns it into plain text (no source was opened)
        pubs = {pl: (lambda pl: lambda a: (bodies.append(a["body"]), {"url": "https://live.example.net/%s/p" % pl, "id": 1})[1])(pl) for pl in P.PLATFORMS}
        res, _ = quiet(post_ai=ai_with_repair(calls, COPY, "RAW:" + bad), publishers=pubs)
        self.assertTrue(bodies)
        self.assertTrue(all("elsewhere.example.com" not in b for b in bodies))

    def test_banned_phrase_is_repaired_not_thrown_away(self):
        calls = []
        res, out = quiet(post_ai=ai_with_repair(calls, "\n\nWe say delve into it.", "x"), publishers=fake_publishers([]))
        self.assertEqual(set(res.values()), {"PUBLISHED"})
        self.assertEqual(calls.count("repair"), 3)
        self.assertEqual(calls.count(D.WRITER), 3)

    def test_repair_not_tried_for_other_problems(self):
        calls = []
        res, _ = quiet(post_ai=ai_with_repair(calls, " " + filler("long", 400), "x"), publishers=fake_publishers([]))     # too long: only the writer can fix it
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertEqual(calls.count("repair"), 0)

    def test_truncated_qa_reply_is_read_leniently(self):
        calls, sent = [], []
        raw = '{"verdict":"PASS","reasons":["all numbers match the lin'          # cut off at the token limit
        res, _ = quiet(post_ai=ai_with_repair(calls, "", "x", qa_raw=raw), publishers=fake_publishers(sent))
        self.assertEqual(set(res.values()), {"PUBLISHED"})

    def test_cap_error_inside_repair_stops_cleanly_and_publishes_nothing(self):
        orig = P.repair_article

        def boom(*a, **k):
            raise D.CapReached("daily cost cap")
        P.repair_article = boom
        try:
            sent, calls = [], []
            res, out = quiet(post_ai=ai_with_repair(calls, COPY, "x"), publishers=fake_publishers(sent))
        finally:
            P.repair_article = orig
        self.assertEqual(sent, [])
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertEqual(calls.count(D.WRITER), 3)                     # no second writer try after the cap hit
        self.assertIn("cost cap reached during repair", open(P.LOG_PATH, encoding="utf8").read())

    def test_repair_is_skipped_when_it_cannot_be_afforded(self):
        P.DAY_CAP = 0.50                      # room for the writer + QA only, not for the extra repair + QA
        calls, sent = [], []
        res, _ = quiet(post_ai=ai_with_repair(calls, COPY, "x"), publishers=fake_publishers(sent))
        self.assertEqual(res["wordpress"], "FAIL")                     # the first platform could not afford a repair, so it is FAILED, not repaired
        self.assertLessEqual(calls.count("repair"), 2)                 # not for the first platform (it would starve the later ones)
        self.assertLessEqual(json.load(open(P.STATE_PATH))["spent"][D.utc_today()], 0.50 + 1e-9)

    def test_qa_lenient_rules(self):
        self.assertEqual(D.qa_lenient('{"verdict": "FAIL", "reasons": ["the number is wrong here"')["verdict"], "FAIL")
        self.assertEqual(D.qa_lenient("PASS. Everything matches.")["verdict"], "PASS")
        self.assertEqual(D.qa_lenient("PASS, but one claim could FAIL")["verdict"], "FAIL")      # FAIL wins
        self.assertEqual(D.qa_lenient('{"verdict":"PASS","reasons":[]} {"verdict":"FAIL","reasons":["a number is invented"')["verdict"], "FAIL")
        self.assertEqual(D.qa_lenient('{"verdict":"FAIL","reasons":["x is wrong"]} then {"verdict":"PASS"')["verdict"], "FAIL")
        self.assertIsNone(D.qa_lenient("looks fine to me"))
        self.assertIsNone(D.qa_lenient(""))


# ---------- 25 Nov: why every draft failed on 24 Nov, and the fixes (facts bullets, 2 rewrites, tolerant QA with strict retry, QA-claim rewrite) ----------
SITE2 = (SITE_TEXT + " Is eligibility for the for you feed a post or a profile question is answered on this page in plain words. "
         "Followers is the size of the audience and likes show how many people reacted to a video.")
CLAIM = "\n\nThe tool tells readers to use TikTok Studio for those limits."
OVER = "\n\nPeople ask is eligibility for the for you feed a post or a profile question every week."


def get2(url, timeout=15, limit=0):
    if url.startswith("https://inayaflow.ai"):
        return 200, url, "<html><body><p>%s</p></body></html>" % SITE2
    return fake_get(url)


def smart_ai(calls, extra="", qa_mode="ok", facts_reply=None, seen=None, repair_text=None):
    """Scripted Anthropic: writer body carries `extra`; Haiku answers facts / overlap-repair / qa-repair / QA by what the system prompt asks."""
    base = fake_ai(calls, bodyfn=lambda pl, tgt: body_for(pl, tgt, extra=extra))
    n = {"qa": 0, "rep": 0}

    def text(t):
        return {"content": [{"type": "text", "text": t}], "usage": {"input_tokens": 3000, "output_tokens": 80}}

    def post(url, b, h):
        sysm = b["system"]
        user = b["messages"][0]["content"]
        if b["model"] == D.QA_MODEL:
            if "notes for a writer" in sysm:
                calls.append("facts")
                return text(facts_reply if facts_reply is not None else "- The page is a free checker that explains its result in plain words.\n- It needs nothing but the public details.\n- It is a helper, not a promise of results.\n- The page also explains what the numbers mean for a creator.")
            if "fact-checker found" in sysm:
                calls.append("qa_repair")
                drop = {}
                for i, l in re.findall(r"^(\d+)\. (.*)$", user, re.M):
                    if "TikTok Studio" in l:
                        drop[i] = ""
                return text(json.dumps(drop))
            if "copy editor" in sysm:
                calls.append("repair")
                n["rep"] += 1
                sent = re.findall(r"^(\d+)\. \[[^\]]*\] (.*)$", user, re.M)
                if seen is not None:
                    seen.append(user)
                if repair_text == "same":
                    return text(json.dumps({k: v for k, v in sent}))
                return text(json.dumps({k: "%s we cover it." % filler("rw" + chr(97 + n["rep"] % 26) + chr(97 + int(k) % 26), 14) for k, _ in sent}))
            calls.append("qa")
            n["qa"] += 1
            claim = "TikTok Studio" in user.split("ARTICLE TITLE:", 1)[-1]
            if qa_mode == "garbled" and "Your reply must start" not in sysm:
                return text("Let me read this article carefully. The first section discusses captions and the second")
            if qa_mode == "never":
                return text("I am unable to decide.")
            if claim:
                return text('VERDICT: FAIL\nREASON: "The tool tells readers to use TikTok Studio for those limits." | not in our page text')
            return text("VERDICT: PASS")
        return base(url, b, h)
    return post


class Fixes25Nov(unittest.TestCase):
    def setUp(self):
        for f in (P.STATE_PATH, P.LOG_PATH):
            if os.path.exists(f):
                os.remove(f)
        P.DRY = False
        P.DAY_CAP = 0.90
        os.environ["PLATFORM_STATE_OK"] = "true"
        os.environ["PLATFORM_FIRST_RUN"] = "true"
        os.environ["PLATFORM_POSTER_LIVE"] = "true"

    def run_flow(self, **kw):
        calls, sent = [], []
        res, out = quiet(post_ai=smart_ai(calls, **kw), publishers=fake_publishers(sent), getter=get2)
        return res, out, calls, sent

    # failure kind 1: a 10-word run copied from our site (log rows 1-7)
    def test_site_overlap_is_rewritten_and_publishes(self):
        res, out, calls, sent = self.run_flow(extra=OVER)
        self.assertEqual(set(res.values()), {"PUBLISHED"})
        self.assertIn("repair wordpress problems 1 -> 0", out)
        self.assertEqual(len(sent), 3)

    # the rewrite prompt carries the exact offending phrase
    def test_repair_prompt_lists_the_exact_phrase(self):
        seen = []
        self.run_flow(extra=OVER, seen=seen)
        self.assertTrue(seen and "is eligibility for the for you feed a post or" in seen[0])

    # a rewrite that still copies gets a SECOND rewrite attempt, then fails honestly
    def test_two_rewrites_then_honest_fail(self):
        res, out, calls, sent = self.run_flow(extra=OVER, repair_text="same")
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertEqual(sent, [])
        self.assertEqual(calls.count("repair"), 6)

    # failure kind 2: first person
    def test_first_person_rewritten(self):
        res, out, calls, sent = self.run_flow(extra="\n\nI think we should always check our hashtags before posting anything.")
        self.assertEqual(set(res.values()), {"PUBLISHED"})

    # failure kind 3: 'QA answer not understood' (cut-off prose): the strict retry now reads
    def test_unreadable_qa_gets_a_strict_retry(self):
        res, out, calls, sent = self.run_flow(qa_mode="garbled")
        self.assertEqual(set(res.values()), {"PUBLISHED"})
        self.assertEqual(calls.count("qa"), 6)                         # two asks per platform

    def test_qa_never_readable_fails_with_the_old_words(self):
        res, out, calls, sent = self.run_flow(qa_mode="never")
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertIn("QA answer not understood", open(P.LOG_PATH, encoding="utf8").read())

    # failure kind 4: a claim about the tool that is not on our page: the named sentence is removed, QA runs again, PASS
    def test_unsupported_claim_sentence_is_removed(self):
        res, out, calls, sent = self.run_flow(extra=CLAIM)
        self.assertEqual(set(res.values()), {"PUBLISHED"})
        self.assertEqual(calls.count("qa_repair"), 3)

    # ... and when the false claim cannot be removed it stays a FAIL (the honesty rule is not loosened)
    def test_unremovable_false_claim_still_fails(self):
        base = smart_ai([], extra=CLAIM)

        def never_fix(url, b, h):
            if "fact-checker found" in b["system"]:
                return {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 100, "output_tokens": 5}}
            return base(url, b, h)
        sent = []
        res, _ = quiet(post_ai=never_fix, publishers=fake_publishers(sent), getter=get2)
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertEqual(sent, [])
        self.assertIn("TikTok Studio", open(P.LOG_PATH, encoding="utf8").read())

    def test_parse_qa_forms(self):
        self.assertEqual(P.parse_qa("VERDICT: PASS")[0], "PASS")
        self.assertEqual(P.parse_qa("**VERDICT:** PASS\n")[0], "PASS")
        self.assertEqual(P.parse_qa("PASS")[0], "PASS")
        self.assertEqual(P.parse_qa('{"verdict":"PASS","reasons":[]}')[0], "PASS")
        v, r = P.parse_qa('```json\n{"verdict":"FAIL","reasons":["a number is invented"]}\n```')
        self.assertEqual((v, r), ("FAIL", ["a number is invented"]))
        v, r = P.parse_qa('VERDICT: FAIL\nREASON: "tool says x" | not on page\nREASON: "y" | invented')
        self.assertEqual(v, "FAIL")
        self.assertEqual(len(r), 2)
        self.assertEqual(P.parse_qa("VERDICT: PASS but this could FAIL a reviewer")[0], "FAIL")      # FAIL wins
        self.assertIsNone(P.parse_qa("Let me read this carefully. The first section")[0])
        self.assertIsNone(P.parse_qa("")[0])

    def test_fact_bullets_filter_copies(self):
        page = "Free TikTok tool page with plain explanations of how the checker works and what it needs."
        raw = "- Free TikTok tool page with plain explanations of how the checker works\n- A helper that shows a result in simple words.\n- ok"
        self.assertEqual(P.fact_bullets(raw, page), ["A helper that shows a result in simple words."])

    def _spy(self, **kw):
        seen = []
        base = smart_ai([], **kw)

        def spy(url, b, h):
            if b["model"] == D.WRITER:
                seen.append(b["messages"][0]["content"])
            return base(url, b, h)
        res, _ = quiet(post_ai=spy, publishers=fake_publishers([]), getter=get2)
        return res, seen

    def test_writer_gets_facts_not_the_page_sentences(self):
        res, seen = self._spy()
        self.assertNotIn("plain explanations of how the checker works", seen[0])
        self.assertIn("FACTS ABOUT OUR PAGE", seen[0])
        self.assertIn("free checker that explains its result", seen[0])

    def test_bad_facts_fall_back_to_page_text(self):
        res, seen = self._spy(facts_reply="- Free TikTok tool page with plain explanations of how the checker works and what it needs.")
        self.assertIn("plain explanations of how the checker works", seen[0])
        self.assertEqual(set(res.values()), {"PUBLISHED"})

    def test_cost_stays_inside_the_cap_with_repairs(self):
        self.run_flow(extra="\n\nI think we should always check our hashtags before posting anything." + CLAIM)
        self.assertLessEqual(json.load(open(P.STATE_PATH))["spent"][D.utc_today()], 0.90 + 1e-9)


# ---------- 25 Nov round 2: facts that survive, 2 QA rewrite rounds, cheap second attempt, full reasons in the private log ----------
class Round2(unittest.TestCase):
    def setUp(self):
        for f in (P.STATE_PATH, P.LOG_PATH):
            if os.path.exists(f):
                os.remove(f)
        P.DRY = False
        P.DAY_CAP = 0.90
        os.environ["PLATFORM_STATE_OK"] = "true"
        os.environ["PLATFORM_FIRST_RUN"] = "true"
        os.environ["PLATFORM_POSTER_LIVE"] = "true"

    def test_bullets_sharing_a_short_phrase_are_kept(self):
        page = "Our checker looks at the last videos and shows how many views came from the for you feed in a plain table."
        raw = "- It reads the last videos and shows views from the for you feed\n- Result is a plain table\n- Nothing to install"
        got = P.fact_bullets(raw, page)
        self.assertEqual(len(got), 3)                      # old 5-word filter dropped the first one; only a 7-word run is a copy now

    def test_distill_retries_once_when_too_few_survive(self):
        page = "Free TikTok tool page with plain explanations of how the checker works and what it needs. " * 3
        replies = ["- Free TikTok tool page with plain explanations of how the checker works\n- Needs only a public handle",
                   "- A helper that reads a public profile.\n- It explains the result in simple terms.\n- It is free to use.\n- It cannot promise reach."]
        users = []

        def post(url, b, h):
            users.append(b["messages"][0]["content"])
            return {"content": [{"type": "text", "text": replies[len(users) - 1]}], "usage": {"input_tokens": 1500, "output_tokens": 120}}
        m = D.Meter(cap=0.9)
        with contextlib.redirect_stdout(io.StringIO()):
            facts = P.distill_facts(page, m, post=post)
        self.assertEqual(len(users), 2)
        self.assertIn("too close to the page", users[1])
        self.assertIn("Needs only a public handle", facts)
        self.assertIn("It cannot promise reach.", facts)
        self.assertLess(m.cost, 0.01)

    def test_distill_gives_up_cleanly(self):
        page = "Free TikTok tool page with plain explanations of how the checker works and what it needs. " * 3
        post = lambda url, b, h: {"content": [{"type": "text", "text": "- Free TikTok tool page with plain explanations of how the checker works"}], "usage": {"input_tokens": 1500, "output_tokens": 50}}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(P.distill_facts(page, D.Meter(cap=0.9), post=post), "")

    def test_multiline_reason_is_kept_whole(self):
        raw = 'VERDICT: FAIL\nREASON: "Content and entire\nsentence continues here" | not in our page text\nREASON: "second" | invented'
        v, r = P.parse_qa(raw)
        self.assertEqual(v, "FAIL")
        self.assertEqual(len(r), 2)
        self.assertIn("Content and entire sentence continues here", r[0])

    def test_private_log_keeps_the_full_reason(self):
        long_reason = "x" * 20
        claim = "The tool tells readers to use TikTok Studio for those limits."
        base = smart_ai([], extra=CLAIM)

        def post(url, b, h):
            if b["model"] == D.QA_MODEL and "fact-check editor" in b["system"] and "TikTok Studio" in b["messages"][0]["content"]:
                return {"content": [{"type": "text", "text": 'VERDICT: FAIL\nREASON: "%s" | %s' % (claim, "not on our page " * 30 + long_reason)}], "usage": {"input_tokens": 100, "output_tokens": 50}}
            if "fact-checker found" in b["system"]:
                return {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 100, "output_tokens": 5}}
            return base(url, b, h)
        res, _ = quiet(post_ai=post, publishers=fake_publishers([]), getter=get2)
        self.assertEqual(set(res.values()), {"FAIL"})
        self.assertIn(long_reason, open(P.LOG_PATH, encoding="utf8").read())

    def test_second_qa_rewrite_round_removes_a_newly_named_claim(self):
        """QA names claim A, the rewrite removes it, QA then names claim B, the second round removes it, QA passes."""
        A, B = "The tool tells readers to use TikTok Studio for those limits.", "Two tags beat five tags every single time for reach."
        extra = "\n\n" + A + "\n\n" + B
        base = smart_ai([], extra=extra)
        rounds = {"n": 0}

        def post(url, b, h):
            u = b["messages"][0]["content"]
            if b["model"] == D.QA_MODEL and "fact-check editor" in b["system"]:
                art = u.split("ARTICLE TITLE:", 1)[-1]
                if "TikTok Studio" in art:
                    return {"content": [{"type": "text", "text": 'VERDICT: FAIL\nREASON: "%s" | not on page' % A}], "usage": {"input_tokens": 100, "output_tokens": 30}}
                if "beat five tags" in art:
                    return {"content": [{"type": "text", "text": 'VERDICT: FAIL\nREASON: "%s" | unsupported comparison' % B}], "usage": {"input_tokens": 100, "output_tokens": 30}}
                return {"content": [{"type": "text", "text": "VERDICT: PASS"}], "usage": {"input_tokens": 100, "output_tokens": 5}}
            if "fact-checker found" in b["system"]:
                rounds["n"] += 1
                drop = {i: "" for i, l in re.findall(r"^(\d+)\. (.*)$", u, re.M) if ("TikTok Studio" in l and "TikTok Studio" in u.split("FACT-CHECKER REASONS:")[1].split("SENTENCES:")[0]) or ("beat five" in l and "comparison" in u)}
                return {"content": [{"type": "text", "text": json.dumps(drop)}], "usage": {"input_tokens": 200, "output_tokens": 20}}
            return base(url, b, h)
        sent = []
        res, out = quiet(post_ai=post, publishers=fake_publishers(sent), getter=get2)
        self.assertEqual(set(res.values()), {"PUBLISHED"})
        self.assertGreaterEqual(rounds["n"], 6)            # two rounds on each of the 3 platforms

    def test_attempt_two_is_a_toolless_revision_not_a_second_research_run(self):
        calls = []
        base = smart_ai(calls, extra=" " + filler("long", 400))          # too long: not repairable, so attempt 2 (the revision) runs
        bodies = []

        def spy(url, b, h):
            if b["model"] == D.WRITER:
                bodies.append(b)
            return base(url, b, h)
        old = P.WRITER_WEB
        P.WRITER_WEB = True                                # the older research writer, switched on by PLATFORM_WRITER_WEB=true
        try:
            quiet(post_ai=spy, publishers=fake_publishers([]), getter=get2)
        finally:
            P.WRITER_WEB = old
        with_tools = [b for b in bodies if b.get("tools")]
        without = [b for b in bodies if not b.get("tools")]
        self.assertEqual(len(with_tools), 3)               # one research run per platform
        self.assertEqual(len(without), 3)                  # the retry is a revision with no web tools
        self.assertIn("YOUR DRAFT", without[0]["messages"][0]["content"])
        self.assertIn("outside", without[0]["messages"][0]["content"])

    def test_default_writer_has_no_web_tools_and_gets_effort_and_room(self):
        bodies = []
        base = smart_ai([])

        def spy(url, b, h):
            bodies.append(b)
            return base(url, b, h)
        quiet(post_ai=spy, publishers=fake_publishers([]), getter=get2)
        w = [b for b in bodies if b["model"] == D.WRITER]
        self.assertTrue(w and all(not b.get("tools") for b in w))
        self.assertTrue(all(b["output_config"]["effort"] == P.WRITER_EFFORT and b["max_tokens"] >= 6000 for b in w))
        self.assertIn("NO web access", w[0]["system"])
        h = [b for b in bodies if b["model"] == D.QA_MODEL]
        self.assertTrue(h and all(b["max_tokens"] >= 3000 and b["output_config"].get("effort") for b in h))     # Haiku thinks: room + explicit effort
        self.assertTrue(all(b["output_config"]["format"]["type"] == "json_schema" for b in h))

    def test_writer_prompt_forbids_comparisons_and_invented_features(self):
        s = P.writer_system("notion")
        self.assertIn("No comparisons or performance claims", s)
        self.assertIn("unless a FACT states it exactly", s)


# ---------- 26 Nov round 3: why 0 articles were ever published, and the fixes ----------
def hk(text, stop="end_turn", out=80):
    return {"content": [{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": text}], "stop_reason": stop,
            "usage": {"input_tokens": 3000, "output_tokens": out}}


class Round3(unittest.TestCase):
    def setUp(self):
        for f in (P.STATE_PATH, P.LOG_PATH):
            if os.path.exists(f):
                os.remove(f)
        P.DRY = False
        P.DAY_CAP = 0.90
        os.environ["PLATFORM_STATE_OK"] = "true"
        os.environ["PLATFORM_FIRST_RUN"] = "true"
        os.environ["PLATFORM_POSTER_LIVE"] = "true"

    # the 25 Nov wordpress draft: the model wrote the article, then a second delimiter and an 'Oops' note; the old parser kept the note as the body
    def test_parse_writer_keeps_the_article_not_the_trailing_note(self):
        art_body = body_for("wordpress", T)
        raw = ("Let us write it.\nTITLE: Sudden view drop? A calm check\nSOURCES: none\n===BODY===\n" + art_body +
               "\n===BODY===\nOops, I accidentally included an extra delimiter at the end of that. Here is the clean version of the output above.")
        a = P.parse_writer(raw)
        self.assertEqual(a["title"], "Sudden view drop? A calm check")
        self.assertEqual(a["body"].strip(), art_body.strip())
        self.assertEqual(a["sources"], [])
        b = P.parse_writer("**TITLE:** T2\nSOURCES: https://a.example.org/x | https://b.example.org/y\n=== BODY ===\nShort body here.")
        self.assertEqual((b["title"], b["sources"], b["body"]), ("T2", ["https://a.example.org/x", "https://b.example.org/y"], "Short body here."))
        self.assertIsNone(P.parse_writer("no format at all"))

    def test_ask_json_reads_a_schema_reply_after_thinking_and_retries_a_cut_reply_with_more_room(self):
        seen = []
        replies = [hk("", stop="max_tokens", out=4000), hk('{"facts":["One fact here in words.","Two fact here in words.","Three fact here in words."]}')]

        def post(url, b, h):
            seen.append(b)
            return replies[len(seen) - 1]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            val, _ = P.ask_json(P.FACTS_SYSTEM, "PAGE", P.FACTS_SCHEMA, D.Meter(cap=0.9), P._fact_candidates, post=post, label="facts")
        self.assertEqual(len(val), 3)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[1]["max_tokens"], 2 * seen[0]["max_tokens"])
        self.assertIn("stop=max_tokens", out.getvalue())
        self.assertEqual(seen[0]["output_config"]["format"]["schema"], P.FACTS_SCHEMA)

    def test_ask_json_falls_back_to_plain_json_when_the_schema_is_refused(self):
        seen = []

        def post(url, b, h):
            seen.append(b)
            if "format" in b.get("output_config", {}):
                raise urllib.error.HTTPError(url, 400, "bad", {}, None)
            return hk('{"verdict":"PASS","problems":[]}')
        with contextlib.redirect_stdout(io.StringIO()):
            ok, reasons = P.qa_check(art(), T, {}, "page", D.Meter(cap=0.9), post=post)
        self.assertEqual((ok, reasons), (True, []))
        self.assertEqual(len(seen), 2)

    def test_qa_json_forms_strict(self):
        self.assertEqual(P.parse_qa('{"verdict":"PASS","problems":[]}'), ("PASS", []))
        v, r = P.parse_qa('{"verdict":"FAIL","problems":[{"quote":"Starter Mode applies.","why":"not on our page"}]}')
        self.assertEqual((v, r), ("FAIL", ['"Starter Mode applies." | not on our page']))
        self.assertEqual(P.parse_qa('{"verdict":"PASS","problems":[{"quote":"x y z","why":"odd"}]}')[0], "FAIL")      # a PASS that lists a problem is a FAIL
        self.assertEqual(P.parse_qa('{"verdict":"FAIL","problems":[]}'), ("FAIL", ["QA said FAIL without a reason"]))
        self.assertIsNone(P.parse_qa('{"verdict":"MAYBE","problems":[]}')[0])

    def test_facts_keep_numbers_only_when_printed_on_the_page(self):
        page = "The checker reads the last 3 videos of a public account and answers in about 30 seconds without a login."
        got = P.fact_bullets(["It looks at the most recent 3 videos.", "It answers within 45 seconds.", "No login is needed to use it."], page)
        self.assertEqual(got, ["It looks at the most recent 3 videos.", "No login is needed to use it."])

    def test_number_from_the_facts_needs_no_other_source_but_other_numbers_do(self):
        facts = "- The report arrives in about 30 seconds.\n- It needs at least 3 videos."
        ok = art(body=body_for("wordpress", T, extra=" A report takes about 30 seconds to arrive."))
        self.assertEqual(probs(ok, T, facts=facts), [])
        bad = art(body=body_for("wordpress", T, extra=" A report takes about 45 seconds to arrive."))
        self.assertTrue(any("45 seconds" in x for x in probs(bad, T, facts=facts)))
        unit = art(body=body_for("wordpress", T, extra=" Expect 30% more reach."))            # same number, different unit: not from the facts
        self.assertTrue(any("30%" in x for x in probs(unit, T, facts=facts)))

    def test_invented_mode_name_is_flagged_and_repaired(self):
        a = art(body=body_for("wordpress", T, extra=" With fewer, the free Starter Mode applies instead."))
        pr = probs(a, T)
        self.assertTrue(any("Starter Mode" in x for x in pr))
        self.assertTrue(P.repairable(pr))
        bad = P.offending_sentences(a["body"], "", [], pr)
        self.assertEqual(len(bad), 1)
        self.assertIn("drop the name", bad[0][1])
        self.assertFalse(any("Starter Mode" in x for x in probs(a, T, page_full=SITE_TEXT + " Starter Mode is our free mode.")))

    def test_unsourced_number_sentence_is_rewritten_without_the_figure(self):
        a = art(body=body_for("wordpress", T, extra="\n\nCreators see 47.4% growth with tags."))
        pr = probs(a, T)
        bad = P.offending_sentences(a["body"], "", [], pr)
        self.assertEqual(len(bad), 1)
        self.assertIn("drop the figure", bad[0][1])

        def post(url, b, h):
            return hk(json.dumps({"rewrites": [{"n": 1, "text": "Creators often see tags help when they match the video."}]}))
        fixed = P.repair_article(a, "", [], D.Meter(cap=0.9), post=post, problems=pr)
        self.assertEqual(probs(fixed, T), [])

    def test_normalize_keeps_one_target_link_in_a_middle_paragraph_and_unlinks_the_rest(self):
        body = ("## H\n\nFirst [InayaFlow](%s) para %s.\n\nMiddle [the tool](%s) and [a study](https://x.example.org/s) and [home](https://inayaflow.ai/) %s.\n\n"
                "Third [inayaflow.ai/x](%s) %s.\n\nLast %s." % (T, filler("aa", 30), T, filler("bb", 30), T, filler("cc", 30), filler("dd", 30)))
        n = P.normalize_links({"title": "t", "body": body, "sources": []}, T, web=False)["body"]
        self.assertEqual(n.count("](" + T + ")"), 1)
        self.assertIn("[the tool](%s)" % T, n)
        self.assertNotIn("x.example.org", n)
        self.assertNotIn("](https://inayaflow.ai/)", n)
        self.assertIn("Third the same free tool", n)
        self.assertFalse(any("first or last" in x or "exactly one" in x or "bare" in x for x in probs({"title": "t", "body": n, "sources": []}, T)))
        same = {"title": "t", "body": body_for("wordpress", T), "sources": []}
        self.assertIs(P.normalize_links(same, T, web=False), same)

    def test_qa_repair_changes_only_the_quoted_sentence(self):
        A = "Check the area beneath your display name for a notice."
        a = art(body=body_for("wordpress", T, extra="\n\n" + A + " Keep this line as it is."))
        reasons = ['"%s" | our page says below the username' % A]
        seen = []

        def post(url, b, h):
            u = b["messages"][0]["content"]
            seen.append(u)
            nums = re.findall(r"^(\d+)\. (.*)$", u, re.M)
            target = [k for k, l in nums if l.startswith("Check the area")][0]
            other = [k for k, l in nums if l.startswith("Keep this line")][0]
            return hk(json.dumps({"rewrites": [{"n": int(target), "text": "Open your profile and look for a notice."}, {"n": int(other), "text": "CHANGED"}]}))
        fixed = P.qa_repair_article(a, reasons, D.Meter(cap=0.9), post=post)
        self.assertIn("Open your profile and look for a notice.", fixed["body"])
        self.assertIn("Keep this line as it is.", fixed["body"])              # the checker did not quote it: it stays
        self.assertNotIn("CHANGED", fixed["body"])
        self.assertIn("THE CHECKER QUOTED THESE SENTENCES", seen[0])

    def test_a_rewrite_that_drops_the_link_is_not_applied(self):
        body = "## H\n\nOne plain line. See [the tool](%s) for it.\n\nTwo plain line." % T
        spans = P.sentence_spans(body)
        i = [k for k, (a, b) in enumerate(spans) if "the tool" in body[a:b]][0]
        self.assertEqual(P._splice(body, spans, {i: "See it for yourself."}), body)                 # link lost: the sentence stays as it was
        self.assertEqual(P._splice(body, spans, {i: ""}), body)                                      # a sentence with a link is never deleted
        self.assertIn("Look at [the tool](%s) now." % T, P._splice(body, spans, {i: "Look at [the tool](%s) now." % T}))

    def test_a_repair_that_trades_a_problem_for_a_lost_link_is_not_kept(self):
        """The overlap sits in the sentence with our link; the editor rewrites it WITHOUT the link: the rewrite is refused, the draft fails honestly."""
        calls, sent = [], []
        base = smart_ai(calls)

        def post(url, b, h):
            if b["model"] == D.WRITER:
                r = base(url, b, h)
                tgt = re.search(r"TARGET PAGE: (\S+)", b["messages"][0]["content"]).group(1)
                t = r["content"][0]["text"].replace("[InayaFlow's free checker](%s)" % tgt,
                                                    "[InayaFlow's free checker](%s) plain explanations of how the checker works and what it needs" % tgt)
                return dict(r, content=[{"type": "text", "text": t}])
            if "copy editor" in b["system"] and "fact-checker found" not in b["system"]:
                calls.append("repair")
                items = re.findall(r"^(\d+)\. \[[^\]]*\] (.*)$", b["messages"][0]["content"], re.M)
                return hk(json.dumps({"rewrites": [{"n": int(k), "text": "%s we cover it." % filler("lk" + chr(97 + len(calls) % 26), 8)} for k, _ in items]}))
            return base(url, b, h)
        res, _ = quiet(post_ai=post, publishers=fake_publishers(sent), getter=get2)
        self.assertGreater(calls.count("repair"), 0)
        self.assertEqual(sent, [])
        self.assertEqual(set(res.values()), {"FAIL"})

    def test_writer_prompt_lists_every_code_banned_phrase_and_the_banned_claims(self):
        s = P.writer_system("wordpress")
        for b in P.BANNED:
            self.assertIn('"%s"' % b, s)
        self.assertIn("BANNED CLAIMS", s)
        self.assertIn("Starter Mode", s)
        self.assertIn("SOURCES: none", s)

    def test_writer_gets_facts_with_their_source(self):
        seen = []
        base = smart_ai([])

        def spy(url, b, h):
            if b["model"] == D.WRITER:
                seen.append(b["messages"][0]["content"])
            return base(url, b, h)
        quiet(post_ai=spy, publishers=fake_publishers([]), getter=get2)
        self.assertRegex(seen[0], r"FACTS ABOUT OUR PAGE \(source: https://inayaflow\.ai/")

    def test_end_to_end_the_four_25nov_failures_are_fixed_or_failed_honestly(self):
        """Writer drafts carry an 'I', a banned phrase, an unsourced 47.4% and an invented 'Starter Mode': one repair fixes all four, QA (JSON) passes."""
        extra = "\n\nI think tags matter. Take your time with them.\n\nCreators see 47.4% growth with tags.\n\nWith fewer, the free Starter Mode applies instead."
        calls = []
        base = smart_ai(calls, extra=extra)

        def post(url, b, h):
            if b["model"] == D.QA_MODEL and "copy editor" in b["system"] and "fact-checker found" not in b["system"]:
                calls.append("repair")
                items = re.findall(r"^(\d+)\. \[[^\]]*\] (.*)$", b["messages"][0]["content"], re.M)
                return hk(json.dumps({"rewrites": [{"n": int(k), "text": "%s we cover it." % filler("zq" + chr(97 + len(calls) % 26) + chr(97 + int(k) % 26), 9)}
                                                   for k, _ in items]}))
            if b["model"] == D.QA_MODEL and "fact-check editor" in b["system"]:
                calls.append("qa")
                return hk('{"verdict":"PASS","problems":[]}')
            return base(url, b, h)
        sent = []
        res, out = quiet(post_ai=post, publishers=fake_publishers(sent), getter=get2)
        self.assertEqual(set(res.values()), {"PUBLISHED"})
        self.assertEqual(calls.count(D.WRITER), 3)                    # no second writer call needed
        self.assertLessEqual(json.load(open(P.STATE_PATH))["spent"][D.utc_today()], 0.90 + 1e-9)


if __name__ == "__main__":
    unittest.main(verbosity=1)
