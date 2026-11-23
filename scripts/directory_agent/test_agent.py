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


class Budget(unittest.TestCase):
    def test_defaults_match_the_workflow(self):
        self.assertEqual(ag.RUN_SECONDS, 8400)                 # below the 150-minute step timeout
        self.assertEqual(ag.USD_CAP, 6.0)
        self.assertEqual(ag.ATTEMPT_MAX_USD, 1.0)

    def test_dollar_cap_counts_priced_and_unpriced_attempts(self):
        import tempfile
        old = ag.SPEND_PATH
        ag.SPEND_PATH = os.path.join(tempfile.mkdtemp(), "ops", "spend.json")
        try:
            sp = {}
            ag.add_spend(sp, "2026-11-25", {"cost": 1.5})
            ag.add_spend(sp, "2026-11-25", {"cost": None})                      # unpriced: the assumed flat price
            self.assertAlmostEqual(ag.spent_today(sp, "2026-11-25"), 1.5 + ag.UNPRICED_USD)
            self.assertEqual(ag.spent_today(sp, "2026-11-26"), 0.0)             # a new day starts at zero
            self.assertEqual(ag.load_spend(), sp)
            ag.add_spend(sp, "2026-11-25", {"cost": ag.USD_CAP})
            self.assertGreaterEqual(ag.spent_today(sp, "2026-11-25"), ag.USD_CAP)
        finally:
            ag.SPEND_PATH = old

    def test_two_soft_failures_in_a_row_stop_the_tool_loop(self):
        self.assertEqual(ag.MAX_FAILS_IN_A_ROW, 2)
        fails = 0
        order = []
        for st in ("SUBMITTED", "TIMEOUT", "OTHER", "SUBMITTED"):
            fails = fails + 1 if st in ag.SOFT_FAILS else 0
            order.append(fails)
            if fails >= ag.MAX_FAILS_IN_A_ROW:
                break
        self.assertEqual(order, [0, 1, 2])


class ManualRuns(unittest.TestCase):
    def test_manual_batch_runs_respect_the_dollar_cap_before_every_attempt(self):
        import tempfile, io, contextlib
        d = tempfile.mkdtemp()
        batch = os.path.join(d, "batch.json")
        json.dump({"sites": [{"name": "S%d" % i, "url": "https://s%d.example/submit" % i} for i in range(5)]}, open(batch, "w"))
        saved = (ag.STATE_PATH, ag.SPEND_PATH, ag.LOG_PATH, ag.SUMMARY_PATH, ag.OLD_STATE_PATH, ag.VSTATE_PATH, ag.USD_CAP, ag.run_one, ag.WITH_TOOLS, ag.ROOT, ag.ATTEMPT_MAX_USD, ag.preflight_limits)
        calls = []

        def fake(s, product=None):
            calls.append((s["name"], product["id"] if product else "homepage"))
            return {"status": "SUBMITTED", "note": "", "secs": 1, "cost": 0.4, "cost_estimated": False, "steps": 1, "usage": None, "flags": [], "model": "m"}
        ag.STATE_PATH, ag.SPEND_PATH = os.path.join(d, "ops", "state.json"), os.path.join(d, "ops", "spend.json")
        ag.LOG_PATH, ag.SUMMARY_PATH = os.path.join(d, "ops", "log.md"), os.path.join(d, "ops", "sum.md")
        ag.OLD_STATE_PATH, ag.VSTATE_PATH = os.path.join(d, "ops", "old.json"), os.path.join(d, "ops", "v.json")
        ag.USD_CAP, ag.run_one, ag.WITH_TOOLS, ag.ATTEMPT_MAX_USD = 1.0, fake, True, 0.5
        ag.preflight_limits = lambda: True
        os.environ["BATCH_FILE"] = batch
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ag.main()
        finally:
            os.environ.pop("BATCH_FILE", None)
            (ag.STATE_PATH, ag.SPEND_PATH, ag.LOG_PATH, ag.SUMMARY_PATH, ag.OLD_STATE_PATH, ag.VSTATE_PATH, ag.USD_CAP, ag.run_one, ag.WITH_TOOLS, ag.ROOT, ag.ATTEMPT_MAX_USD, ag.preflight_limits) = saved
        self.assertEqual(len(calls), 2, calls)          # cap 1.0, reserve 0.5: homepage (0+0.5 fits) 0.4, tool (0.4+0.5 fits) 0.8, third (0.8+0.5 > 1.0) refused BEFORE it starts
        spend = json.load(open(os.path.join(d, "ops", "spend.json")))
        self.assertAlmostEqual(sum(spend.values()), 0.8)  # the spend file was updated after each attempt


