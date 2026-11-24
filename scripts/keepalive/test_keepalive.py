import datetime, os, re, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keepalive as k

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")


def run(mins_ago, status="completed", conclusion="success", event="schedule"):
    t = NOW - datetime.timedelta(minutes=mins_ago)
    return {"created_at": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": status, "conclusion": conclusion, "event": event}


class Decide(unittest.TestCase):
    def test_on_time(self):
        self.assertEqual(k.decide(60, [run(80)], NOW)[0], "skip")

    def test_late_over_1_5x(self):
        self.assertEqual(k.decide(60, [run(91)], NOW)[0], "dispatch")
        self.assertEqual(k.decide(60, [run(90)], NOW)[0], "skip")       # exactly 1.5x is not yet late

    def test_never_ran(self):
        self.assertEqual(k.decide(60, [], NOW)[0], "dispatch")

    def test_busy_never_dispatched(self):
        for st in ("queued", "in_progress", "waiting"):
            self.assertEqual(k.decide(60, [run(100, status=st, conclusion=None), run(900)], NOW)[0], "skip")

    def test_one_dispatch_per_hour(self):
        self.assertEqual(k.decide(10, [run(30, event="workflow_dispatch"), run(500)], NOW)[0], "skip")
        self.assertEqual(k.decide(10, [run(61, event="workflow_dispatch"), run(500)], NOW)[0], "dispatch")

    def test_failed_recent_run_counts_as_ran(self):
        self.assertEqual(k.decide(60, [run(10, conclusion="failure")], NOW)[0], "skip")   # no re-run storm on a failing robot

    def test_cancelled_run_does_not_count(self):
        self.assertEqual(k.decide(60, [run(5, conclusion="cancelled"), run(200)], NOW)[0], "dispatch")

    def test_stuck_busy_run_does_not_block_but_a_fresh_one_does(self):
        old = run(200, status="queued", conclusion=None)
        self.assertEqual(k.decide(60, [old, run(300)], NOW, "hunter.yml")[0], "dispatch")
        self.assertEqual(k.flag(60, [old, run(10)], NOW, "hunter.yml"), "RED (stuck)")
        fresh = run(100, status="in_progress", conclusion=None)
        self.assertEqual(k.decide(60, [fresh, run(300)], NOW, "hunter.yml")[0], "skip")
        hb = run(300, status="in_progress", conclusion=None)      # the heartbeat legitimately runs for hours
        self.assertEqual(k.decide(180, [hb, run(900)], NOW, "heartbeat.yml")[0], "skip")
        self.assertEqual(k.flag(180, [hb, run(10)], NOW, "heartbeat.yml"), "ok")

    def test_skipped_run_does_not_count(self):
        self.assertEqual(k.decide(60, [run(5, conclusion="skipped"), run(200)], NOW)[0], "dispatch")


class Report(unittest.TestCase):
    def test_red_over_2x(self):
        self.assertEqual(k.flag(60, [run(121)], NOW), "RED")
        self.assertEqual(k.flag(60, [run(119)], NOW), "ok")
        self.assertEqual(k.flag(60, [], NOW), "RED")

    def test_section(self):
        robots = [("a.yml", 60, {}), ("b.yml", 60, {}), ("c.yml", 60, {})]
        data = {"a.yml": [run(10)], "b.yml": [run(300, conclusion="failure"), run(400)], "c.yml": None}

        def fetch(wf):
            if data[wf] is None:
                raise RuntimeError("x")
            return data[wf]
        text, reds = k.age_section("t", NOW, fetch, robots)
        self.assertEqual(reds, 1)
        self.assertIn("| b | 60 min | 300 min ago | 400 min ago | RED |", text)
        self.assertIn("| a | 60 min | 10 min ago | 10 min ago | ok |", text)
        self.assertIn("UNKNOWN", text)
        self.assertIn("RED robots: 1", text)


class Main(unittest.TestCase):
    """main() driven with a fake API: no network."""

    def setUp(self):
        self.saved = (k.fetch_runs, k.api, k.dispatch)
        self.env = {x: os.environ.get(x) for x in ("GITHUB_TOKEN", "KEEPALIVE_DRY")}
        os.environ["GITHUB_TOKEN"] = "x"
        os.environ.pop("KEEPALIVE_DRY", None)
        self.sent = []

    def tearDown(self):
        k.fetch_runs, k.api, k.dispatch = self.saved
        for key, v in self.env.items():
            if v is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = v

    def quiet(self):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = k.main()
        return code, buf.getvalue()

    def test_one_robot_error_does_not_stop_the_others(self):
        def fetch(token, wf):
            if wf == "outbox.yml":
                raise RuntimeError("boom")
            return []                                           # never ran: dispatch
        k.fetch_runs = fetch
        k.dispatch = lambda token, wf, inputs, repo=None: self.sent.append(wf)
        code, out = self.quiet()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.sent), len(k.ROBOTS) - 1)
        self.assertNotIn("outbox.yml", self.sent)
        self.assertIn("outbox.yml ERROR RuntimeError", out)
        self.assertNotIn("boom", out)                           # only the class name is printed

    def test_all_robots_erroring_returns_1(self):
        def fetch(token, wf):
            raise RuntimeError("rate limit")
        k.fetch_runs = fetch
        self.assertEqual(self.quiet()[0], 1)

    def test_dry_run_dispatches_nothing(self):
        os.environ["KEEPALIVE_DRY"] = "true"
        k.fetch_runs = lambda token, wf: []
        k.dispatch = lambda *a, **kw: self.sent.append(a)
        code, out = self.quiet()
        self.assertEqual((code, self.sent), (0, []))
        self.assertIn("[dry]", out)

    def test_dispatch_sends_ref_main_and_the_inputs(self):
        calls = []
        k.api = lambda method, path, token, body=None: calls.append((method, path, body)) or {}
        k.dispatch("t", "backlink-batch.yml", {"dry_run": "false"}, repo="o/r")
        k.dispatch("t", "hunter.yml", {}, repo="o/r")
        self.assertEqual(calls[0], ("POST", "/repos/o/r/actions/workflows/backlink-batch.yml/dispatches", {"ref": "main", "inputs": {"dry_run": "false"}}))
        self.assertEqual(calls[1][2], {"ref": "main"})
        inputs = {f: i for f, _, i in k.ROBOTS}
        self.assertEqual(inputs["directory-agent.yml"], {"dry_run": "false"})   # its default is TRUE: a bare dispatch would be a dry run


