"""Tests for the code QA findings of 19 Nov 2026 (cost meter, cap overshoot, BAD_BRIEF file names)."""
import json, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="jobs_qa_test_")
os.environ["JOBS_WORK"] = os.path.join(TMP, "work")
os.environ["GENIE_MAIN_WORK"] = os.path.join(TMP, "genie")
os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ.pop("JOB_DEEPSEEK_FALLBACK", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner as r

BRIEF = """---
id: 2026-11-19-qa-1
role: research
max_cost: 0.20
---
## Job
Find the thing and report it with sources, please.
"""
LF = chr(10)


def pending(name, text):
    d = os.path.join(r.WORK, "jobs", "pending")
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(r.WORK, "context", "docs"), exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(text)


def all_files(root):
    return sorted(os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/") for d, _s, fs in os.walk(root) for f in fs)


class Meter(unittest.TestCase):
    def test_spend_is_kept_when_a_later_call_fails(self):
        j = r.parse_brief(BRIEF)
        m = r.Meter("haiku")
        n = []

        def post(url, body, headers, timeout=0):
            n.append(1)
            if len(n) == 2:
                raise OSError("network down")
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 100000, "output_tokens": 4000},
                    "content": [{"type": "tool_use", "id": "t1", "name": "list_files", "input": {"repo": "docs", "path": "."}}]}
        with self.assertRaises(OSError):
            r.run_claude(j, post, meter=m)
        self.assertAlmostEqual(m.cost, (100000 * 0.10 + 4000 * 0.50) / 1e6)     # the first call is still on the meter
        self.assertEqual(m.calls, 1)

    def test_process_books_the_spend_of_a_failed_job(self):
        shutil.rmtree(r.WORK, ignore_errors=True)
        pending("2026-11-19-qa-1.md", BRIEF)
        real = r.run_claude

        def boom(job, post=None, clock=None, meter=None):
            meter.add({"input_tokens": 200000, "output_tokens": 10000})        # paid for, then the next call dies
            raise OSError("rate limited")
        r.run_claude = boom
        try:
            status, cost = r.process("2026-11-19-qa-1.md", {}, "2026-11-19")
        finally:
            r.run_claude = real
        self.assertEqual(status, "ERROR")
        self.assertAlmostEqual(cost, (200000 * 0.50 + 10000 * 2.50) / 1e6)       # long-prompt Haiku price, NOT $0
        doc = open(os.path.join(r.WORK, "jobs", "failed", "2026-11-19-qa-1.md"), encoding="utf-8").read()
        self.assertIn("1 model calls, 200000 tokens in", doc)
        self.assertIn("| ERROR | Claude | $0.1250 |", open(os.path.join(r.WORK, "jobs", "status.md"), encoding="utf-8").read())

    def test_a_turn_cannot_overshoot_the_cap(self):
        j = r.parse_brief(BRIEF.replace("max_cost: 0.20", "max_cost: 0.03"))
        bodies = []

        def post(url, body, headers, timeout=0):
            bodies.append(json.loads(json.dumps(body)))
            # the model uses every output token it was allowed
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 20000, "output_tokens": body["max_tokens"]},
                    "content": [{"type": "tool_use", "id": "t%d" % len(bodies), "name": "list_files", "input": {"repo": "docs", "path": "."}}]}
        status, _t, m = r.run_claude(j, post)
        self.assertEqual(status, "COST_CAP")
        self.assertLessEqual(m.cost, 0.03 + 1e-9, "the job went over its cap")
        self.assertGreaterEqual(len(bodies), 1)
        self.assertLess(bodies[-1]["max_tokens"], 8001)
        for b in bodies:                      # every request was sized to what was left
            self.assertLessEqual(b["max_tokens"], 8000)

    def test_no_call_when_even_the_prompt_does_not_fit(self):
        j = r.parse_brief(BRIEF.replace("max_cost: 0.20", "max_cost: 0.01"))
        j["body"] = "x" * 600000                                                   # about 200K tokens of prompt
        calls = []
        status, _t, m = r.run_claude(j, lambda *a, **k: calls.append(1))
        self.assertEqual((status, calls, m.cost), ("COST_CAP", [], 0.0))

    def test_web_reserve_counts_only_searches_left(self):
        j = r.parse_brief(BRIEF.replace("max_cost: 0.20", "max_cost: 0.20" + LF + "allowed: read, write_result, web" + LF + "max_searches: 5"))
        m = r.Meter("haiku")
        a0 = r.affordable_tokens(j, m, 3000, 0)
        m.searches = 5
        self.assertGreater(r.affordable_tokens(j, m, 3000, 0), a0)                 # used searches free their reserve
        noweb = r.parse_brief(BRIEF)
        self.assertGreater(r.affordable_tokens(noweb, r.Meter("haiku"), 3000, 0), a0)


class BadBrief(unittest.TestCase):
    def test_hostile_id_cannot_leave_the_jobs_folder(self):
        for i, evil in enumerate(("../../evil", "..\\..\\evil", "/etc/cron.d/x", "a/b/c", "..", "con:x", "x" * 300)):
            shutil.rmtree(r.WORK, ignore_errors=True)
            name = "brief%d.md" % i
            pending(name, "---" + LF + "id: " + evil + LF + "---" + LF + "Do this job please, thank you very much.")
            before = set(all_files(TMP))
            status, cost = r.process(name, {}, "2026-11-19")
            self.assertEqual((status, cost), ("BAD_BRIEF", 0.0), evil)
            new = sorted(set(all_files(TMP)) - before)
            for f in new:
                self.assertTrue(f.startswith("work/jobs/"), (evil, f))             # nothing outside jobs/
                self.assertRegex(os.path.basename(f), r"^[A-Za-z0-9._-]{1,90}$", (evil, f))
            self.assertTrue([f for f in new if f.startswith("work/jobs/failed/")], evil)
            self.assertFalse(os.path.exists(os.path.join(TMP, "evil.md")))

    def test_safe_name(self):
        self.assertEqual(r.safe_name("../../evil", "x"), "evil")
        self.assertEqual(r.safe_name("", "..", "ok-1"), "ok-1")
        self.assertEqual(r.safe_name("", None), "job")
        self.assertEqual(r.safe_name("2026-11-19-good.id_1"), "2026-11-19-good.id_1")

    def test_good_brief_keeps_its_own_output_name(self):
        shutil.rmtree(r.WORK, ignore_errors=True)
        pending("2026-11-19-qa-1.md", BRIEF.replace("role: research", "role: research" + LF + "output: my-report"))
        real = r.run_claude
        r.run_claude = lambda job, post=None, clock=None, meter=None: ("DONE", "RESULT TEXT", meter)
        try:
            status, _c = r.process("2026-11-19-qa-1.md", {}, "2026-11-19")
        finally:
            r.run_claude = real
        self.assertEqual(status, "DONE")
        self.assertTrue(os.path.exists(os.path.join(r.WORK, "jobs", "done", "my-report.md")))
        self.assertTrue(os.path.exists(os.path.join(r.WORK, "jobs", "briefs", "2026-11-19-qa-1.md")))


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
