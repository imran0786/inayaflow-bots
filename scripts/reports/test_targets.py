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

    def test_all_nine_tools_and_the_homepage_are_target_pages(self):
        self.assertEqual(len(bb.TARGET_PAGES), 17)             # homepage + 9 tools + the 7 quick-win pages (incl. the hashtag guide)
        for t in ("tiktok-hashtag-generator", "tiktok-hashtag-counter", "tiktok-engagement-rate-calculator", "best-time-to-post-on-tiktok-today",
                  "tiktok-coins-to-usd", "tiktok-username-generator"):
            self.assertIn("https://inayaflow.ai/tools/" + t, bb.TARGET_PAGES)
        self.assertEqual(len(tg.PAGES), 17)
        self.assertEqual(bb.best_match(pros("username", 1), {}), "username")          # topic match stays hard
        self.assertEqual(bb.best_match(pros("coins", 1), {}), "coins")

    def test_rotation_picks_the_least_mailed_target_page(self):
        counts = {"https://inayaflow.ai/tools/tiktok-money-calculator": 2}
        rem = [pros("money", 1), pros("shadowban", 2), pros("followers", 3)]
        self.assertEqual(rem[bb.next_by_rotation(rem, counts)]["match"], "shadowban")   # money already had 2 today; shadowban is first of the zeros
        counts["https://inayaflow.ai/tools/tiktok-shadowban-checker"] = 1
        counts["https://inayaflow.ai/blog/tiktok-shadowban"] = 2        # the shadowban topic can also go to our quick-win guide; both are busy now
        self.assertEqual(rem[bb.next_by_rotation(rem, counts)]["match"], "followers")

    def test_target_pages_come_before_other_pages(self):
        rem = [pros("guide", 1), pros("money", 2)]          # "guide" (best-time article) is not one of the target pages
        self.assertEqual(rem[bb.next_by_rotation(rem, {"https://inayaflow.ai/tools/tiktok-money-calculator": 5})]["match"], "money")

    def test_topic_match_stays_hard(self):
        # a shadowban article can only ever get the shadowban page; only hashtag articles may swap between the two hashtag pages
        self.assertEqual(bb.best_match(pros("shadowban", 1), {}), "shadowban")
        self.assertEqual(bb.best_match(pros("money", 1), {"https://inayaflow.ai/tools/tiktok-money-calculator": 9}), "money")
        self.assertIn(bb.best_match(pros("hashtags", 1), {}), ("hashtags", "hashtag-strategy"))
        busy = {bb.MATCH["hashtags"][0]: 1, bb.MATCH["hashtag-counter"][0]: 1}
        self.assertEqual(bb.best_match(pros("hashtags", 1), busy), "hashtag-strategy")        # the least-mailed of the three hashtag pages
        self.assertEqual(bb.best_match(pros("hashtags", 1), {}), "hashtags")
        self.assertEqual(bb.best_match(pros("/tools", 1), {bb.MATCH["/tools"][0]: 0}), "home")   # Director 23 Nov: a /tools round-up may get /tools OR the homepage

    def test_sent_today_counts_by_page(self):
        st = {"outreach": {"a.com": {"status": "queued", "date": "2026-11-25", "match": "money"},
                           "b.com": {"status": "sent", "date": "2026-11-25", "match": "money"},
                           "c.com": {"status": "sent", "date": "2026-11-24", "match": "money"},
                           "d.com": {"status": "skipped", "date": "2026-11-25", "match": "money"}}}
        self.assertEqual(bb.sent_today_by_page(st, "2026-11-25"), {"https://inayaflow.ai/tools/tiktok-money-calculator": 2})

    def test_new_mails_use_only_inayaflow_links_and_company_voice(self):
        for m in ("home", "hashtag-strategy", "followers", "hashtag-counter", "username"):
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
        row = [l for l in md.split("\n") if l.startswith("| Shadowban checker")][0]
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


