"""commit_push on a real (local) git remote: a push race with a rebase conflict is merged, nothing is lost or blindly overwritten (23 Nov 2026)."""
import json, os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("FIXTURE_DIR", None)
import common as c


def sh(*a, cwd=None):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout


def clone(remote, name, base):
    d = os.path.join(base, name)
    sh("clone", "-q", remote, d)
    sh("config", "user.name", "t", cwd=d)
    sh("config", "user.email", "t@example.com", cwd=d)
    return d


class PushMerge(unittest.TestCase):
    def test_conflicting_json_state_is_merged_not_overwritten(self):
        base = tempfile.mkdtemp()
        remote = os.path.join(base, "remote.git")
        sh("init", "-q", "--bare", "-b", "ops", remote)
        seed = clone(remote, "seed", base)
        os.makedirs(os.path.join(seed, "ops"))
        json.dump({"days": {"d": 1}, "ai_calls": {"d": 10}, "outreach": {"x.com": {"status": "queued", "date": "d"}}, "last_run": "1"}, open(os.path.join(seed, "ops", "state.json"), "w"))
        json.dump({"sites": [{"domain": "a.com", "status": "TODO", "checked": "2026-11-20"}]}, open(os.path.join(seed, "ops", "cands.json"), "w"))
        sh("add", "-A", cwd=seed)
        sh("commit", "-q", "-m", "seed", cwd=seed)
        sh("push", "-q", "origin", "HEAD:ops", cwd=seed)
        a, b = clone(remote, "a", base), clone(remote, "b", base)
        sh("checkout", "-q", "ops", cwd=a)
        sh("checkout", "-q", "ops", cwd=b)
        # run A saves first
        json.dump({"days": {"d": 2}, "ai_calls": {"d": 40}, "outreach": {"x.com": {"status": "sent", "date": "d"}, "a-only.com": {"status": "queued", "date": "d"}}, "last_run": "2"}, open(os.path.join(a, "ops", "state.json"), "w"))
        json.dump({"sites": [{"domain": "a.com", "status": "TODO", "checked": "2026-11-24", "usable": True}]}, open(os.path.join(a, "ops", "cands.json"), "w"))
        self.assertTrue(c.commit_push("A", "ops", a))
        # run B, started from the old state, now conflicts
        json.dump({"days": {"d": 1, "e": 1}, "ai_calls": {"d": 15}, "outreach": {"x.com": {"status": "queued", "date": "d"}, "b-only.com": {"status": "queued", "date": "d"}}, "last_run": "1"}, open(os.path.join(b, "ops", "state.json"), "w"))
        json.dump({"sites": [{"domain": "a.com", "status": "UNCHECKED hunter", "checked": "2026-11-26"}, {"domain": "b.com", "status": "TODO"}]}, open(os.path.join(b, "ops", "cands.json"), "w"))
        self.assertTrue(c.commit_push("B", "ops", b))
        check = clone(remote, "check", base)
        sh("checkout", "-q", "ops", cwd=check)
        st = json.load(open(os.path.join(check, "ops", "state.json")))
        self.assertEqual(st["outreach"]["x.com"]["status"], "sent")                       # the further-along entry survives
        self.assertIn("a-only.com", st["outreach"])
        self.assertIn("b-only.com", st["outreach"])                                       # nothing lost from either side
        self.assertEqual(st["ai_calls"]["d"], 40)                                         # counters never go down
        self.assertEqual(st["days"], {"d": 2, "e": 1})
        self.assertEqual(st["last_run"], "2")
        cands = {s["domain"]: s for s in json.load(open(os.path.join(check, "ops", "cands.json")))["sites"]}
        self.assertEqual(cands["a.com"]["status"], "TODO")                                # an UNCHECKED row never beats a checked one
        self.assertIn("b.com", cands)

    def test_merge_state_rules(self):
        m = c.merge_state({"n": 3, "l": [1, 2], "e": {"status": "skipped", "date": "2"}}, {"n": 5, "l": [2, 3], "e": {"status": "queued", "date": "1"}})
        self.assertEqual((m["n"], m["l"], m["e"]["status"]), (5, [2, 3, 1], "queued"))


if __name__ == "__main__":
    unittest.main()
