"""Offline tests for the 24/7 prospect hunter and its feeder (23 Nov 2026). No network, no secrets."""
import datetime as dt, json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "hunter"))
sys.path.insert(0, os.path.join(HERE, "..", "job_runner"))
import hunter as hu
import hunter_ingest as hi
sys.path.insert(0, HERE)
import common as c
import runner
import backlink_batch as bb


def at(h, d=23):
    return dt.datetime(2026, 11, d, h, 17, tzinfo=dt.timezone.utc)


class HunterBudget(unittest.TestCase):
    def test_workflow_env_matches_the_code(self):
        w = open(os.path.join(HERE, "..", "..", ".github", "workflows", "hunter.yml"), encoding="utf8").read()
        self.assertIn('HUNTER_DAILY_CAP: "3.0"', w)


class Hunter(unittest.TestCase):
    def test_every_page_meets_every_type_over_55_slots(self):
        self.assertEqual(len(hu.PAGES), 17)                                        # homepage, hashtag guide, 9 tools, 6 more quick-win pages
        self.assertEqual(len(hu.ROTATION), 24)                                     # the 7 quick-win pages are listed twice
        seen = set()
        for n in range(24 * 8):
            page, typ = hu.ROTATION[n % 24], hu.TYPE_BY_KEY[hu.TYPE_SEQ[(n + n // 24) % 8]]
            seen.add((page[0], typ[0]))
        self.assertEqual(len(seen), 17 * 4)                                        # every page meets every type in the sequence over 192 slots (4 types: journalist has no consumer)
        counts = {}
        for p in hu.ROTATION:
            counts[p[0]] = counts.get(p[0], 0) + 1
        quick = {p[0] for p in hu.PAGES if hu.tg.is_quick_win(p[1])}
        self.assertEqual(len(quick), 7)
        for slug in counts:
            self.assertEqual(counts[slug], 2 if slug in quick else 1)               # quick wins get TWICE the slots; tools and homepage keep theirs
        for slug in ("homepage", "follower-checker", "money-calculator", "hashtag-generator"):
            self.assertEqual(counts[slug], 1)
        self.assertEqual(hu.slot(at(9), 0)[0], hu.ROTATION[hu.slot(at(9), 0)[2] % 24])
        self.assertEqual(len({hu.slot(at(h))[2] for h in range(0, 24, 3)}), 8)      # 8 different slots a day (every 3 hours)
        self.assertEqual(hu.MAX_COST, 0.60)
        self.assertEqual(hu.JOBS_PER_RUN, 2)
        # 2 jobs per run: 16 different slots a day, consecutive numbers, 16 different page/type pairs
        pairs = {(hu.slot(at(h), k)[0][0], hu.slot(at(h), k)[1][0]) for h in range(0, 24, 3) for k in range(hu.JOBS_PER_RUN)}
        self.assertEqual(len(pairs), 16)
        self.assertEqual(hu.slot(at(9), 1)[2], hu.slot(at(9), 0)[2] + 1)
        self.assertEqual(hu.slot(at(12), 0)[2], hu.slot(at(9), 1)[2] + 1)

    def test_daily_stop_keeps_worst_case_at_3_dollars(self):
        import tempfile
        self.assertEqual(hu.DAILY_CAP, 3.0)
        now = at(21)
        with tempfile.TemporaryDirectory() as w:
            os.makedirs(os.path.join(w, "jobs", "done"))
            for i in range(5):                                    # 5 jobs today = $3.00 of caps (a 6th would pass $3.00)
                open(os.path.join(w, "jobs", "done", "2026-11-23-%02d00-hunter-p%d-x.md" % (i, i)), "w").close()
            open(os.path.join(w, "jobs", "done", "2026-11-22-0900-hunter-old-x.md"), "w").close()    # yesterday does not count
            self.assertEqual(hu.queued_today(w, now), 5)
        # at most floor(3.00 / 0.60) = 5 jobs a day can ever be queued; the runner enforces the $0.60 per job (max_cost in the brief)
        self.assertEqual(int((hu.DAILY_CAP + 1e-9) // hu.MAX_COST), 5)
        # a hunter job still pending from yesterday counts against today; a done one from yesterday does not
        with tempfile.TemporaryDirectory() as w:
            for d in ("pending", "done"):
                os.makedirs(os.path.join(w, "jobs", d))
            open(os.path.join(w, "jobs", "pending", "2026-11-22-2100-hunter-old-x.md"), "w").close()
            open(os.path.join(w, "jobs", "done", "2026-11-22-0900-hunter-old2-x.md"), "w").close()
            open(os.path.join(w, "jobs", "pending", "2026-11-23-0900-other-job.md"), "w").close()    # not a hunter job
            self.assertEqual(hu.queued_today(w, now), 1)

    def run_main(self, files, hour=21, max_waiting=99):
        import tempfile
        w = tempfile.mkdtemp()
        for d, n in files:
            os.makedirs(os.path.join(w, "jobs", d), exist_ok=True)
            open(os.path.join(w, "jobs", d, n), "w").close()
        saved = (c.clone, c.commit_push, c.now, c.DRY, hu.MAX_WAITING)
        c.clone, c.commit_push, c.now, c.DRY, hu.MAX_WAITING = (lambda *a, **k: w), (lambda *a, **k: True), (lambda: at(hour)), False, max_waiting
        try:
            hu.main()
        finally:
            c.clone, c.commit_push, c.now, c.DRY, hu.MAX_WAITING = saved
        return len(os.listdir(os.path.join(w, "jobs", "pending")))

    def test_main_11_done_plus_2_picks_queues_one_more_only_if_it_fits(self):
        done = [("done", "2026-11-23-%02d00-hunter-p%d-x.md" % (i, i)) for i in range(3)]
        self.assertEqual(self.run_main(done), 2)                  # 3 + 2 = 5 jobs = $3.00: both fit (the QA double-count case)
        done = [("done", "2026-11-23-%02d00-hunter-p%d-x.md" % (i, i)) for i in range(4)]
        self.assertEqual(self.run_main(done), 1)                  # 4 + 1 = 5 fits, the 6th is refused
        done = [("done", "2026-11-23-%02d00-hunter-p%d-x.md" % (i, i)) for i in range(5)]
        self.assertEqual(self.run_main(done), 0)                  # 5 already: nothing

    def test_main_old_pending_jobs_count_against_today(self):
        old = [("pending", "2026-11-22-%02d00-hunter-p%d-x.md" % (i, i)) for i in range(2)]
        done = [("done", "2026-11-23-%02d00-hunter-q%d-x.md" % (i, i)) for i in range(2)]
        self.assertEqual(self.run_main(old + done), 2 + 1)        # 2 carried over + 2 done = 4: one more fits (5), the second does not

    def test_job_queued_yesterday_that_ran_today_counts_today(self):
        import tempfile
        w = tempfile.mkdtemp()
        for d in ("done", "failed"):
            os.makedirs(os.path.join(w, "jobs", d))
        def result(d, name, finished):
            with open(os.path.join(w, "jobs", d, name), "w") as f:
                f.write("# Result\n\n- Status: DONE\n- Finished: %s 00:41 UTC. A cloud helper made this\n" % finished if finished else "# Failed\n")
        result("done", "2026-11-22-2332-hunter-a-x.md", "2026-11-23")        # queued yesterday, RAN today: counts
        result("done", "2026-11-23-0100-hunter-b-x.md", "2026-11-23")        # queued today, ran today: counts
        result("done", "2026-11-22-1000-hunter-c-x.md", "2026-11-22")        # ran yesterday: does not
        result("failed", "2026-11-22-2300-hunter-d-x.md", "")                # failed, no Finished line: file name day = yesterday: does not
        result("failed", "2026-11-23-0200-hunter-e-x.md", "")                # failed, no Finished line: file name day = today: counts
        self.assertEqual(hu.queued_today(w, at(9)), 3)

    def test_already_queued_survives_a_later_minute(self):
        import tempfile
        now = at(9)
        page, typ, _ = hu.slot(now)
        with tempfile.TemporaryDirectory() as w:
            os.makedirs(os.path.join(w, "jobs", "pending"))
            self.assertFalse(hu.already_queued(w, now, page, typ))
            open(os.path.join(w, "jobs", "pending", hu.job_id(now, page, typ) + ".md"), "w").close()
            later = now.replace(minute=52)                                      # a keep-alive start at another minute
            self.assertTrue(hu.already_queued(w, later, page, typ))
            self.assertFalse(hu.already_queued(w, at(9, d=24), page, typ))      # another day is free

    def test_id_has_hunter_and_the_brief_is_valid_for_the_runner(self):
        now = at(8)
        page, typ, _ = hu.slot(now)
        pid = hu.job_id(now, page, typ)
        self.assertIn("-hunter-", pid)
        job = runner.parse_brief(hu.build_brief(now, page, typ), pid)
        self.assertEqual((job["role"], job["model"], job["max_cost"], job["max_searches"]), ("research", "sonnet", 0.6, 10))
        self.assertIn("web", job["allowed"])
        self.assertEqual(job["output"], pid)
        self.assertEqual(job["refused_actions"], [])
        self.assertIn("JSON lines", job["body"])
        self.assertIn(page[1], job["body"])
        self.assertIn("never invent an e-mail", job["body"].lower())

    def test_page_values_match_the_feeder(self):
        for p in hu.PAGES:
            self.assertIn(p[0], hi.PAGE_MATCH)


GOOD_MAIL = {"url": "https://blog.example.com/tiktok-hashtags", "type": "blog_article", "page": "hashtag-strategy", "name": "Blog", "topic": "tiktok hashtags",
             "email": "editor@example.com", "email_source_url": "https://example.com/contact", "rel": "unknown", "rel_evidence": "", "free": "unknown"}
GOOD_DIR = {"url": "https://newdir.io/submit", "type": "directory", "page": "homepage", "name": "NewDir", "email": "", "email_source_url": "",
            "rel": "followed", "rel_evidence": "listing page link has no rel", "free": "yes"}


def result(*objs):
    return "status: DONE\n\nHUNT x DONE\n" + "\n".join(json.dumps(o) for o in objs) + "\nCOUNT %d\n" % len(objs)


class Ingest(unittest.TestCase):
    def run_ingest(self, files, state=None, prospects=None, cands=None, exclude=(), in_outbox=lambda *a: False):
        state = state if state is not None else {}
        prospects = prospects if prospects is not None else []
        cands = cands if cands is not None else {"sites": []}
        cnt = hi.ingest(files, state, prospects, cands, set(exclude), {}, in_outbox, "2026-11-23")
        return cnt, state, prospects, cands

    def test_directory_dedupe_also_covers_the_deepseek_file(self):
        files = {"jobs/done/2026-11-22-hunter-follower-checker.md": result(GOOD_DIR)}
        other = {"sites": [{"domain": "newdir.io", "submit_url": "https://newdir.io/", "found_by": "deepseek"}]}
        state, pros, cands = {}, [], {"sites": []}
        cnt = hi.ingest(files, state, pros, cands, set(), {}, lambda *a: False, "2026-11-23", other_cands=other)
        self.assertEqual((cnt["directories"], cnt["dropped"]), (0, 1))                       # already held by DeepSeek's list: not added again
        self.assertEqual(cands["sites"], [])                                                 # and nothing is written into the other file's rows

    def test_rows_are_added_and_result_is_processed_once(self):
        files = {"jobs/done/2026-11-22-hunter-follower-checker.md": result(GOOD_MAIL, GOOD_DIR)}
        cnt, st, pros, cands = self.run_ingest(files)
        self.assertEqual((cnt["prospects"], cnt["directories"]), (1, 1))
        self.assertEqual(pros[0]["match"], "hashtag-strategy")
        self.assertEqual(pros[0]["pri"], "B")
        self.assertEqual(pros[0]["emails"], ["editor@example.com"])
        self.assertFalse(cands["sites"][0]["usable"])                              # unchecked: the batch's live check decides
        self.assertEqual(cands["sites"][0]["status"], "UNCHECKED hunter")
        self.assertIsNone(cands["sites"][0]["http"])
        self.assertEqual(cands["sites"][0]["domain"], "newdir.io")
        cnt2, _s, pros2, cands2 = self.run_ingest(files, st, pros, cands)       # same file again: nothing
        self.assertEqual((cnt2["results"], len(pros2), len(cands2["sites"])), (0, 1, 1))

    def test_dedupe_against_everything_known(self):
        files = {"jobs/done/a-hunter-x.md": result(GOOD_MAIL, GOOD_DIR)}
        cnt, *_ = self.run_ingest(files, prospects=[{"n": 1, "site": "example.com", "page": "https://example.com/p"}],
                                  cands={"sites": [{"domain": "newdir.io", "submit_url": "https://newdir.io/"}]})
        self.assertEqual((cnt["prospects"], cnt["directories"], cnt["dropped"]), (0, 0, 2))
        cnt, *_ = self.run_ingest(files, exclude=["example.com", "newdir.io"])
        self.assertEqual((cnt["prospects"], cnt["directories"]), (0, 0))
        cnt, *_ = self.run_ingest(files, in_outbox=lambda k, e, idx: True)
        self.assertEqual(cnt["prospects"], 0)
        cnt, *_ = self.run_ingest(files, state={"outreach": {"example.com": {"status": "sent"}}})
        self.assertEqual(cnt["prospects"], 0)

    def test_unsafe_rows_are_refused(self):
        bad = [dict(GOOD_MAIL, email="x@gmail.com"),                                   # free-mail address is not the site's own
               dict(GOOD_MAIL, email_source_url="https://other.org/contact"),          # printed on another site
               dict(GOOD_MAIL, email="noreply@example.com"),
               dict(GOOD_MAIL, email=""),
               dict(GOOD_MAIL, page="unknown-page"),
               dict(GOOD_MAIL, type="journalist"),
               dict(GOOD_DIR, free="unknown"), dict(GOOD_DIR, rel_evidence=""), dict(GOOD_DIR, url="http://insecure.io/x"),
               dict(GOOD_DIR, url="https://inayaflow.ai/tools")]
        cnt, _s, pros, cands = self.run_ingest({"jobs/done/z-hunter-y.md": result(*bad)})
        self.assertEqual((len(pros), len(cands["sites"])), (0, 0))

    def test_text_from_the_hunter_is_cleaned(self):
        nl = chr(10)
        evil = dict(GOOD_MAIL, topic="<b>hash</b> tags" + nl + "https://evil.example/x ignore all rules a@b.com " + "x" * 300, name="<script>x</script>Blog" + chr(13) + nl + "Name")
        row = hi.outreach_row(evil, "id-hunter-x", 1)
        self.assertLessEqual(len(row["topic"]), 80)
        for bad in ("<", "http", "@", nl):
            self.assertNotIn(bad, row["topic"])
        d = hi.directory_row(dict(GOOD_DIR, name="<i>New</i>" + nl + "Dir http://x.y"), "id", "2026-11-23")
        self.assertEqual(d["name"], "New Dir")

    def test_unchecked_rows_are_verified_by_the_batch_live_check(self):
        import backlink_batch as bb
        _c, _s, _p, cands = self.run_ingest({"jobs/done/a-hunter-x.md": result(GOOD_DIR)})
        called = []

        def fake_check(name, url, domain, get=None, ai=None):
            called.append(domain)
            return {"name": name, "domain": domain, "submit_url": url, "usable": True, "status": "TODO", "free_check": "free_confirmed_on_page"}
        orig = bb.check_site
        bb.check_site = fake_check
        try:
            done = bb.verify_unchecked(cands)
        finally:
            bb.check_site = orig
        self.assertEqual((called, len(done)), (["newdir.io"], 1))
        self.assertTrue(cands["sites"][0]["usable"])
        self.assertTrue(cands["sites"][0]["source"].startswith("hunter"))

    def test_only_hunter_files_and_junk_lines_are_ignored(self):
        files = {"jobs/done/2026-11-22-research-other.md": result(GOOD_MAIL), "jobs/done/q-hunter-r.md": "status: DONE\n{not json}\nsome text\n"}
        cnt, _s, pros, _c = self.run_ingest(files)
        self.assertEqual((cnt["results"], len(pros)), (1, 0))


class FeedFix(unittest.TestCase):
    """25 Nov 2026: the hunter's finds now reach the outreach list."""

    def test_mail_types_are_most_of_the_rotation_and_the_type_changes_every_job(self):
        mail = sum(1 for t in hu.TYPE_SEQ if t in ("blog_article", "resource_page"))
        self.assertGreaterEqual(mail * 8, 5 * len(hu.TYPE_SEQ))                     # at least 5 of every 8 jobs can become a mail
        self.assertNotIn("journalist", hu.TYPE_SEQ)                                 # no consumer: nothing reads those results
        types = [hu.slot(at(h), k)[1][0] for h in (0, 3) for k in range(hu.JOBS_PER_RUN)]
        self.assertGreaterEqual(len(set(types)), 2)                                 # the first 4 jobs of a day are not all the same type
        day = [hu.slot(at(0, d), 0)[1][0] for d in range(20, 30)]
        self.assertTrue({"blog_article", "resource_page"} <= set(day))              # over 10 days both mail types come up in the budgeted first slot

    def test_mail_brief_asks_for_listicles_authors_and_the_relaxed_match(self):
        now = at(8)
        page = hu.PAGES[2]
        b = hu.build_brief(now, page, hu.TYPE_BY_KEY["blog_article"])
        self.assertIn("BEST TARGETS", b)
        self.assertIn('"author"', b)
        self.assertIn("RELAXED MATCH", b)
        for p in hu.PAGES:
            self.assertIn(p[0] + " = ", b)                                          # the menu of our pages is in the brief
        d = hu.build_brief(now, page, hu.TYPE_BY_KEY["directory"])
        self.assertNotIn("RELAXED MATCH", d)                                         # directories keep their own wording
        self.assertNotIn("BEST TARGETS", d)

    def test_unknown_rel_directory_rows_now_go_in_unchecked(self):
        row = hi.directory_row(dict(GOOD_DIR, rel="unknown", rel_evidence=""), "j1", "2026-11-25")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "UNCHECKED hunter")
        self.assertFalse(row["usable"])                                              # the live check decides, never the hunter's word
        self.assertIsNone(hi.directory_row(dict(GOOD_DIR, rel="followed", rel_evidence=""), "j1", "2026-11-25"))

    def test_author_is_stored_and_a_cost_cap_result_in_failed_is_read(self):
        res = result(dict(GOOD_MAIL, author="Jane Writer"))
        state, pros = {}, []
        cnt = hi.ingest({"jobs/failed/2026-11-24-hunter-x-blog-article.md": res}, state, pros, {"sites": []}, set(), {}, lambda *a: False, "2026-11-25")
        self.assertEqual(cnt["prospects"], 1)
        self.assertEqual(pros[0]["author"], "Jane Writer")
        self.assertEqual(pros[0]["pri"], "B")

    def test_batch_reads_done_and_failed_results(self):
        t = open(os.path.join(HERE, "backlink_batch.py"), encoding="utf8").read()
        self.assertIn('["jobs/done", "jobs/failed"], name_has="-hunter-"', t)


class RelaxedTopic(unittest.TestCase):
    PAGE = "<title>Best TikTok tools</title>" + "Words about tools and shadowban checks. " * 40

    def prospect(self, match="hashtags"):
        return {"site": "a.com", "page": "https://a.com/p", "match": match, "emails": ["ed@a.com"]}

    def get(self, url, timeout=15, limit=0):
        if url == "https://a.com/p":
            return 200, url, self.PAGE
        if url.endswith("/contact"):
            return 200, url, "Write to ed@a.com"
        return 404, url, ""

    def ai(self, topic_match, best):
        return lambda s, u, m=0: json.dumps({"topic_match": topic_match, "best_page": best, "sponsored_or_paid": False, "title": "Best TikTok tools", "covers": "It covers free tools."})

    def test_article_about_another_target_page_is_pitched_that_page(self):
        job, why = bb.prepare_first(self.prospect(), self.get, self.ai(False, "shadowban"))
        self.assertIsNone(why)
        self.assertEqual(job["match"], "shadowban")                                  # honest re-pointing, and the mail names the shadowban self-check
        self.assertIn("shadowban", job["body"].lower())

    def test_no_alt_or_a_page_outside_our_17_stays_a_skip(self):
        self.assertEqual(bb.prepare_first(self.prospect(), self.get, self.ai(False, ""))[1], "topic is not an exact match")
        self.assertEqual(bb.prepare_first(self.prospect(), self.get, self.ai(False, "scripts"))[1], "topic is not an exact match")      # "scripts" is not one of the 17
        self.assertEqual(bb.prepare_first(self.prospect(), self.get, self.ai(False, "made-up"))[1], "topic is not an exact match")

    def test_old_topic_skips_are_looked_at_once_more_but_other_skips_are_not(self):
        ps = [{"n": 1, "site": "A", "page": "https://a.com/p", "match": "money", "emails": ["x@a.com"], "pri": "B"},
              {"n": 2, "site": "B", "page": "https://b.com/p", "match": "money", "emails": ["x@b.com"], "pri": "B"},
              {"n": 3, "site": "C", "page": "https://c.com/p", "match": "money", "emails": ["x@c.com"], "pri": "B"},
              {"n": 4, "site": "D", "page": "https://d.com/p", "match": "money", "emails": ["x@d.com"], "pri": "B"}]
        st = {"outreach": {"a.com": {"status": "skipped", "why": "topic is not an exact match", "date": "2026-11-20"},
                           "b.com": {"status": "skipped", "why": "topic is not an exact match", "date": "2026-11-25"},      # already judged with the new check
                           "c.com": {"status": "skipped", "why": "contact address not found on the live site", "date": "2026-11-20"}}}
        ok, _h = bb.eligible_prospects(ps, st, set())
        self.assertEqual([p["site"] for p in ok], ["A", "D"])


class EndToEnd(unittest.TestCase):
    def test_a_hunter_result_becomes_a_queued_mail(self):
        """hunter result file -> hunter_ingest -> prospect row -> run_outreach (page + contact check + judge + guard) -> a mail job for the outbox robot."""
        import tempfile
        res = result(dict(GOOD_MAIL, url="https://blog.example.com/best-tiktok-tools", page="shadowban-checker", author="Jane Writer", email="editor@example.com",
                          email_source_url="https://blog.example.com/contact"))
        state, pros = {}, []
        cnt = hi.ingest({"jobs/done/2026-11-25-0301-hunter-shadowban-checker-blog-article.md": res}, state, pros, {"sites": []}, set(), {}, lambda *a: False, "2026-11-25")
        self.assertEqual(cnt["prospects"], 1)
        work = tempfile.mkdtemp()
        os.makedirs(os.path.join(work, "ops"))
        json.dump(pros, open(os.path.join(work, "ops", "outreach_prospects.json"), "w"))
        page = "<title>Best TikTok tools</title>" + "A list of tools and a shadowban check. " * 40

        def get(url, timeout=15, limit=0):
            if url == "https://blog.example.com/best-tiktok-tools":
                return 200, url, page
            if url.endswith("/contact"):
                return 200, url, "Email editor@example.com"
            return 404, url, ""
        ai = lambda s_, u, m=0: json.dumps({"topic_match": True, "best_page": "shadowban", "sponsored_or_paid": False, "title": "Best TikTok tools", "covers": "It covers free tools."})
        saved = (c.fetch_file, c.read_tree)
        c.fetch_file = lambda branch, path: ""
        c.read_tree = lambda *a, **k: {}
        try:
            queued, skipped, human = bb.run_outreach(work, {}, True, get, ai)
        finally:
            c.fetch_file, c.read_tree = saved
        self.assertEqual((len(queued), len(skipped)), (1, 0), skipped)
        self.assertEqual(queued[0]["to"], ["editor@example.com"])
        self.assertIn("We ", queued[0]["body"])
        self.assertNotIn(" I ", queued[0]["body"])


if __name__ == "__main__":
    unittest.main()