class WorstCase(unittest.TestCase):
    """The hard daily caps of every robot that spends model money add up to $16.98 (Imran 22 Nov: "Spend 14, maybe $15 a day"; 24 Nov: list builder +$2 -> 16.98, approved)."""

    def test_sum_of_the_hard_caps(self):
        base = os.path.join(ROOT, "scripts")
        for sub in ("directory_agent", "hunter", "reports", "email_replier", "mail_router", "list_builder"):
            sys.path.insert(0, os.path.join(base, sub))
        import agent as ag, hunter as hu, builder as lb, backlink_batch as bb, replier as rp, router as rt, inbox_report as ir
        dev_cap = 0.60                                           # same value as the poster's DAY_CAP; read from the poster itself where that file exists
        if os.path.exists(os.path.join(base, "dev_poster", "poster.py")):
            sys.path.insert(0, os.path.join(base, "dev_poster"))
            import poster as pp
            dev_cap = pp.DAY_CAP
            self.assertLessEqual(dev_cap, 0.60)
        wfp = open(os.path.join(ROOT, ".github", "workflows", "platform-poster.yml"), encoding="utf8").read()
        platform_cap = float(re.search(r'PLATFORM_DAY_USD_CAP: "([\d.]+)"', wfp).group(1))   # the cap the platform poster really runs with (workflow env)
        self.assertLessEqual(platform_cap, 0.90)
        PER_CALL_HAIKU = 0.003                                   # 15k tokens in x $0.10/M + 3000 out x $0.50/M (scripts/reports/common.py prices)
        table = {
            "directory agent": ag.USD_CAP,
            "hunter (runner enforces $0.60 a job)": hu.DAILY_CAP,
            "list builder": lb.DAY_CAP,
            "backlink batch": bb.AI_USD_PER_DAY,
            "ops report (daily counter, 160 calls)": ir.REPORT_AI_PER_DAY * PER_CALL_HAIKU,
            "email replier": rp.AI_CALLS_PER_DAY * PER_CALL_HAIKU,
            "mail router": rt.AI_CALLS_PER_DAY * PER_CALL_HAIKU,
            "DEV poster (daily cap in poster.py when that file exists)": dev_cap,
            "platform poster (PLATFORM_DAY_USD_CAP in platform-poster.yml)": platform_cap,
        }
        total = sum(table.values())
        self.assertLessEqual(total, 17.0, table)
        self.assertAlmostEqual(total, 16.98, places=2)
        # NOT in the sum, on purpose: the job runner's other jobs (Director-submitted QA, research and code helper jobs) are not robots; they sit under
        # the runner's OWN $15/day cap (job-runner.yml DAILY_COST_CAP), which is separate. The hunter jobs also run through that runner.
        jr = open(os.path.join(ROOT, ".github", "workflows", "job-runner.yml"), encoding="utf8").read()
        self.assertIn('DAILY_COST_CAP: "15.00"', jr)
        self.assertEqual(ir.REPORT_AI_PER_DAY, 160)
        self.assertEqual(ag.USD_CAP, 7.10)
        self.assertEqual(hu.DAILY_CAP, 3.0)
        self.assertEqual(lb.DAY_CAP, 3.0)
        self.assertEqual(lb.DEEPSEEK_DAY_CAP, 1.0)      # the DeepSeek twin has its OWN cap and is NOT in the sum above
        self.assertEqual(bb.AI_USD_PER_DAY, 1.0)
        self.assertLessEqual(rp.AI_CALLS_PER_DAY * PER_CALL_HAIKU, 0.45 + 1e-9)


