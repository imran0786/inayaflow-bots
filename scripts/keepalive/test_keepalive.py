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
            if re.search(r"^\s+- cron:", t, re.M) and f not in ("keepalive-check.yml", "keepalive.yml", "app-scan.yml") :
                self.assertIn(f, watched, f)


if __name__ == "__main__":
    unittest.main()
