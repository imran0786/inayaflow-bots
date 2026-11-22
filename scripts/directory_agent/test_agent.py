"""Offline tests for the per-tool listings of the directory agent (22 Nov 2026). No browser, no network."""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent as ag


class Listings(unittest.TestCase):
    def test_caps_raised(self):
        self.assertEqual(ag.SUCCESS_CAP, 30)
        self.assertEqual(ag.ATTEMPT_CAP, 150)

    def test_listings_file(self):
        prods = ag.load_products()
        ids = [p["id"] for p in prods]
        self.assertEqual(ids[0], "homepage")
        for need in ("follower-count-checker", "money-calculator", "shadowban-checker", "hashtag-generator", "hashtag-counter", "coins-to-usd"):
            self.assertIn(need, ids)
        self.assertEqual(len(ids), len(set(ids)))
        for p in prods:
            self.assertLessEqual(len(p["short_description"]), 50, p["id"])
            self.assertLessEqual(len(p["long_description"]), 150, p["id"])
            self.assertTrue(p["url"].startswith("https://inayaflow.ai"), p["id"])
        urls = [p["url"] for p in prods]
        self.assertEqual(len(urls), len(set(urls)))

    def test_task_for_tool_names_that_tool_and_allows_one_per_domain(self):
        site = {"name": "Dir", "url": "https://dir.example/submit"}
        tool = [p for p in ag.load_products() if p["id"] == "money-calculator"][0]
        t = ag.build_task(site, tool)
        self.assertIn("tiktok-money-calculator", t)
        self.assertIn("ONE_PER_DOMAIN", t)
        self.assertIn("ADDITIONAL listing", t)
        home = ag.build_task(site)
        self.assertNotIn("ADDITIONAL listing", home)
        self.assertEqual(ag.facts_for(None), ag.FACTS)

    def test_product_todo_and_one_per_domain_skip(self):
        prods = ag.load_products()
        site = {"name": "Dir", "url": "https://dir.example/submit"}
        st = {}
        ag.record(st, site, {"status": "SUBMITTED"}, "2026-11-25")
        todo = ag.product_todo(st, site, prods, "2026-11-25")
        self.assertEqual(len(todo), len(prods) - 1)                       # homepage is the site listing itself
        ag.record_product(st, site, todo[0], {"status": "SUBMITTED", "note": "ok"}, "2026-11-25")
        ag.record_product(st, site, todo[1], {"status": "ONE_PER_DOMAIN", "note": "x"}, "2026-11-25")
        ag.skip_remaining_products(st, site, todo[2:], "2026-11-25", "one listing per company here")
        self.assertEqual(ag.product_todo(st, site, prods, "2026-11-26"), [])   # every tool has a final result: never tried again
        self.assertEqual(st["dir.example"]["products"][todo[0]["id"]]["status"], "SUBMITTED")
        ag.record(st, site, {"status": "SUBMITTED"}, "2026-11-26")        # re-recording the site keeps the product results
        self.assertIn(todo[0]["id"], st["dir.example"]["products"])

    def test_failed_tool_is_retried_once_on_a_later_day(self):
        prods = ag.load_products()
        site = {"name": "Dir", "url": "https://dir.example/submit"}
        st = {}
        ag.record(st, site, {"status": "SUBMITTED"}, "2026-11-25")
        t = ag.product_todo(st, site, prods, "2026-11-25")[0]
        ag.record_product(st, site, t, {"status": "TIMEOUT", "note": ""}, "2026-11-25")
        self.assertNotIn(t["id"], [p["id"] for p in ag.product_todo(st, site, prods, "2026-11-25")])   # not twice the same day
        self.assertIn(t["id"], [p["id"] for p in ag.product_todo(st, site, prods, "2026-11-26")])
        ag.record_product(st, site, t, {"status": "TIMEOUT", "note": ""}, "2026-11-26")
        self.assertNotIn(t["id"], [p["id"] for p in ag.product_todo(st, site, prods, "2026-11-27")])   # second failure: stop

    def test_one_per_domain_is_a_valid_result_code(self):
        self.assertIn("ONE_PER_DOMAIN", ag.RESULT_CODES)


if __name__ == "__main__":
    unittest.main()
