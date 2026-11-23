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
            self.assertEqual(k.decide(60, [run(500, status=st, conclusion=None), run(900)], NOW)[0], "skip")

    def test_one_dispatch_per_hour(self):
        self.assertEqual(k.decide(10, [run(30, event="workflow_dispatch"), run(500)], NOW)[0], "skip")
        self.assertEqual(k.decide(10, [run(61, event="workflow_dispatch"), run(500)], NOW)[0], "dispatch")

    def test_failed_recent_run_counts_as_ran(self):
        self.assertEqual(k.decide(60, [run(10, conclusion="failure")], NOW)[0], "skip")   # no re-run storm on a failing robot

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

    def test_every_scheduled_workflow_is_watched(self):
        watched = {f for f, _, _ in k.ROBOTS}
        for f in os.listdir(self.wf):
            t = open(os.path.join(self.wf, f), encoding="utf8").read()
            if re.search(r"^\s+- cron:", t, re.M) and f not in ("keepalive-check.yml", "keepalive.yml", "app-scan.yml") :
                self.assertIn(f, watched, f)


if __name__ == "__main__":
    unittest.main()