class QuickWins(unittest.TestCase):
    """24 Nov 2026 quick-win tier: ONE list shared by the report, hunter, DEV poster and outreach rotation; 2x weight."""

    def test_one_shared_list_of_seven(self):
        self.assertEqual(len(tg.QUICK_WIN_PATHS), 7)
        self.assertIn("/blog/tiktok-hashtag-strategy", tg.QUICK_WIN_PATHS)
        paths = {p[1] for p in tg.PAGES}
        for q in tg.QUICK_WIN_PATHS:
            self.assertIn(q, paths)
        self.assertTrue(tg.is_quick_win("https://inayaflow.ai/analyze/"))
        self.assertFalse(tg.is_quick_win("https://inayaflow.ai/tools/tiktok-money-calculator"))
        self.assertFalse(tg.is_quick_win("/"))
        for p in tg.PAGES:
            self.assertEqual(p[3], 4 if p[1] != "/" else 5)                           # min followed per week
        for q in tg.QUICK_WIN_PATHS:
            self.assertIn("https://inayaflow.ai" + q, bb.TARGET_PAGES)                # outreach keeps all of them as target pages

    def test_every_page_without_a_listing_id_works_everywhere(self):
        none_pages = [p for p in tg.PAGES if p[2] is None]
        self.assertEqual(len(none_pages), 7)                                           # the 7 guides / analyzer have no listing id
        ds = {"a": {"name": "A", "status": "SUBMITTED", "date": "2026-11-25", "products": {}}}
        md = tg.build_targets([["x", "LIVE", "followed", "", "2026-11-25", "", p[1]] for p in none_pages], {"outreach": {}}, ds, TODAY)
        for p in none_pages:
            row = [l for l in md.split(chr(10)) if l.startswith("| " + p[0])][0]
            self.assertIn("n/a (a guide, not listed)", row)
            self.assertIn("| 1 / 0 |", row)
        self.assertEqual(tg.directory_today(ds, "2026-11-25"), {"homepage": 1})

    def test_report_shows_quick_wins_first(self):
        md = tg.build_targets([], {}, {}, TODAY)
        i, j = md.index("### Quick wins (page 1-2 now)"), md.index("### Other targets")
        self.assertLess(i, j)
        quick, other = md[i:j], md[j:]
        for label in ("Hashtag strategy guide", "No views guide", "Shadowban guide", "Account analyzer", "How many views is good guide", "Hooks guide", "Captions guide"):
            self.assertIn("| " + label, quick)
        self.assertIn("| Homepage", other)
        self.assertIn("| Money calculator", other)
        self.assertNotIn("| Money calculator", quick)

    def test_outreach_gives_quick_win_pages_double_weight(self):
        guide, tool = "https://inayaflow.ai/blog/tiktok-shadowban", "https://inayaflow.ai/tools/tiktok-shadowban-checker"
        self.assertEqual(bb.page_load(guide, {guide: 2}), 1.0)                         # 2 mails count as 1 on a quick-win page
        self.assertEqual(bb.page_load(tool, {tool: 2}), 2.0)
        rem = [pros("money", 1), pros("no-views", 2)]
        counts = {"https://inayaflow.ai/tools/tiktok-money-calculator": 1, "https://inayaflow.ai/blog/why-is-my-tiktok-getting-no-views": 1}
        self.assertEqual(rem[bb.next_by_rotation(rem, counts)]["match"], "no-views")  # same count, the quick-win page is the less busy one
        self.assertEqual(bb.best_match(pros("shadowban", 1), {tool: 1}), "shadowban-guide")
        self.assertEqual(bb.best_match(pros("shadowban", 1), {}), "shadowban")
        for k in ("no-views", "shadowban-guide", "analyze", "how-many-views", "hooks", "captions"):
            self.assertIn(k, bb.MATCH)
            self.assertIn(k, bb.SUBJECT)

    def test_hunter_and_poster_use_the_same_list(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hunter"))
        import hunter as hu
        self.assertIs(hu.tg, tg)
        self.assertEqual({p[1] for p in hu.PAGES if tg.is_quick_win(p[1])}, {"https://inayaflow.ai" + q for q in tg.QUICK_WIN_PATHS})


if __name__ == "__main__":
    unittest.main()
