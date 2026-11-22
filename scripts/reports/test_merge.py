import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import merge_candidates as mc


class Merge(unittest.TestCase):
    def test_union_by_domain_nothing_lost(self):
        ours = {"generated": "2026-11-25", "sites": [{"domain": "a.com", "status": "TODO", "checked": "2026-11-20"}, {"domain": "n.com", "status": "TODO"}]}
        theirs = {"generated": "2026-11-24", "sites": [{"domain": "a.com", "status": "TRIED CAPTCHA", "checked": "2026-11-20"}, {"domain": "t.com", "status": "TODO"}]}
        out = mc.merge(ours, theirs)
        by = {s["domain"]: s["status"] for s in out["sites"]}
        self.assertEqual(by, {"a.com": "TRIED CAPTCHA", "n.com": "TODO", "t.com": "TODO"})   # their TRIED result is not overwritten
        self.assertEqual(out["generated"], "2026-11-25")
        ours["sites"][0].update({"status": "TRIED captcha 2", "checked": "2026-11-22"})
        self.assertEqual({s["domain"]: s["status"] for s in mc.merge(ours, theirs)["sites"]}["a.com"], "TRIED captcha 2")


    def test_newer_checked_wins_and_unchecked_never_beats_checked(self):
        old_unchecked = {"domain": "a.com", "status": "UNCHECKED hunter", "checked": "2026-11-26"}
        new_checked = {"domain": "a.com", "status": "TODO", "checked": "2026-11-24", "usable": True}
        for ours, theirs in ((old_unchecked, new_checked), (new_checked, old_unchecked)):
            out = mc.merge({"sites": [ours]}, {"sites": [theirs]})
            self.assertEqual(out["sites"], [new_checked])
        older, newer = {"domain": "b.com", "status": "HOLD", "checked": "2026-11-20"}, {"domain": "b.com", "status": "TODO", "checked": "2026-11-25"}
        self.assertEqual(mc.merge({"sites": [older]}, {"sites": [newer]})["sites"], [newer])
        self.assertEqual(mc.merge({"sites": [newer]}, {"sites": [older]})["sites"], [newer])


if __name__ == "__main__":
    unittest.main()
