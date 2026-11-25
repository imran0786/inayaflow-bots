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
        self.assertEqual(ag.USD_CAP, 7.10)
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
        # the response that crosses a limit is a LARGE one: 93,000 tokens in + 12,000 out (the library's fixed max_tokens), at the conservative prices
        self.assertEqual((ag.CROSS_IN_TOKENS, ag.CROSS_OUT_TOKENS), (93_000, 12_000))
        crossing_sonnet = 93_000 / 1e6 * ag.PRICE_IN + 12_000 / 1e6 * ag.PRICE_OUT
        crossing_haiku = 93_000 / 1e6 * ag.HAIKU_PRICE_PER_M[0] + 12_000 / 1e6 * ag.HAIKU_PRICE_PER_M[1]
        self.assertAlmostEqual(crossing_sonnet, 0.459, places=3)
        worst = ag.RUN_BUDGET_PRIMARY_USD + crossing_haiku + ag.RUN_BUDGET_FALLBACK_USD + crossing_sonnet
        self.assertAlmostEqual(worst, ag.worst_attempt_usd(), places=6)
        self.assertLessEqual(worst, ag.ATTEMPT_MAX_USD)
        self.assertLess(worst, 0.97)
        # the old budgets (0.25 / 0.40) would NOT fit with the large crossing request: the test would have failed
        self.assertGreater(ag.worst_attempt_usd(budget_p=0.25, budget_f=0.40), ag.ATTEMPT_MAX_USD)
        self.assertEqual(ag.UNPRICED_USD, 1.0)                # an attempt with no known price is booked at the full reserve

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

    def fake_library(self, loop_body=None, agent_init=None):
        """A tiny browser_use package on disk (so inspect.getsource works) shaped like the real one."""
        import tempfile, importlib
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "browser_use", "agent"))
        loop = loop_body or (
            "class UsageLimits:\n"
            "    def __init__(self, **kw):\n"
            "        self.request_limit = kw.get('request_limit'); self.input_tokens_limit = kw.get('input_tokens_limit'); self.output_tokens_limit = kw.get('output_tokens_limit')\n"
            "async def run(agent, task, session):\n"
            "    result = await agent.run(task, usage_limits=UsageLimits(request_limit=session.max_steps + 1))\n"
            "    return result\n")
        open(os.path.join(d, "browser_use", "agent", "loop.py"), "w").write(loop)
        open(os.path.join(d, "browser_use", "agent", "__init__.py"), "w").write("")
        open(os.path.join(d, "browser_use", "__init__.py"), "w").write(
            "class Agent:\n    def __init__(self, task, %s):\n        pass\n" % (agent_init or "judge=True, max_context_chars=None"))
        keys = ("browser_use", "browser_use.agent", "browser_use.agent.loop")
        saved = {k: sys.modules.pop(k, None) for k in keys}
        sys.path.insert(0, d)
        importlib.invalidate_caches()

        def cleanup():
            sys.path.remove(d)
            for k in keys:
                sys.modules.pop(k, None)
                if saved[k] is not None:
                    sys.modules[k] = saved[k]
        self.addCleanup(cleanup)
        import browser_use.agent.loop as bl
        return bl

    def test_prover_refuses_every_other_way_to_run_the_agent(self):
        for other in ("run_sync(task)", "run_stream(task)", "iter(task)", "run_stream_events(task)"):
            body = ("class UsageLimits:\n    def __init__(self, **kw):\n        self.input_tokens_limit = kw.get('input_tokens_limit'); self.output_tokens_limit = kw.get('output_tokens_limit')\n"
                    "async def run(agent, task, session):\n"
                    "    x = agent.%s\n"
                    "    return await agent.run(task, usage_limits=UsageLimits(request_limit=session.max_steps + 1))\n") % other
            self.fake_library(loop_body=body)
            self.assertFalse(ag.preflight_limits(), other)

    def test_built_agent_is_checked_at_runtime(self):
        class A:
            judge, max_context_chars = False, ag.HUGE_CONTEXT
        self.assertTrue(ag.check_agent_built(A()))
        for judge, ctx in ((True, ag.HUGE_CONTEXT), (False, 100_000), (False, None)):
            B = type("B", (), {"judge": judge, "max_context_chars": ctx})
            with self.assertRaises(ag.LimitsBroken):
                ag.check_agent_built(B())

    def test_a_broken_limit_stops_the_whole_run_instead_of_becoming_a_failed_attempt(self):
        async def fake_run_site(site, model, product=None, budget=None):
            raise ag.LimitsBroken("judge on")
        import io, contextlib
        saved = ag.run_site
        ag.run_site = fake_run_site
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(ag.LimitsBroken):
                    ag.run_one({"name": "S", "url": "https://s.example"})
        finally:
            ag.run_site = saved

    def test_run_site_checks_the_built_agent_before_the_first_request(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py"), encoding="utf8").read()
        self.assertLess(src.index("check_agent_built(agent)"), src.index("agent.run(max_steps=MAX_STEPS"))

    def test_prover_accepts_a_loop_shaped_like_the_real_one(self):
        bl = self.fake_library()
        self.assertTrue(ag.preflight_limits())
        self.assertIsNone(ag._CUR_LIMITS["tokens"])               # the proof leaves nothing behind

    def test_prover_refuses_when_the_limits_are_not_per_attempt(self):
        two_runs = ("class UsageLimits:\n    def __init__(self, **kw):\n        self.input_tokens_limit = kw.get('input_tokens_limit'); self.output_tokens_limit = kw.get('output_tokens_limit')\n"
                    "async def run(agent, task, session):\n"
                    "    for _ in range(session.max_steps):\n"
                    "        r = await agent.run(task, usage_limits=UsageLimits(request_limit=1))\n"
                    "    return r\n")
        self.fake_library(loop_body=two_runs)
        self.assertFalse(ag.preflight_limits())                 # a run per step would reset the limits every step: refused

    def test_prover_refuses_when_the_loop_does_not_use_the_patched_name(self):
        other = ("class UsageLimits:\n    def __init__(self, **kw):\n        self.input_tokens_limit = None\n"
                 "async def run(agent, task, session):\n"
                 "    return await agent.run(task, usage_limits=make_limits(session))\n"
                 "def make_limits(session):\n    return UsageLimits(request_limit=1)\n")
        self.fake_library(loop_body=other)
        self.assertFalse(ag.preflight_limits())

    def test_prover_refuses_an_agent_without_judge_or_context_options(self):
        self.fake_library(agent_init="foo=1")
        self.assertFalse(ag.preflight_limits())

    def test_the_agent_is_built_without_the_judge_and_without_compaction(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.py"), encoding="utf8").read()
        self.assertIn("judge=False", src)
        self.assertIn("max_context_chars=HUGE_CONTEXT", src)
        self.assertGreaterEqual(ag.HUGE_CONTEXT, 10_000_000)

    def test_an_attempt_with_an_unknown_price_is_booked_at_the_full_reserve(self):
        # one run priced, the fallback run timed out (cost None): the WHOLE attempt is unpriced, never "0.1 + 0"
        async def fake_run_site(site, model, product=None, budget=None):
            if model == ag.MODEL:
                return {"status": "OTHER", "note": "", "secs": 1, "cost": 0.1, "cost_estimated": False, "steps": 1, "usage": None, "flags": []}
            raise asyncio.TimeoutError()
        import asyncio
        saved = ag.run_site
        ag.run_site = fake_run_site
        try:
            r = ag.run_one({"name": "S", "url": "https://s.example"})
        finally:
            ag.run_site = saved
        self.assertIsNone(r["cost"])
        sp = {}
        saved_path = ag.SPEND_PATH
        import tempfile
        ag.SPEND_PATH = os.path.join(tempfile.mkdtemp(), "ops", "s.json")
        try:
            ag.add_spend(sp, "2026-11-25", r)
        finally:
            ag.SPEND_PATH = saved_path
        self.assertEqual(sp["2026-11-25"], 1.0)

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
        self.assertIn('DAILY_USD_CAP: "7.10"', w)
        self.assertIn('ATTEMPT_MAX_USD: "1.00"', w)
        self.assertIn("agent.preflight_limits()", w)


class NoRepeats(unittest.TestCase):
    """24 Nov 2026: a domain that is done or walled is never tried again (any product, feed or backfill)."""
    T0, T1 = "2026-11-24", "2026-11-25"

    def setUp(self):
        import tempfile
        self.kp = os.path.join(tempfile.mkdtemp(), "known.json")
        json.dump({"domains": {}}, open(self.kp, "w"))
        self._old = ag.KNOWN_PATH
        ag.KNOWN_PATH = self.kp
        self.site = {"name": "Dir", "url": "https://www.dir.example/submit"}
        self.prods = ag.load_products()

    def tearDown(self):
        ag.KNOWN_PATH = self._old

    def _listed(self, st):
        ag.record(st, self.site, {"status": "SUBMITTED"}, self.T0)

    def test_every_wall_status_blocks_the_whole_domain(self):
        for wall in ("PAID", "ONE_PER_DOMAIN", "CAPTCHA", "SOCIAL_LOGIN_ONLY", "BADGE_REQUIRED", "PHONE_REQUIRED"):
            st = {}
            self._listed(st)
            todo = ag.product_todo(st, self.site, self.prods, self.T0)
            ag.record_product(st, self.site, todo[0], {"status": wall, "note": ""}, self.T0)
            self.assertEqual(st[ag.WALLS_KEY]["dir.example"]["status"], "WALLED", wall)
            self.assertEqual(ag.product_todo(st, self.site, self.prods, self.T1), [], wall)
            self.assertFalse(ag.eligible(self.site, st, {}, self.T1), wall)
            self.assertEqual(ag.backfill_sites(st, self.prods, self.T1), [], wall)

    def test_wall_note_text_blocks_the_domain(self):
        for note in ("You already have an account", "free launch already used on this account", "account exists"):
            st = {}
            self._listed(st)
            todo = ag.product_todo(st, self.site, self.prods, self.T0)
            ag.record_product(st, self.site, todo[0], {"status": "OTHER", "note": note}, self.T0)
            self.assertIn("dir.example", st[ag.WALLS_KEY], note)
            self.assertEqual(ag.product_todo(st, self.site, self.prods, self.T1), [], note)

    def test_wall_is_found_in_old_state_without_the_walls_key(self):
        st = {"dir.example": {"name": "Dir", "status": "SUBMITTED", "date": self.T0, "tries": 1, "url": self.site["url"],
                              "products": {"x": {"status": "PAID", "date": self.T0, "tries": 1, "note": ""}}}}
        self.assertEqual(ag.product_todo(st, self.site, self.prods, self.T1), [])
        ag.update_walls(st, self.T1)
        self.assertEqual(st[ag.WALLS_KEY]["dir.example"]["reason"], "PAID")

    def test_already_listed_is_done_and_two_stop_the_domain(self):
        st = {}
        self._listed(st)
        todo = ag.product_todo(st, self.site, self.prods, self.T0)
        ag.record_product(st, self.site, todo[0], {"status": "ALREADY_LISTED", "note": ""}, self.T0)
        self.assertNotIn(todo[0]["id"], [p["id"] for p in ag.product_todo(st, self.site, self.prods, self.T1)])   # that product is done
        self.assertTrue(ag.product_todo(st, self.site, self.prods, self.T1))                                       # others still allowed
        ag.record_product(st, self.site, todo[1], {"status": "ALREADY_LISTED", "note": ""}, self.T0)
        self.assertEqual(ag.product_todo(st, self.site, self.prods, self.T1), [])
        self.assertEqual(ag.backfill_sites(st, self.prods, self.T1), [])

    def test_two_soft_failures_per_domain_park_it_14_days_across_products(self):
        st = {}
        self._listed(st)
        todo = ag.product_todo(st, self.site, self.prods, self.T0)
        ag.record_product(st, self.site, todo[0], {"status": "TIMEOUT", "note": ""}, self.T0)
        self.assertTrue(ag.product_todo(st, self.site, self.prods, self.T1))        # one failure: still fine
        ag.record_product(st, self.site, todo[1], {"status": "ERROR", "note": ""}, self.T0)   # a different product, same domain
        self.assertEqual(ag.product_todo(st, self.site, self.prods, "2026-12-07"), [])   # day 13
        self.assertTrue(ag.product_todo(st, self.site, self.prods, "2026-12-08"))        # day 14: parked period over
        self.assertEqual(len(ag.needs_director(st, self.T1)), 1)
        self.assertIn("dir.example", ag.needs_director(st, self.T1)[0])
        self.assertEqual(ag.needs_director(st, "2026-12-08"), [])

    def test_second_park_is_permanent(self):
        st = {}
        for d in ("2026-11-24", "2026-11-24", "2026-12-10", "2026-12-10"):
            ag.note_soft(st, "dir.example", "OTHER", d)
        self.assertEqual(ag.parked_until(st, "dir.example"), "forever")
        self.assertIsNotNone(ag.domain_block(st, "dir.example", "2027-06-01"))

    def test_site_level_soft_failures_count_with_product_ones(self):
        st = {}
        ag.record(st, self.site, {"status": "OTHER", "note": ""}, self.T0)
        ag.record(st, self.site, {"status": "OTHER", "note": ""}, self.T0)
        self.assertFalse(ag.eligible(self.site, st, {}, "2026-11-30"))

    def test_known_sites_list_blocks_feed_and_backfill(self):
        json.dump({"domains": {"dir.example": "listed"}}, open(self.kp, "w"))
        self.assertFalse(ag.eligible(self.site, {}, {}, self.T0))
        st = {"dir.example": {"name": "Dir", "status": "SUBMITTED", "date": self.T0, "tries": 1, "url": self.site["url"]}}
        self.assertEqual(ag.product_todo(st, self.site, self.prods, self.T0), [])
        self.assertEqual(ag.backfill_sites(st, self.prods, self.T0), [])
        self.assertTrue(ag.eligible({"name": "Other", "url": "https://other.example/x"}, {}, {}, self.T0))

    def test_the_shipped_known_sites_file_has_the_agreed_domains(self):
        d = ag.load_known(self._old)
        for dom in ("awesomeindie.com", "apilist.fun", "shipboost.io", "tipseason.com", "uiuxshowcase.com", "tinystartups.com", "findstack.com", "aidude.info",
                    "thesaasdir.com", "saashub.com", "directree.io", "huntscreens.com", "rankmyai.com", "startupdope.com", "ainave.com", "toools.design",
                    "zplatform.ai", "aitoolboard.com"):
            self.assertIn(dom, d)

    def test_state_helper_keys_do_not_break_the_counters(self):
        st = {}
        self._listed(st)
        ag.note_soft(st, "dir.example", "OTHER", self.T0)
        ag.update_walls(st, self.T0)
        self.assertEqual(ag.count_today(st, self.T0), (1, 1))
        self.assertEqual(ag.count_tool_today(st, self.T0), 0)


class FollowProof(unittest.TestCase):
    """24 Nov 2026: follow-proof pre-check. The network is mocked; nothing here touches the internet."""
    T0 = "2026-11-24"

    def setUp(self):
        orig = ag.usability_check
        ag.usability_check = lambda url, get=None: ("UNKNOWN", "not checked in this test")   # these tests are about the follow-proof, not the usability check
        self.addCleanup(lambda: setattr(ag, "usability_check", orig))

    def _site(self, pages):
        """pages: {url: (html, headers)}; installs a fake _http_get, returns the list of fetched urls."""
        fetched = []

        def fake(url):
            fetched.append(url)
            if url not in pages:
                raise OSError("no route")
            return pages[url]
        self._orig = ag._http_get
        ag._http_get = fake
        self.addCleanup(lambda: setattr(ag, "_http_get", self._orig))
        return fetched

    HOME = '<a href="/tool/a">A</a> <a href="/tool/b">B</a> <a href="/submit">s</a> <a href="/blog/x">b</a> <a href="/pricing">p</a>'

    def _verdict(self, listing_html, hdr=None, home=None):
        pages = {"https://www.dir.example/": (home or self.HOME, {}),
                 "https://www.dir.example/tool/a": (listing_html, hdr or {}),
                 "https://www.dir.example/tool/b": (listing_html, hdr or {})}
        self._site(pages)
        return ag.follow_proof("https://www.dir.example/submit")[0]

    def test_followed(self):
        self.assertEqual(self._verdict('<a href="https://other.com/x">visit</a>'), "FOLLOWED")

    def test_nofollow_ugc_sponsored_only(self):
        for rel in ("nofollow", "ugc", "sponsored", "noopener nofollow"):
            self.assertEqual(self._verdict('<a rel="%s" href="https://other.com/x">v</a>' % rel), "NOFOLLOW_ONLY", rel)

    def test_one_followed_among_nofollow_is_followed(self):
        self.assertEqual(self._verdict('<a rel="nofollow" href="https://a.com">a</a><a href="https://b.com">b</a>'), "FOLLOWED")

    def test_noindex_meta_and_header(self):
        self.assertEqual(self._verdict('<meta name="robots" content="noindex, follow"><a href="https://other.com">v</a>'), "NOINDEX")
        self.assertEqual(self._verdict('<meta name="googlebot" content="NOINDEX"><a href="https://other.com">v</a>'), "NOINDEX")
        self.assertEqual(self._verdict('<a href="https://other.com">v</a>', {"x-robots-tag": "noindex"}), "NOINDEX")

    def test_no_listing_links_or_failed_fetch_is_unknown(self):
        self.assertEqual(self._verdict("<a>x</a>", home='<a href="/submit">s</a><a href="/about">a</a>'), "UNKNOWN")
        self._site({})   # homepage unreachable
        self.assertEqual(ag.follow_proof("https://dir.example/submit")[0], "UNKNOWN")
        self._site({"https://dir.example/": (self.HOME, {})})   # listing pages unreachable
        self.assertEqual(ag.follow_proof("https://dir.example/submit")[0], "UNKNOWN")

    def test_platform_and_own_domain_links_ignored(self):
        html = ('<a href="https://twitter.com/x">t</a><a href="https://x.com/a">x</a><a href="https://github.com/a">g</a>'
                '<a href="mailto:a@b.com">m</a><a href="javascript:void(0)">j</a><a href="/tool/c">own</a><a href="https://sub.dir.example/z">own2</a>')
        self.assertEqual(self._verdict(html), "UNKNOWN")   # nothing outbound that counts

    def test_at_most_three_listing_pages_fetched(self):
        home = "".join('<a href="/tool/%d">t</a>' % i for i in range(8))
        pages = {"https://dir.example/": (home, {})}
        for i in range(8):
            pages["https://dir.example/tool/%d" % i] = ('<a rel="nofollow" href="https://o.com">o</a>', {})
        fetched = self._site(pages)
        self.assertEqual(ag.follow_proof("https://dir.example/submit")[0], "NOFOLLOW_ONLY")
        self.assertEqual(len(fetched), 4)   # homepage + 3 listings

    def test_deadline_is_checked_before_every_fetch_and_each_fetch_has_a_10_s_timeout(self):
        import time as _t
        self.assertLessEqual(ag.PROOF_TIMEOUT, 10)
        pages = {"https://dir.example/": (self.HOME, {}), "https://dir.example/tool/a": ('<a href="https://o.com">o</a>', {}), "https://dir.example/tool/b": ("<a>x</a>", {})}
        fetched = self._site(pages)
        self.assertEqual(ag.follow_proof("https://dir.example/submit", deadline=_t.time() - 1), ("UNKNOWN", ag.TIME_LIMIT_EVIDENCE))
        self.assertEqual(fetched, [])                                                      # deadline already passed: not even the homepage is fetched
        inner = ag._http_get

        def slow(url):
            _t.sleep(0.25)                                                                 # the homepage takes longer than the time left
            return inner(url)
        ag._http_get = slow
        self.assertEqual(ag.follow_proof("https://dir.example/submit", deadline=_t.time() + 0.1), ("UNKNOWN", ag.TIME_LIMIT_EVIDENCE))
        self.assertEqual(len(fetched), 1)                                                  # stopped MID-ROW: homepage fetched, no listing page
        ag._http_get = inner
        self.assertEqual(ag.follow_proof("https://dir.example/submit", deadline=_t.time() + 60)[0], "FOLLOWED")   # a far deadline changes nothing

    def test_cache_reuse_and_expiry(self):
        st = {ag.PROOF_KEY: {"dir.example": {"verdict": "NOINDEX", "date": "2026-10-30", "evidence": "x"}}}
        self.assertEqual(ag.cached_proof(st, "dir.example", self.T0)["verdict"], "NOINDEX")   # 25 days old
        self.assertEqual(ag.domain_block(st, "dir.example", self.T0), "NOINDEX")
        self.assertIsNone(ag.cached_proof(st, "dir.example", "2026-11-30"))                # 31 days old
        self.assertIsNone(ag.domain_block(st, "dir.example", "2026-11-30"))
        st[ag.PROOF_KEY]["dir.example"] = {"verdict": "NOFOLLOW_ONLY", "date": self.T0, "evidence": "x"}
        self.assertEqual(ag.domain_block(st, "dir.example", self.T0), "NO_FOLLOW_PROOF")
        st[ag.PROOF_KEY]["dir.example"]["verdict"] = "FOLLOWED"
        self.assertIsNone(ag.domain_block(st, "dir.example", self.T0))

    def test_backfill_skips_blocked_domain_from_cache_only(self):
        site = {"name": "Dir", "url": "https://dir.example/submit"}
        st = {}
        ag.record(st, site, {"status": "SUBMITTED"}, self.T0)
        prods = ag.load_products()
        self.assertTrue(ag.backfill_sites(st, prods, self.T0))
        st[ag.PROOF_KEY] = {"dir.example": {"verdict": "NOFOLLOW_ONLY", "date": self.T0, "evidence": "x"}}
        self.assertEqual(ag.backfill_sites(st, prods, self.T0), [])
        self.assertEqual(ag.product_todo(st, site, prods, self.T0), [])

    def _feed(self, sites, verdicts, state=None, vstate=None, limit=None):
        """Runs build_feed with the data files and follow_proof replaced; returns (names, stats, state, calls)."""
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "data"))
        json.dump({"sites": []}, open(os.path.join(d, "data", "directories_candidates.json"), "w"))
        json.dump([{"name": n, "submit_url": u} for n, u in sites], open(os.path.join(d, "data", "directories.json"), "w"))
        kp = os.path.join(d, "known.json")
        json.dump({"domains": {}}, open(kp, "w"))
        calls = []
        saved = (ag.ROOT, ag.KNOWN_PATH, ag.follow_proof, ag.PROOF_CHECKS_PER_RUN)

        def fake(url):
            calls.append(url)
            return verdicts[ag.dom_of(url)], "ev"
        ag.ROOT, ag.KNOWN_PATH, ag.follow_proof = d, kp, fake
        if limit is not None:
            ag.PROOF_CHECKS_PER_RUN = limit
        self.addCleanup(lambda: (setattr(ag, "ROOT", saved[0]), setattr(ag, "KNOWN_PATH", saved[1]),
                                 setattr(ag, "follow_proof", saved[2]), setattr(ag, "PROOF_CHECKS_PER_RUN", saved[3])))
        st = state if state is not None else {}
        stats = {}
        feed = ag.build_feed(st, {}, self.T0, vstate, stats)
        return [s["name"] for s in feed], stats, st, calls

    def test_feed_reads_both_list_builder_files(self):
        """24 Nov 2026: Sonnet's and DeepSeek's candidate files are BOTH read for TODO rows."""
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "data"))
        row = lambda n, u, fb: {"name": n, "submit_url": u, "usable": True, "free_check": "free_likely", "found_by": fb}   # noqa: E731
        json.dump({"sites": [row("S1", "https://s1.example/submit", "sonnet")]}, open(os.path.join(d, "data", "directories_candidates.json"), "w"))
        json.dump({"sites": [row("D1", "https://d1.example/submit", "deepseek")]}, open(os.path.join(d, "data", "directories_candidates_deepseek.json"), "w"))
        json.dump([], open(os.path.join(d, "data", "directories.json"), "w"))
        kp = os.path.join(d, "known.json")
        json.dump({"domains": {}}, open(kp, "w"))
        saved = (ag.ROOT, ag.KNOWN_PATH, ag.follow_proof)
        ag.ROOT, ag.KNOWN_PATH, ag.follow_proof = d, kp, (lambda url: ("FOLLOWED", "ev"))
        self.addCleanup(lambda: (setattr(ag, "ROOT", saved[0]), setattr(ag, "KNOWN_PATH", saved[1]), setattr(ag, "follow_proof", saved[2])))
        feed = ag.build_feed({}, {}, self.T0, None, {})
        self.assertEqual(sorted(x["name"] for x in feed), ["D1", "S1"])
        self.assertEqual(len(ag.candidate_sites(os.path.join(d, "data"))), 2)
        os.remove(os.path.join(d, "data", "directories_candidates_deepseek.json"))             # a missing DeepSeek file is simply empty
        self.assertEqual([x["name"] for x in ag.build_feed({}, {}, self.T0, None, {})], ["S1"])

    SITES = [("A", "https://a.example/submit"), ("B", "https://b.example/submit"), ("C", "https://c.example/submit"),
             ("D", "https://d.example/submit"), ("E", "https://e.example/submit")]

    def test_feed_order_blocks_and_stats(self):
        v = {"a.example": "UNKNOWN", "b.example": "FOLLOWED", "c.example": "NOFOLLOW_ONLY", "d.example": "NOINDEX", "e.example": "FOLLOWED"}
        names, stats, st, calls = self._feed(self.SITES, v)
        self.assertEqual(names, ["B", "E", "A"])
        self.assertEqual(stats, {"checked": 5, "FOLLOWED": 2, "NOFOLLOW_ONLY": 1, "NOINDEX": 1, "UNKNOWN": 1, "skipped_nofollow": 1})
        self.assertEqual(st[ag.PROOF_KEY]["c.example"]["verdict"], "NOFOLLOW_ONLY")
        # second run the same day: all from the cache, no new checks
        names2, stats2, _, calls2 = self._feed(self.SITES, v, state=st)
        self.assertEqual((names2, calls2, stats2["checked"]), (["B", "E", "A"], [], 0))

    def test_boosted_followed_first_and_boosted_nofollow_skipped(self):
        st = {"c.example": {"name": "C", "status": "OTHER", "date": "2026-11-20", "tries": 1}, "e.example": {"name": "E", "status": "OTHER", "date": "2026-11-20", "tries": 1}}
        vstate = {"retry": {"c.example": "2026-11-23", "e.example": "2026-11-23"}}
        v = {"a.example": "FOLLOWED", "b.example": "FOLLOWED", "c.example": "NOFOLLOW_ONLY", "d.example": "UNKNOWN", "e.example": "FOLLOWED"}
        names, _, _, _ = self._feed(self.SITES, v, state=st, vstate=vstate)
        self.assertEqual(names, ["E", "A", "B", "D"])   # E boosted + followed first; C boosted but nofollow-only is skipped

    def test_per_run_check_limit(self):
        v = {k: "FOLLOWED" for k in ("a.example", "b.example", "c.example", "d.example", "e.example")}
        names, stats, st, calls = self._feed(self.SITES, v, limit=2)
        self.assertEqual(stats["checked"], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(names, ["A", "B"])   # the rest wait for the next run
        names2, stats2, _, _ = self._feed(self.SITES, v, state=st, limit=2)
        self.assertEqual(names2, ["A", "B", "C", "D"])
        self.assertEqual(stats2["checked"], 2)

    def test_network_error_is_unknown_and_never_crashes(self):
        def boom(url):
            raise TimeoutError("slow")
        self._orig = ag._http_get
        ag._http_get = boom
        self.addCleanup(lambda: setattr(ag, "_http_get", self._orig))
        v, ev = ag.follow_proof("https://dir.example/submit")
        self.assertEqual(v, "UNKNOWN")
        self.assertIn("TimeoutError", ev)
        self.assertEqual(ag.follow_proof("")[0], "UNKNOWN")


class Usability(unittest.TestCase):
    """25 Nov 2026: usability pre-check (plain HTTP, no AI) + reasons for runs that end without an answer + mail matching. No network."""
    T0 = "2026-11-25"

    def chk(self, pages, url="https://dir.example/submit"):
        def get(u):
            return pages.get(u, (0, u, {}, "DNS"))
        return ag.usability_check(url, get)

    def test_dead_dns_404_and_redirect_to_another_site(self):
        self.assertEqual(self.chk({})[0], "DEAD")
        self.assertEqual(self.chk({"https://dir.example/submit": (404, "https://dir.example/submit", {}, "nope")})[0], "DEAD")
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://other.example/", {}, "<form><input></form>")})[0], "DEAD")

    def test_captcha_markers_and_cloudflare_challenge(self):
        page = "<form><input name=a></form><div class='g-recaptcha'></div>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, page)})[0], "CAPTCHA")
        self.assertEqual(self.chk({"https://dir.example/submit": (403, "https://dir.example/submit", {"cf-mitigated": "challenge"}, "")})[0], "CAPTCHA")
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, "<div class=cf-turnstile></div><form><input></form>")})[0], "CAPTCHA")

    def test_form_is_ok_and_embedded_forms_count(self):
        ok = "<form action=/s><input name=url><textarea name=d></textarea></form>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, ok)})[0], "OK")
        emb = "<h1>Submit</h1><iframe src='https://tally.so/embed/abc'></iframe>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, emb)})[0], "OK")

    def test_no_form_mailto_only_and_follow_one_submit_link(self):
        mail = "<h1>Send us your tool</h1><a href='mailto:hi@dir.example'>mail</a>" + "x " * 400
        v, ev = self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, mail)})
        self.assertEqual(v, "NO_FORM")
        self.assertIn("e-mail only", ev)
        home = "<a href='/add-your-tool'>add</a>" + "x " * 400
        tgt = "<form><input name=u></form>"
        pages = {"https://dir.example/": (200, "https://dir.example/", {}, home), "https://dir.example/add-your-tool": (200, "https://dir.example/add-your-tool", {}, tgt)}
        self.assertEqual(self.chk(pages, "https://dir.example/")[0], "OK")

    def test_paid_only_and_google_only_login(self):
        paid = "<p>Paid listing only. Listing fee $49.</p><form><input></form>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, paid)})[0], "PAID")
        priced = "<h1>Get featured</h1><p>Submit your tool from $99</p>" + "x " * 400
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, priced)})[0], "PAID")
        free_and_paid = "<h1>Submit</h1><p>Free listing, or featured $99</p><form><input></form>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, free_and_paid)})[0], "OK")
        goog = "<h1>Log in</h1><button>Continue with Google</button>" + "x " * 400
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, goog)})[0], "SOCIAL_LOGIN_ONLY")
        both = "<button>Continue with Google</button><form><input type=email><input type=password></form>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, both)})[0], "OK")

    def test_js_app_and_failed_fetch_stay_unknown_so_the_agent_still_tries(self):
        js = "<div id='root'></div><script>app()</script>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, js)})[0], "UNKNOWN")
        self.assertEqual(self.chk({"https://dir.example/submit": (500, "https://dir.example/submit", {}, "")})[0], "UNKNOWN")
        self.assertEqual(self.chk({"https://dir.example/submit": (0, "https://dir.example/submit", {}, "timeout")})[0], "UNKNOWN")

    def _feed(self, verdicts):
        sites = [{"name": "A", "submit_url": "https://a.example/submit", "usable": True, "free_check": "free_likely"},
                 {"name": "B", "submit_url": "https://b.example/submit", "usable": True, "free_check": "free_likely"}]
        o = (ag.candidate_sites, ag.load_json, ag.follow_proof, ag.usability_check)
        ag.candidate_sites = lambda d: sites
        ag.load_json = lambda p, d: [] if p.endswith("directories.json") else d
        ag.follow_proof = lambda u, deadline=None: ("FOLLOWED", "x")
        calls = []

        def uc(url, get=None):
            calls.append(url)
            return verdicts[url]
        ag.usability_check = uc

        def restore():
            ag.candidate_sites, ag.load_json, ag.follow_proof, ag.usability_check = o
        self.addCleanup(restore)
        state, stats = {}, {}
        out = ag.build_feed(state, {}, self.T0, {}, stats)
        return [s["name"] for s in out], state, stats, calls

    def test_feed_drops_sure_losers_caches_the_verdict_and_never_rechecks_inside_the_window(self):
        names, state, stats, calls = self._feed({"https://a.example/submit": ("CAPTCHA", "bot-check marker"), "https://b.example/submit": ("OK", "form")})
        self.assertEqual(names, ["B"])
        self.assertEqual(stats["use_CAPTCHA"], 1)
        self.assertEqual(state[ag.USE_KEY]["a.example"]["verdict"], "CAPTCHA")
        names2 = [s["name"] for s in ag.build_feed(state, {}, "2026-11-26", {}, {})]
        self.assertEqual(names2, ["B"])
        self.assertEqual(len(calls), 2)
        self.assertIsNone(ag.cached_usable(state, "a.example", "2026-12-10"))

    def test_unknown_goes_to_the_agent(self):
        names, _, _, _ = self._feed({"https://a.example/submit": ("UNKNOWN", "JS app"), "https://b.example/submit": ("DEAD", "x")})
        self.assertEqual(names, ["A"])

    def test_blocked_verdicts_cover_the_25_nov_failure_mix(self):
        self.assertTrue({"NO_FORM", "CAPTCHA", "DEAD", "PAID", "SOCIAL_LOGIN_ONLY"} <= ag.USE_BLOCK)

    class _Res:
        def __init__(self, steps=3, errors=()):
            self._s, self._e = steps, list(errors)

        def number_of_steps(self):
            return self._s

        def errors(self):
            return self._e

    def test_no_answer_always_has_a_reason_code(self):
        r = ag.no_answer_reason(self._Res(40), {"input_tokens": 100, "output_tokens": 5}, (50000, 9000), 40)
        self.assertTrue(r.startswith("NO_ANSWER_STEP_LIMIT"), r)
        r = ag.no_answer_reason(self._Res(7), {"input_tokens": 49000, "output_tokens": 5}, (50000, 9000), 40)
        self.assertTrue(r.startswith("NO_ANSWER_TOKEN_LIMIT"), r)
        r = ag.no_answer_reason(self._Res(5, [None, "TimeoutError: page https://x.example failed"]), {"input_tokens": 10, "output_tokens": 1}, (50000, 9000), 40)
        self.assertTrue(r.startswith("NO_ANSWER_BROWSER_ERROR"), r)
        self.assertNotIn("https://", r)
        r = ag.no_answer_reason(self._Res(5), {}, None, 40)
        self.assertTrue(r.startswith("NO_ANSWER_STOPPED"), r)
        self.assertLessEqual(len(r), 160)

    def test_confirm_mail_matches_the_site_or_a_mailer_sending_for_it(self):
        self.assertTrue(ag.mail_is_from_site("mail.dir.example", "hello", "dir.example"))
        self.assertTrue(ag.mail_is_from_site("sendgrid.net", "Click https://dir.example/verify?token=abc", "dir.example"))
        self.assertFalse(ag.mail_is_from_site("sendgrid.net", "Click https://other.example/verify?token=abc", "dir.example"))
        self.assertGreaterEqual(ag.MAIL_WAIT, 240)

    def test_task_says_prefer_email_signup_and_finish_in_the_same_session(self):
        t = ag.build_task({"name": "Dir", "url": "https://dir.example/submit"})
        self.assertIn("always use the e-mail option", t)
        self.assertIn("THIS browser session", t)