class HardAttemptStop(unittest.TestCase):
    """QA round 3: one attempt can never cost more than the $1.00 reserve."""

    def test_token_limits_keep_each_run_inside_its_budget(self):
        for model, budget in (("claude-haiku-5-5", ag.RUN_BUDGET_PRIMARY_USD), ("claude-sonnet-5-5", ag.RUN_BUDGET_FALLBACK_USD)):
            tin, tout = ag.token_limits(model, budget)
            pin, pout = ag.HAIKU_PRICE_PER_M if "haiku" in model else (ag.PRICE_IN, ag.PRICE_OUT)
            self.assertLessEqual(tin / 1e6 * pin + tout / 1e6 * pout, budget + 1e-9)
        self.assertEqual(ag.token_limits("claude-sonnet-5-5", 0.40), (93333, 8000))
        self.assertEqual(ag.token_limits("claude-haiku-5-5", 0.25), (175000, 15000))

    def test_primary_plus_fallback_plus_the_crossing_responses_fit_the_reserve(self):
        crossing_haiku, crossing_sonnet = 0.01, 0.29          # the one response that goes over a limit: Sonnet 35k in + 12k out max_tokens
        worst = ag.RUN_BUDGET_PRIMARY_USD + crossing_haiku + ag.RUN_BUDGET_FALLBACK_USD + crossing_sonnet
        self.assertLessEqual(worst, ag.ATTEMPT_MAX_USD)
        self.assertEqual(ag.UNPRICED_USD, 0.5)                # unpriced attempts count $0.50

    def fake_loop(self, accept=True):
        import types
        calls = []

        class UL:
            def __init__(self, **kw):
                if not accept and "input_tokens_limit" in kw:
                    raise TypeError("no such limit")
                calls.append(kw)
        loop = types.ModuleType("browser_use.agent.loop")
        loop.UsageLimits = UL
        pkg, agent_pkg = types.ModuleType("browser_use"), types.ModuleType("browser_use.agent")
        saved = {k: sys.modules.get(k) for k in ("browser_use", "browser_use.agent", "browser_use.agent.loop")}
        sys.modules.update({"browser_use": pkg, "browser_use.agent": agent_pkg, "browser_use.agent.loop": loop})
        self.addCleanup(lambda: [sys.modules.pop(k) if v is None else sys.modules.__setitem__(k, v) for k, v in saved.items()])
        return loop, calls

    def test_installed_limits_reach_every_run_and_are_not_stacked(self):
        loop, calls = self.fake_loop()
        self.assertTrue(ag.install_limits())
        self.assertTrue(ag.install_limits())                              # twice: still ONE wrapper level
        ag._CUR_LIMITS["tokens"] = (111, 22)
        loop.UsageLimits(request_limit=41)
        self.assertEqual(calls[-1], {"request_limit": 41, "input_tokens_limit": 111, "output_tokens_limit": 22})
        ag._CUR_LIMITS["tokens"] = None
        loop.UsageLimits(request_limit=3)
        self.assertEqual(calls[-1], {"request_limit": 3})

    def test_library_without_the_limits_stops_the_whole_run_before_any_attempt(self):
        import io, contextlib, tempfile
        self.fake_loop(accept=False)
        self.assertFalse(ag.preflight_limits())
        ran = []
        saved = (ag.run_one, ag.ROOT)
        ag.run_one = lambda *a, **k: ran.append(1)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = ag.main()
        finally:
            ag.run_one = saved[0]
        self.assertEqual((code, ran), (1, []))
        self.assertIn("NO attempt started", out.getvalue())

    def test_run_one_gives_the_primary_and_the_fallback_their_own_budget(self):
        seen = []

        async def fake_run_site(site, model, product=None, budget=None):
            seen.append((model, budget))
            return {"status": "OTHER", "note": "", "secs": 1, "cost": 0.1, "cost_estimated": False, "steps": 1, "usage": None, "flags": []}
        saved = ag.run_site
        ag.run_site = fake_run_site
        try:
            r = ag.run_one({"name": "S", "url": "https://s.example"})
        finally:
            ag.run_site = saved
        self.assertEqual(seen, [(ag.MODEL, ag.RUN_BUDGET_PRIMARY_USD), (ag.FALLBACK_MODEL, ag.RUN_BUDGET_FALLBACK_USD)])
        self.assertAlmostEqual(r["cost"], 0.2)

    def test_spend_file_is_re_read_before_every_attempt(self):
        import tempfile, io, contextlib, datetime
        d = tempfile.mkdtemp()
        batch = os.path.join(d, "batch.json")
        json.dump({"sites": [{"name": "S%d" % i, "url": "https://s%d.example/submit" % i} for i in range(4)]}, open(batch, "w"))
        names = ("STATE_PATH", "SPEND_PATH", "LOG_PATH", "SUMMARY_PATH", "OLD_STATE_PATH", "VSTATE_PATH", "USD_CAP", "run_one", "WITH_TOOLS", "ATTEMPT_MAX_USD", "preflight_limits")
        saved = {n: getattr(ag, n) for n in names}
        calls = []
        today = datetime.date.today().isoformat()

        def fake(s, product=None):
            calls.append(s["name"])
            json.dump({today: 5.5}, open(ag.SPEND_PATH, "w"))             # "another process" spent the day's money meanwhile
            return {"status": "SUBMITTED", "note": "", "secs": 1, "cost": 0.0, "cost_estimated": False, "steps": 1, "usage": None, "flags": [], "model": "m"}
        ag.STATE_PATH, ag.SPEND_PATH = os.path.join(d, "ops", "state.json"), os.path.join(d, "ops", "spend.json")
        ag.LOG_PATH, ag.SUMMARY_PATH = os.path.join(d, "ops", "log.md"), os.path.join(d, "ops", "sum.md")
        ag.OLD_STATE_PATH, ag.VSTATE_PATH = os.path.join(d, "ops", "old.json"), os.path.join(d, "ops", "v.json")
        ag.USD_CAP, ag.run_one, ag.WITH_TOOLS, ag.ATTEMPT_MAX_USD, ag.preflight_limits = 6.0, fake, True, 1.0, (lambda: True)
        os.environ["BATCH_FILE"] = batch
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ag.main()
        finally:
            os.environ.pop("BATCH_FILE", None)
            for n, v in saved.items():
                setattr(ag, n, v)
        self.assertEqual(len(calls), 1, calls)        # after the first attempt the file says 5.5 + 1.0 > 6.0: nothing else starts

    def test_two_directory_agent_runs_can_never_overlap(self):
        w = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".github", "workflows", "directory-agent.yml"), encoding="utf8").read()
        self.assertIn("group: directory-agent", w)
        self.assertIn("cancel-in-progress: false", w)
        self.assertIn('DAILY_USD_CAP: "6"', w)
        self.assertIn('ATTEMPT_MAX_USD: "1.00"', w)
        self.assertIn("agent.preflight_limits()", w)


if __name__ == "__main__":
    unittest.main()
