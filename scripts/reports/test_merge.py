import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_candidates as mc


class Merge(unittest.TestCase):
    def test_union_by_domain_nothing_lost(self):
        ours = {"generated": "2026-11-25", "sites": [{"domain": "a.com", "status": "TODO"}, {"domain": "n.com", "status": "TODO"}]}
        theirs = {"generated": "2026-11-24", "sites": [{"domain": "a.com", "status": "TRIED CAPTCHA"}, {"domain": "t.com", "status": "TODO"}]}
        out = mc.merge(ours, theirs)
        by = {s["domain"]: s["status"] for s in out["sites"]}
        self.assertEqual(by, {"a.com": "TRIED CAPTCHA", "n.com": "TODO", "t.com": "TODO"})   # their TRIED result is not overwritten
        self.assertEqual(out["generated"], "2026-11-25")
        ours["sites"][0]["status"] = "TRIED captcha 2"
        self.assertEqual({s["domain"]: s["status"] for s in mc.merge(ours, theirs)["sites"]}["a.com"], "TRIED captcha 2")


if __name__ == "__main__":
    unittest.main()