class ClickListAndResume(unittest.TestCase):
    def test_click_list_keeps_old_rows_lists_followed_first_and_skips_nofollow(self):
        import tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "imran_click_list.md")
        open(path, "w", encoding="utf8").write("# Directories that need Imran\n\n| old row |\n")
        st = {"a.example": {"name": "A", "status": "CAPTCHA", "note": "turnstile", "url": "https://a.example/submit"},
              "b.example": {"name": "B", "status": "PHONE_REQUIRED", "note": "phone", "url": "https://b.example/s"},
              "c.example": {"name": "C", "status": "CAPTCHA", "note": "x"}, "d.example": {"name": "D", "status": "SUBMITTED"},
              ag.PROOF_KEY: {"a.example": {"verdict": "FOLLOWED"}, "b.example": {"verdict": "UNKNOWN"}, "c.example": {"verdict": "NOFOLLOW_ONLY"}}}
        ag.write_click_list(st, "2026-11-25", path)
        ag.write_click_list(st, "2026-11-25", path)           # twice: the section is replaced, not doubled
        t = open(path, encoding="utf8").read()
        self.assertIn("| old row |", t)
        self.assertEqual(t.count(ag.CLICK_START), 1)
        self.assertLess(t.index("a.example"), t.index("b.example"))   # followed first
        self.assertNotIn("c.example", t)                              # nofollow-only: not worth a person
        self.assertNotIn("d.example", t)                              # submitted: nothing to do
        self.assertIn("exact step", t)

    def test_resume_sites_go_first_and_unknown_owner_sites_are_never_resumed(self):
        a = {"name": "A", "url": "https://a.example/submit"}
        b = {"name": "B", "url": "https://b.example/submit"}
        st = {"a.example": {"name": "A", "status": "EMAIL_CODE_NOT_RECEIVED", "date": "2026-11-20"}}
        self.assertTrue(ag.is_resume(a, st, {}))
        self.assertFalse(ag.is_resume(b, st, {}))
        self.assertTrue(ag.is_resume(b, {}, {"B": {"confirm_pending": True}}))
        d = json.load(open(ag.RESUME_FILE, encoding="utf8"))
        self.assertEqual(d["robot_signed_up"], [])
        self.assertIn("shipboost.io", d["sign_up_method_unknown_do_not_touch"])