class Config(unittest.TestCase):
    wf = os.path.join(ROOT, ".github", "workflows")

    def name_of(self, f):
        return re.search(r"^name: (.+)$", open(os.path.join(self.wf, f), encoding="utf8").read(), re.M).group(1).strip()

    def test_every_robot_is_listed_in_the_trigger(self):
        text = open(os.path.join(self.wf, "keepalive-check.yml"), encoding="utf8").read()
        listed = set(re.findall(r'^      - "(.+)"$', text, re.M))
        self.assertEqual(listed, {self.name_of(f) for f, _, _ in k.ROBOTS})
        self.assertNotIn(self.name_of("keepalive-check.yml"), listed)       # no loop

    def test_robots_exist_and_have_dispatch(self):
        for f, _, inputs in k.ROBOTS:
            t = open(os.path.join(self.wf, f), encoding="utf8").read()
            self.assertIn("workflow_dispatch", t, f)
            for key in inputs:
                self.assertIn(key + ":", t, f)

    def test_workflow_permissions_and_concurrency(self):
        t = open(os.path.join(self.wf, "keepalive-check.yml"), encoding="utf8").read()
        self.assertIn("actions: write", t)
        self.assertIn("group: keepalive-check", t)
        self.assertIn("cancel-in-progress: false", t)

    # scheduled workflows the keep-alive check deliberately does NOT start, with the reason:
    NOT_WATCHED = {
        "keepalive-check.yml": "this check itself",
        "keepalive.yml": "monthly commit that keeps the schedules enabled; nothing to catch up",
        "app-scan.yml": "manual / long scan, not a timed robot",
        "list-builder-deepseek.yml": "the DeepSeek twin of the list builder, optional (runs only when the DEEPSEEK_API_KEY secret exists); a missed run is simply missed",
        "dev-poster.yml": "publishes to dev.to only when its own repository switch DEV_POSTER_LIVE is true; a keep-alive dispatch would run it dry (default dry_run) or bypass the switch, so a missed day is simply missed",
    }

    def test_readme_and_workflows_say_what_the_14_98_covers(self):
        r = open(os.path.join(ROOT, "README.md"), encoding="utf8").read()
        self.assertIn("$16.98 a day is the hard cap of THESE robots only", r)
        self.assertIn("separate $15/day cap", r)
        self.assertIn("separate from, and not part of, the $16.98", open(os.path.join(self.wf, "job-runner.yml"), encoding="utf8").read())
        self.assertIn("$16.98 a day is the hard cap of the LISTED robots", open(os.path.join(self.wf, "directory-agent.yml"), encoding="utf8").read())

    def test_ops_report_has_no_write_permission(self):
        t = open(os.path.join(self.wf, "ops-report.yml"), encoding="utf8").read()
        block = t.split("permissions:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("actions: read", block)
        self.assertIn("contents: read", block)
        self.assertNotIn("write", block)
        ka = open(os.path.join(self.wf, "keepalive-check.yml"), encoding="utf8").read().split("permissions:", 1)[1].split("concurrency:", 1)[0]
        self.assertIn("contents: read", ka)
        self.assertNotIn("contents: write", ka)

    def test_every_scheduled_workflow_is_watched(self):
        watched = {f for f, _, _ in k.ROBOTS}
        for f in os.listdir(self.wf):
            t = open(os.path.join(self.wf, f), encoding="utf8").read()
            if re.search(r"^\s+- cron:", t, re.M) and f not in self.NOT_WATCHED:
                self.assertIn(f, watched, f)


if __name__ == "__main__":
    unittest.main()
