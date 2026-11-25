import datetime, json, os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c
import output_alarms as oa

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)

BATCH = """# Backlink batch log (newest first)

## Backlink batch 2026-11-25 09:45 UTC (LIVE)

- Outreach: 0 e-mail(s) queued for the outbox robot, 0 prospect(s) skipped.

## Backlink batch 2026-11-24 22:07 UTC (LIVE)

- Outreach: 3 e-mail(s) queued for the outbox robot, 0 prospect(s) skipped.

## Backlink batch 2026-11-24 10:07 UTC (DRY RUN)

- Outreach: 9 e-mail(s) queued for the outbox robot.

## Backlink batch 2026-11-20 10:07 UTC (LIVE)

- Outreach: 5 e-mail(s) queued for the outbox robot.
"""
SENT = {
    "outbox/sent/2026-11-24-outreach-a-1.json": json.dumps({"sent_at": "20261124T220909Z"}),
    "outbox/sent/watchdog-2026-11-25.json": json.dumps({"sent_at": "20261125T080000Z"}),     # not link outreach
    "outbox/sent/2026-11-19-seo-swap-x-1.json": json.dumps({"sent_at": "20261119T100000Z"}),
}
AGENT = """| 2026-11-23 | attempts 51 | tool listings tried 126 | successes today 4 | cost 1.35 USD | untried left 58 |
| 2026-11-24 | attempts 72 | tool listings tried 70 | successes today 7 | cost 2.30 USD | untried left 0 |
| 2026-11-24 | attempts 24 | tool listings tried 126 | successes today 14 | cost 0.86 USD | untried left 34 |
| 2026-11-25 | attempts 150 | tool listings tried 14 | successes today 1 | cost 2.26 USD | untried left 0 |
"""
LIVE = """# Live links

| site | status | link rel | listing URL | first seen live | last checked | our page |
|---|---|---|---|---|---|---|
| a.com | LIVE | followed | https://a.com/x | 2026-11-25 | 2026-11-25 | / |
| b.com | PENDING | | | | 2026-11-25 | |
| c.com | LIVE | nofollow | https://c.com/x | 2026-11-10 | 2026-11-25 | / |
"""
PLAT_FAIL = """# Platform poster log (private)

| date (UTC) | platform | page | result | article URL | our link | cost | note |
|---|---|---|---|---|---|---|---|
| 2026-11-24 | wordpress | x | FAIL, not published | | | $0.06 | 7-word overlap |
"""
PLAT_OK = PLAT_FAIL + "| 2026-11-25 | blogger | y | PUBLISHED | https://b.example/p | https://inayaflow.ai/tools | $0.2 | |\n"


def cand(rows):
    return json.dumps({"sites": rows})


class Extractors(unittest.TestCase):
    def test_outreach_counts_live_runs_and_real_sent_mails_only(self):
        ev = oa.outreach_events(BATCH, SENT)
        self.assertEqual(oa.within(ev, NOW, 24), 3 + 1)           # 3 queued (22:07), 1 sent outreach (22:09); the DRY RUN, the watchdog and old mails do not count
        self.assertEqual(oa.within(ev, NOW, 48), 4)
        self.assertEqual(oa.within(ev, NOW, 24 * 7), 4 + 1 + 5)   # + the 19 Nov swap mail and the 20 Nov queue line

    def test_directory_counts_real_submissions_and_new_live(self):
        ev = oa.directory_events(AGENT, LIVE)
        self.assertEqual(oa.within(ev, NOW, 24), 14 + 1 + 1)      # 24 Nov highest "successes today" (14, noon 24 h ago) + 25 Nov (1) + a.com first seen live 25 Nov
        self.assertEqual(oa.within(ev, NOW, 12), 14 + 1 + 1)      # date-only rows count for their whole UTC day: a 12 h window reaches back to yesterday noon - 12 h
        early = datetime.datetime(2026, 11, 25, 8, 0, tzinfo=UTC)  # before noon: today's date-only rows still count at once
        self.assertEqual(oa.within(ev, early, 24), 14 + 1 + 1)

    def test_published_needs_a_real_url(self):
        self.assertEqual(oa.within(oa.published_events(PLAT_FAIL), NOW, 48), 0)
        self.assertEqual(oa.within(oa.published_events(PLAT_OK), NOW, 24), 1)

    def test_candidates_split_by_provider(self):
        rows = [{"added": "2026-11-25", "found_by": "sonnet"}, {"added": "2026-11-25", "found_by": "sonnet"}, {"added": "2026-11-20", "found_by": "sonnet"}, {"added": "", "found_by": "x"}]
        self.assertEqual(oa.within(oa.candidate_events(cand(rows), exclude_found_by="deepseek"), NOW, 24), 2)
        self.assertIsNone(oa.candidate_events("not json"))


