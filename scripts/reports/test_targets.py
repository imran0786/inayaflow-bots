"""Offline tests for the 22 Nov 2026 backlink targets: page rotation, per-page report table (no network, no secrets)."""
import datetime as dt, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("REPORTS_WORK", os.path.join(os.environ.get("TEMP", "/tmp"), "targets_test_work"))
import backlink_batch as bb
import targets as tg

TODAY = dt.date(2026, 11, 25)   # a Wednesday: day 3 of the week, Monday = 2026-11-23


def pros(match, n):
    return {"site": "S%d" % n, "page": "https://s%d.com/p" % n, "match": match, "emails": ["a@s%d.com" % n], "pri": "B"}


class Rotation(unittest.TestCase):
    def test_every_target_page_has_a_match_with_the_live_url(self):
        urls = {m[0] for m in bb.MATCH.values()}
        for u in bb.TARGET_PAGES:
            self.assertIn(u, urls, u)

    def test_rotation_picks_the_least_mailed_target_page(self):
        counts = {"https://inayaflow.ai/tools/tiktok-money-calculator": 2}
        rem = [pros("money", 1), pros("shadowban", 2), pros("followers", 3)]
        self.assertEqual(rem[bb.next_by_rotation(rem, counts)]["match"], "shadowban")   # money already had 2 today; shadowban is first of the zeros
        counts["https://inayaflow.ai/tools/tiktok-shadowban-checker"] = 1
        self.assertEqual(rem[bb.next_by_rotation(rem, counts)]["match"], "followers")

    def test_target_pages_come_before_other_pages(self):
        rem = [pros("engagement", 1), pros("money", 2)]
        self.assertEqual(rem[bb.next_by_rotation(rem, {"https://inayaflow.ai/tools/tiktok-money-calculator": 5})]["match"], "money")

    def test_topic_match_stays_hard(self):
        # a shadowban article can only ever get the shadowban page; only hashtag articles may swap between the two hashtag pages
        self.assertEqual(bb.best_match(pros("shadowban", 1), {}), "shadowban")
        self.assertEqual(bb.best_match(pros("money", 1), {"https://inayaflow.ai/tools/tiktok-money-calculator": 9}), "money")
        self.assertIn(bb.best_match(pros("hashtags", 1), {}), ("hashtags", "hashtag-strategy"))
        self.assertEqual(bb.best_match(pros("hashtags", 1), {bb.MATCH["hashtag-strategy"][0]: 3}), "hashtag-strategy")   # the target page is preferred over the generator page
        self.assertEqual(bb.best_match(pros("/tools", 1), {bb.MATCH["/tools"][0]: 0}), "home")   # homepage is a target page, /tools is not

    def test_sent_today_counts_by_page(self):
        st = {"outreach": {"a.com": {"status": "queued", "date": "2026-11-25", "match": "money"},
                           "b.com": {"status": "sent", "date": "2026-11-25", "match": "money"},
                           "c.com": {"status": "sent", "date": "2026-11-24", "match": "money"},
                           "d.com": {"status": "skipped", "date": "2026-11-25", "match": "money"}}}
        self.assertEqual(bb.sent_today_by_page(st, "2026-11-25"), {"https://inayaflow.ai/tools/tiktok-money-calculator": 2})

    def test_new_mails_use_only_inayaflow_links_and_company_voice(self):
        for m in ("home", "hashtag-strategy", "followers"):
            subj, body = bb.build_email(m, "How we did it", "It covers a topic.")
            self.assertIsNone(bb.guard(subj, body, "It covers a topic."), m)
            self.assertIn(bb.MATCH[m][0], body)


class Table(unittest.TestCase):
    LIVE = [["a.com", "LIVE", "followed", "https://a.com/x", "2026-11-25", "2026-11-25", "/"],
            ["b.com", "LIVE", "nofollow", "https://b.com/x", "2026-11-24", "2026-11-25", "/"],
            ["c.com", "LIVE", "followed", "https://c.com/x", "2026-11-25", "2026-11-25", "/tools/tiktok-money-calculator"],
            ["d.com", "LIVE", "followed", "https://d.com/x", "2026-11-25", "2026-11-25", ""],
            ["e.com", "PENDING", "", "", "", "2026-11-25", ""]]

    def test_counts_per_page(self):
        live, unk_t, unk_w, tracked = tg.live_by_page(self.LIVE, TODAY)
        self.assertTrue(tracked)
        self.assertEqual(live["/"], {"today": [1, 0], "week": [1, 1]})
        self.assertEqual(live["/tools/tiktok-money-calculator"]["today"], [1, 0])
        self.assertEqual((unk_t, unk_w), (1, 1))

    def test_red_when_behind_and_not_tracked_when_no_page_data(self):
        md = tg.build_targets(self.LIVE, {"outreach": {"x.com": {"status": "queued", "date": "2026-11-25", "match": "shadowban"}}}, {}, TODAY)
        self.assertIn("## Backlink targets today", md)
        row = [l for l in md.split("\n") if l.startswith("| Shadowban")][0]
        self.assertIn("RED", row)
        self.assertIn("| 1 |", row)          # one mail queued today for that page
        home = [l for l in md.split("\n") if l.startswith("| Homepage")][0]
        self.assertIn("| 1 / 0 |", home)
        old = [r[:6] for r in self.LIVE]
        md2 = tg.build_targets(old, {}, {}, TODAY)
        self.assertIn("not tracked yet", md2)
        self.assertFalse([l for l in md2.split(chr(10)) if l.startswith("| ") and "RED" in l])

    def test_directory_indicator(self):
        ds = {"a": {"name": "A", "status": "SUBMITTED", "date": "2026-11-25", "products": {"money-calculator": {"status": "SUBMITTED", "date": "2026-11-25"},
                                                                                         "shadowban-checker": {"status": "ONE_PER_DOMAIN", "date": "2026-11-25"}}},
              "a.com": {"name": "A", "status": "SUBMITTED", "date": "2026-11-25", "products": {}}}
        self.assertEqual(tg.directory_today(ds, "2026-11-25"), {"homepage": 1, "money-calculator": 1})


if __name__ == "__main__":
    unittest.main()
