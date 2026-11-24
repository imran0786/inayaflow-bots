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
        run7 = "one two three four five six seven eight"
        a = art(body=body_for("wordpress", T, extra=" " + run7))
        self.assertTrue(any("our site" in x for x in probs(a, T, site_corpus="x " + run7 + " y")))
        self.assertTrue(any("today's blogger" in x for x in probs(a, T, others=[("blogger", "q " + run7 + " q")])))
        self.assertTrue(any("earlier" in x for x in probs(a, T, past_shingles=D.shingles(a["body"]))))

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
        for d in range(1, 8):
            p = P.pick_targets(st, "2026-12-%02d" % d)
            for k, v in p.items():
                seen[k].add(v[0])
                st["posts"].append({"platform": k, "slug": v[0], "date": "2026-12-%02d" % d})
        allseen = set().union(*seen.values())
        self.assertTrue({t[0] for t in P.TARGETS if t[2] == "tool"} <= allseen)
        for k in seen:
            self.assertEqual(len(seen[k]), 7)

    def test_targets_have_9_tools_guides_home(self):
        kinds = [t[2] for t in P.TARGETS]
        self.assertEqual(kinds.count("tool"), 9)
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