class Table(unittest.TestCase):
    def inputs(self, **kw):
        d = {"batch_log": BATCH, "outbox_sent": SENT, "agent_summary": AGENT, "live_links": LIVE, "platform_log": PLAT_FAIL, "dev_log": PLAT_FAIL,
             "cand_sonnet": cand([{"added": "2026-11-25", "found_by": "sonnet"}]), "cand_deepseek": "", "prospects": json.dumps([{"n": 1}] * 91)}
        d.update(kw)
        return d

    def test_zero_output_is_broken_in_words_at_the_top(self):
        text, s, _ = oa.build(self.inputs(), NOW, {"platform-poster.yml": 130})
        self.assertTrue(text.startswith("## ROBOT OUTPUT ALARM"))
        for name in ("Platform poster", "DEV poster", "List builder (DeepSeek)"):
            self.assertIn(name, "; ".join(s["broken24"]))
        self.assertIn("| Platform poster | 2.2 h ago | 0 | articles published (48 h: 0) | **BROKEN 48h+ (0 output for 48 hours)** |", text)
        self.assertIn("ROBOT HEALTH RED", text)
        self.assertEqual(sorted(s["broken48"]), sorted(s["broken24"]))

    def test_24h_zero_but_48h_nonzero_is_broken_not_48(self):
        inp = self.inputs()
        inp["platform_log"] = PLAT_FAIL + "| 2026-11-23 | blogger | y | PUBLISHED | https://b.example/p | l | $0.2 | |\n"
        text, s, _ = oa.build(inp, NOW)
        self.assertIn("Platform poster", "; ".join(s["broken24"]))
        self.assertNotIn("Platform poster", s["broken48"])

    def test_all_good(self):
        inp = self.inputs(platform_log=PLAT_OK, dev_log=PLAT_OK, cand_deepseek=cand([{"added": "2026-11-25", "found_by": "deepseek"}]))
        snaps = [{"t": "2026-11-24 10:00", "prospects": 80}]
        text, s, snaps2 = oa.build(inp, NOW, {}, snaps)
        self.assertEqual(s["broken24"], [])
        self.assertTrue(text.startswith("## ROBOT OUTPUT: every robot produced something"))
        self.assertNotIn("**BROKEN", text)
        self.assertTrue(any(x["t"] == "2026-11-25 12:00" and x["prospects"] == 91 for x in snaps2))

    def test_hunter_needs_a_baseline_then_measures_the_delta(self):
        text, s, snaps = oa.build(self.inputs(), NOW)
        self.assertIn("NO BASELINE YET", text)
        self.assertNotIn("Prospect hunter", "; ".join(s["broken24"]))
        later = NOW + datetime.timedelta(hours=25)
        text2, s2, _ = oa.build(self.inputs(), later, {}, snaps)                 # same 91 rows 25 h later = 0 added
        self.assertIn("Prospect hunter", "; ".join(s2["broken24"]))
        text3, s3, _ = oa.build(self.inputs(prospects=json.dumps([{"n": 1}] * 95)), later, {}, snaps)
        self.assertNotIn("Prospect hunter", "; ".join(s3["broken24"]))

    def test_unreadable_log_is_unknown_not_broken(self):
        text, s, _ = oa.build(self.inputs(platform_log=""), NOW)
        self.assertIn("Platform poster", s["unknown"])
        self.assertNotIn("Platform poster", s["broken24"])

    def test_last_run_column(self):
        text, _, _ = oa.build(self.inputs(), NOW, {"directory-agent.yml": 45, "hunter.yml": "never"})
        self.assertIn("| Directory agent | 45 min ago |", text)
        self.assertIn("| Prospect hunter | never |", text)

    def test_snapshots_are_pruned_and_hourly(self):
        s = oa.update_snapshots([{"t": "2026-11-18 10:00", "prospects": 1}, {"t": "2026-11-25 11:30", "prospects": 90}], NOW, 91)
        self.assertEqual([x["t"] for x in s], ["2026-11-25 11:30", "2026-11-25 12:00"])
        s2 = oa.update_snapshots(s, NOW + datetime.timedelta(minutes=10), 92)
        self.assertEqual(len(s2), 2)                                              # same hour: no second snapshot