class QueueOrder(unittest.TestCase):
    def test_speed_words(self):
        self.assertEqual(ag.submission_speed("Your tool goes live instantly after you submit"), "instant")
        self.assertEqual(ag.submission_speed("We review every tool within 3 days; goes live after approval"), "review")
        self.assertEqual(ag.submission_speed("Submit your tool"), "unknown")

    def test_ok_pages_carry_the_speed_and_the_rank_orders_the_queue(self):
        page = "<h1>Submit</h1><p>Published immediately.</p><form><input></form>"
        v, ev = ag.classify_page(200, "https://d.example/s", {}, page, "d.example")
        self.assertEqual((v, "speed=instant" in ev), ("OK", True))
        st = {ag.USE_KEY: {"i.example": {"evidence": "form on the page; speed=instant"}, "r.example": {"evidence": "form on the page; speed=review"}}}
        self.assertEqual(ag.speed_of(st, "i.example"), "instant")
        ranks = [ag.queue_rank("FOLLOWED", "instant"), ag.queue_rank("FOLLOWED", "unknown"), ag.queue_rank("FOLLOWED", "review"), ag.queue_rank("UNKNOWN", "instant")]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(ranks, [0, 1, 2, 3])

    def test_summary_breakdown_and_nofollow_later_file(self):
        import tempfile
        st = {ag.PROOF_KEY: {"a.example": {"verdict": "FOLLOWED"}, "b.example": {"verdict": "FOLLOWED"}, "n.example": {"verdict": "NOFOLLOW_ONLY", "date": "2026-11-25", "evidence": "x"}},
              ag.USE_KEY: {"a.example": {"evidence": "form; speed=instant"}, "b.example": {"evidence": "form; speed=review"}}}
        rows = [({"name": "A", "url": "https://a.example/s"}, {"status": "SUBMITTED"}), ({"name": "B", "url": "https://b.example/s"}, {"status": "SUBMITTED"}),
                ({"name": "C", "url": "https://c.example/s"}, {"status": "CAPTCHA"})]
        line = ag.run_breakdown(rows, st, {"skipped_nofollow": 4})
        self.assertEqual(line, "tried 3 | live followed instant 1 | submitted followed (queue/unknown speed) 1 | skipped nofollow 4 | needs person 1")
        path = os.path.join(tempfile.mkdtemp(), "nofollow_later.md")
        ag.write_nofollow_later(st, "2026-11-25", path)
        t = open(path, encoding="utf8").read()
        self.assertIn("n.example", t)
        self.assertNotIn("a.example", t)


if __name__ == "__main__":
    unittest.main()
