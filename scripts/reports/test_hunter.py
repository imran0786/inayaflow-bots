"""Offline tests for the 24/7 prospect hunter and its feeder (23 Nov 2026). No network, no secrets."""
import datetime as dt, json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "hunter"))
sys.path.insert(0, os.path.join(HERE, "..", "job_runner"))
import hunter as hu
import hunter_ingest as hi
import common as c
import runner


def at(h, d=23):
    return dt.datetime(2026, 11, d, h, 17, tzinfo=dt.timezone.utc)


class Hunter(unittest.TestCase):
    def test_every_page_meets_every_type_over_25_slots(self):
        seen = set()
        for n in range(25):
            page, typ = hu.PAGES[n % 5], hu.TYPES[(n // 5) % 5]
            seen.add((page[0], typ[0]))
        self.assertEqual(len(seen), 25)
        self.assertEqual(len({hu.slot(at(h))[2] for h in range(0, 24, 2)}), 12)     # 12 different slots a day

    def test_id_has_hunter_and_the_brief_is_valid_for_the_runner(self):
        now = at(8)
        page, typ, _ = hu.slot(now)
        pid = hu.job_id(now, page, typ)
        self.assertIn("-hunter-", pid)
        job = runner.parse_brief(hu.build_brief(now, page, typ), pid)
        self.assertEqual((job["role"], job["model"], job["max_cost"], job["max_searches"]), ("research", "sonnet", 1.0, 10))
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

    def test_rows_are_added_and_result_is_processed_once(self):
        files = {"jobs/done/2026-11-22-hunter-follower-checker.md": result(GOOD_MAIL, GOOD_DIR)}
        cnt, st, pros, cands = self.run_ingest(files)
        self.assertEqual((cnt["prospects"], cnt["directories"]), (1, 1))
        self.assertEqual(pros[0]["match"], "hashtag-strategy")
        self.assertEqual(pros[0]["pri"], "B")
        self.assertEqual(pros[0]["emails"], ["editor@example.com"])
        self.assertTrue(cands["sites"][0]["usable"])
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
               dict(GOOD_DIR, free="unknown"), dict(GOOD_DIR, rel="unknown"), dict(GOOD_DIR, rel_evidence=""), dict(GOOD_DIR, url="http://insecure.io/x"),
               dict(GOOD_DIR, url="https://inayaflow.ai/tools")]
        cnt, _s, pros, cands = self.run_ingest({"jobs/done/z-hunter-y.md": result(*bad)})
        self.assertEqual((len(pros), len(cands["sites"])), (0, 0))

    def test_only_hunter_files_and_junk_lines_are_ignored(self):
        files = {"jobs/done/2026-11-22-research-other.md": result(GOOD_MAIL), "jobs/done/q-hunter-r.md": "status: DONE\n{not json}\nsome text\n"}
        cnt, _s, pros, _c = self.run_ingest(files)
        self.assertEqual((cnt["results"], len(pros)), (1, 0))


if __name__ == "__main__":
    unittest.main()