class DateOnlyAndOff(unittest.TestCase):
    def test_date_only_row_is_not_a_false_broken_late_in_the_day(self):
        late = datetime.datetime(2026, 11, 26, 11, 0, tzinfo=UTC)         # yesterday's post (25 Nov) is 23 h old at noon-based timing, 35 h by the old count
        ev = oa.published_events(PLAT_OK)                                  # dated 2026-11-25
        self.assertEqual(oa.within(ev, late, 24), 1)
        self.assertEqual(oa.within(ev, datetime.datetime(2026, 11, 27, 1, 0, tzinfo=UTC), 24), 0)   # 61 h by the day-noon: gone

    def inputs(self):
        return {"batch_log": BATCH, "outbox_sent": SENT, "agent_summary": AGENT, "live_links": LIVE, "platform_log": PLAT_FAIL, "dev_log": PLAT_FAIL,
                "cand_sonnet": cand([{"added": "2026-11-25", "found_by": "sonnet"}]), "cand_deepseek": "", "prospects": json.dumps([{"n": 1}] * 91)}

    def test_off_robots_are_never_broken(self):
        off = {"DEV poster": "switch DEV_POSTER_LIVE not true", "Platform poster": "switch PLATFORM_POSTER_LIVE not true", "List builder (DeepSeek)": "key not set"}
        text, s, _ = oa.build(self.inputs(), NOW, {}, [], off)
        self.assertEqual(s["broken24"], [])
        self.assertEqual(s["broken48"], [])
        self.assertIn("OFF (key not set)", text)
        self.assertNotIn("**BROKEN", text)
        self.assertTrue(text.startswith("## ROBOT OUTPUT: every robot"))

    def test_off_robot_with_output_shows_its_output(self):
        text, s, _ = oa.build(dict(self.inputs(), platform_log=PLAT_OK), NOW, {}, [], {"Platform poster": "x"})
        self.assertIn("| Platform poster | unknown | 1 |", text)
        self.assertIn("ok", text.split("| Platform poster")[1].split("\n")[0])

    def test_switch_states_from_variables_and_log(self):
        import urllib.error, io
        def api_get(path, token):
            if path.endswith("DEV_POSTER_LIVE"):
                return {"value": "false"}
            if path.endswith("PLATFORM_POSTER_LIVE"):
                return {"value": "true"}
        out = oa.switch_states("t", api_get, lambda wf, tok: "x\nlist-builder-deepseek: no key, skipped\n")
        self.assertEqual(sorted(out), ["DEV poster", "List builder (DeepSeek)"])

        def forbidden(path, token):
            raise urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(b""))
        out = oa.switch_states("t", forbidden, lambda wf, tok: "")
        self.assertEqual(out, {})                                          # token cannot read variables: unknown, NOT called off

        def missing(path, token):
            raise urllib.error.HTTPError("u", 404, "x", {}, io.BytesIO(b""))
        self.assertIn("DEV poster", oa.switch_states("t", missing, lambda wf, tok: ""))   # variable not set = off

    def test_error_block_goes_to_the_very_top(self):
        import action_tracker as at
        rep = at.put_on_top("# Inbox + backlinks report\n\nbody\n", at.mail_health([]) + "\n" + at.action_block([]))
        out = at.put_on_top(rep, oa.error_block(ValueError("secret")))
        self.assertTrue(out.split("\n")[2].startswith("> OUTPUT ALARM SECTION ERROR"))
        self.assertNotIn("secret", out)


class Fixtures(unittest.TestCase):
    """The reader works from FIXTURE_DIR the same way the other report robots do."""

    def test_read_inputs_from_fixture_dir(self):
        import tempfile
        d = tempfile.mkdtemp()
        for br, path, text in (("ops-reports", "ops/backlink_batch_log.md", BATCH), ("ops-directory-logs", "ops/directory_agent_summary.md", AGENT),
                               ("ops-platform-poster", "ops/platform_poster_log.md", PLAT_OK), ("ops-outbox", "outbox/sent/2026-11-24-outreach-a-1.json", SENT["outbox/sent/2026-11-24-outreach-a-1.json"])):
            os.makedirs(os.path.dirname(os.path.join(d, br, path)), exist_ok=True)
            open(os.path.join(d, br, path), "w", encoding="utf-8").write(text)
        old = c.FIXTURE_DIR
        c.FIXTURE_DIR = d
        try:
            inp = oa.read_inputs()
        finally:
            c.FIXTURE_DIR = old
        self.assertIn("Backlink batch", inp["batch_log"])
        self.assertEqual(list(inp["outbox_sent"]), ["outbox/sent/2026-11-24-outreach-a-1.json"])
        self.assertEqual(inp["live_links"], "")


class InReport(unittest.TestCase):
    def test_goes_above_mail_health_and_survives_failure(self):
        import action_tracker as at
        rep = at.put_on_top("# Inbox + backlinks report\n\nbody\n", at.mail_health([]) + "\n" + at.action_block([]))
        out = at.put_on_top(rep, oa.build({}, NOW)[0])
        lines = out.split("\n")
        self.assertTrue(lines[2].startswith("## ROBOT OUTPUT"))
        self.assertLess(out.index("## ROBOT OUTPUT"), out.index("## MAIL HEALTH"))


if __name__ == "__main__":
    unittest.main()
