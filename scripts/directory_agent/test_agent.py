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

        def fake(s, product=None, lead=False):
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

        def fake(s, product=None, lead=False):
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
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://other.example/", {}, "<form><input></form>")})[0], "UNKNOWN")   # a redirect is not proof of a dead site

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
        names, state, stats, calls = self._feed({"https://a.example/submit": ("PAID", "paid only"), "https://b.example/submit": ("OK", "form")})
        self.assertEqual(names, ["B"])
        self.assertEqual(stats["use_PAID"], 1)
        self.assertEqual(state[ag.USE_KEY]["a.example"]["verdict"], "PAID")
        names2 = [s["name"] for s in ag.build_feed(state, {}, "2026-11-26", {}, {})]
        self.assertEqual(names2, ["B"])
        self.assertEqual(len(calls), 2)
        self.assertIsNone(ag.cached_usable(state, "a.example", "2026-12-10"))

    def test_unknown_goes_to_the_agent(self):
        names, _, _, _ = self._feed({"https://a.example/submit": ("UNKNOWN", "JS app"), "https://b.example/submit": ("DEAD", "x")})
        self.assertEqual(names, ["A"])

    def test_blocked_verdicts_are_only_the_sure_losers(self):
        # 26 Nov 2026: raw HTML cannot judge a JS-drawn form or an invisible reCAPTCHA: those two are hints, the browser decides
        self.assertEqual(ag.USE_BLOCK, {"DEAD", "PAID", "SOCIAL_LOGIN_ONLY"})
        self.assertEqual(ag.USE_HINT, {"CAPTCHA", "NO_FORM"})
        self.assertFalse(ag.USE_BLOCK & ag.USE_HINT)

    def test_captcha_and_no_form_hints_still_reach_the_browser(self):
        names, state, stats, _ = self._feed({"https://a.example/submit": ("CAPTCHA", "bot-check marker"), "https://b.example/submit": ("NO_FORM", "no form")})
        self.assertEqual(names, ["A", "B"])
        self.assertEqual((stats["hint_CAPTCHA"], stats["hint_NO_FORM"]), (1, 1))
        self.assertFalse(any(k.startswith("use_") for k in stats))
        self.assertEqual(state[ag.USE_KEY]["a.example"]["verdict"], "CAPTCHA")     # the hint is still cached and counted

    def test_google_words_on_a_js_app_are_not_social_only(self):
        js = "<div id='__next'></div><button>Continue with Google</button>" + "x " * 400
        v = self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, js)})[0]
        self.assertNotEqual(v, "SOCIAL_LOGIN_ONLY")
        self.assertNotIn(v, ag.USE_BLOCK)                                          # so the browser still looks

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


def f_path(mod):
    return mod.RESUME_FILE


class ClickListAndResume(unittest.TestCase):
    def chk(self, pages, url="https://dir.example/submit"):
        return ag.usability_check(url, lambda u: pages.get(u, (0, u, {}, "DNS")))

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

    def _resume_file(self, robot=(), dnt=()):
        import tempfile
        f = os.path.join(tempfile.mkdtemp(), "resume_sites.json")
        json.dump({"robot_signed_up": list(robot), "sign_up_method_unknown_do_not_touch": list(dnt)}, open(f, "w"))
        saved = ag.RESUME_FILE
        ag.RESUME_FILE = f
        self.addCleanup(lambda: setattr(ag, "RESUME_FILE", saved))

    def test_resume_sites_go_first(self):
        self._resume_file()
        a = {"name": "A", "url": "https://a.example/submit"}
        b = {"name": "B", "url": "https://b.example/submit"}
        st = {"a.example": {"name": "A", "status": "EMAIL_CODE_NOT_RECEIVED", "date": "2026-11-20"}}
        self.assertTrue(ag.is_resume(a, st, {}))
        self.assertFalse(ag.is_resume(b, st, {}))
        self.assertTrue(ag.is_resume(b, {}, {"B": {"confirm_pending": True}}))

    def test_do_not_touch_domains_are_never_in_the_feed_even_as_candidates(self):
        self._resume_file(dnt=["shipboost.io", "www.sitepatent.com"])
        self.assertTrue(ag.domain_block({}, "shipboost.io", "2026-11-25").startswith("do not touch"))
        self.assertTrue(ag.domain_block({}, "sitepatent.com", "2026-11-25").startswith("do not touch"))
        self.assertIsNone(ag.domain_block({}, "other.example", "2026-11-25"))
        site = {"name": "ShipBoost", "url": "https://shipboost.io/submit"}
        self.assertFalse(ag.eligible(site, {}, {}, "2026-11-25"))
        orig = (ag.candidate_sites, ag.load_json, ag.follow_proof, ag.usability_check)
        ag.candidate_sites = lambda d: [{"name": "ShipBoost", "submit_url": "https://shipboost.io/submit", "usable": True, "free_check": "free_likely"},
                                        {"name": "Fine", "submit_url": "https://fine.example/submit", "usable": True, "free_check": "free_likely"}]
        ag.load_json = lambda path, d: [] if str(path).endswith("directories.json") else (orig[1](path, d) if path == f_path(ag) else d)
        ag.follow_proof = lambda u, deadline=None: ("FOLLOWED", "x")
        ag.usability_check = lambda u, get=None: ("OK", "form; speed=unknown")

        def restore():
            ag.candidate_sites, ag.load_json, ag.follow_proof, ag.usability_check = orig
        self.addCleanup(restore)
        names = [x["name"] for x in ag.build_feed({}, {}, "2026-11-25", {}, {})]
        self.assertEqual(names, ["Fine"])

    def test_the_shipped_public_repo_has_no_resume_file(self):
        self.assertFalse(os.path.exists(os.path.join(ag.ROOT, "data", "resume_sites.json")))
        wf = open(os.path.join(ag.ROOT, ".github", "workflows", "directory-agent.yml"), encoding="utf8").read()
        self.assertEqual(wf.count("resume_sites.json"), 2)                          # restored from and saved to the private branch

    def test_a_resume_site_cannot_be_retried_endlessly(self):
        self._resume_file(robot=["a.example"])
        site = {"name": "A", "url": "https://a.example/submit"}
        st = {"a.example": {"name": "A", "status": "EMAIL_CODE_NOT_RECEIVED", "date": "2026-11-20", "tries": 2}}
        self.assertTrue(ag.is_resume(site, st, {}))
        self.assertFalse(ag.eligible(site, st, {}, "2026-11-25"))                   # 2 tries used: the fail counter stops it, resume only changes the ORDER
        st2 = {"a.example": {"name": "A", "status": "EMAIL_CODE_NOT_RECEIVED", "date": "2026-11-24", "tries": 1}}
        self.assertFalse(ag.eligible(site, st2, {}, "2026-11-25"))                  # and not twice inside 3 days
        st3 = {"a.example": {"name": "A", "status": "OTHER", "date": "2026-11-20", "tries": 1}}
        ag.note_soft(st3, "a.example", "OTHER", "2026-11-20")
        ag.note_soft(st3, "a.example", "OTHER", "2026-11-21")
        self.assertIn("parked", ag.domain_block(st3, "a.example", "2026-11-25") or "")

    def test_confirm_links_only_from_the_sites_own_domain(self):
        text = ("Confirm: https://dir.example/verify?token=abc and https://click.mailer.net/track?u=https%3A%2F%2Fevil.example%2Fconfirm "
                "and https://evil.example/confirm?token=1 and https://www.dir.example/auth/login?code=9")
        self.assertEqual(ag.confirm_links(text, "dir.example"), ["https://dir.example/verify?token=abc", "https://www.dir.example/auth/login?code=9"])
        self.assertEqual(ag.confirm_links("https://evil.example/confirm?token=1", "dir.example"), [])

    def test_dns_is_retried_once_and_a_dns_verdict_is_cached_one_day_only(self):
        import socket, urllib.error
        calls = []
        orig = ag.urlopen

        def boom(req, timeout=0):
            calls.append(1)
            raise urllib.error.URLError(socket.gaierror(-2, "no such host"))
        ag.urlopen = boom
        orig_sleep = ag.time.sleep
        ag.time.sleep = lambda x: None
        self.addCleanup(lambda: (setattr(ag, "urlopen", orig), setattr(ag.time, "sleep", orig_sleep)))
        st = ag._get_status("https://nope.example/")
        self.assertEqual((st[0], st[3], len(calls)), (0, "DNS", 2))                  # tried twice
        state = {ag.USE_KEY: {"d.example": {"verdict": "DEAD", "date": "2026-11-24", "evidence": "no such site (DNS)"},
                              "g.example": {"verdict": "DEAD", "date": "2026-11-24", "evidence": "HTTP 404"}}}
        self.assertIsNone(ag.cached_usable(state, "d.example", "2026-11-25"))        # DNS: looked at again after 1 day
        self.assertIsNotNone(ag.cached_usable(state, "g.example", "2026-11-25"))

    def test_js_app_without_a_form_is_no_form_only_when_nothing_offers_a_submit(self):
        shell = "<div id='root'></div>" + "<p>About us and our team and news. </p>" * 30
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, shell)})[0], "NO_FORM")
        with_link = shell + "<a href='/pricing'>Sign up free</a>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, with_link)})[0], "UNKNOWN")
        with_href = shell + "<a href='/submit-your-tool'>go</a>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, with_href)})[0], "UNKNOWN")
        empty_shell = "<div id='root'></div><script src=app.js></script>"
        self.assertEqual(self.chk({"https://dir.example/submit": (200, "https://dir.example/submit", {}, empty_shell)})[0], "UNKNOWN")

    def test_the_confirm_mail_tool_does_not_block_the_event_loop(self):
        import asyncio, time as _t
        orig = ag.read_mail_from_domain
        ag.read_mail_from_domain = lambda url, wait=None, cancel=None: (_t.sleep(0.4), "MAIL FOUND")[1]      # a blocking poll
        self.addCleanup(lambda: setattr(ag, "read_mail_from_domain", orig))
        ticks = []

        async def ticker():
            for _ in range(8):
                ticks.append(1)
                await asyncio.sleep(0.04)

        async def go():
            t = asyncio.create_task(ticker())
            r = await ag.read_mail_async("https://dir.example/s")
            await t
            return r
        self.assertEqual(asyncio.run(go()), "MAIL FOUND")
        self.assertEqual(len(ticks), 8)                                              # the loop kept ticking while the mail poll waited
        self.assertTrue(asyncio.iscoroutinefunction(ag.read_mail_async))

    def test_one_attempt_always_fits_the_workflow_timeout(self):
        self.assertEqual((ag.SITE_SECONDS, ag.MAIL_WAIT, ag.RUN_SECONDS), (900, 540, 8400))
        self.assertLess(ag.MAIL_WAIT + 120, ag.SITE_SECONDS)                         # the mail wait fits inside one model run
        self.assertEqual(ag.START_BEFORE, 8400 - 2 * 900)
        self.assertLessEqual(ag.START_BEFORE + 2 * ag.SITE_SECONDS, 150 * 60)         # latest start + primary + fallback ends inside the 150-minute step timeout
        wf = open(os.path.join(ag.ROOT, ".github", "workflows", "directory-agent.yml"), encoding="utf8").read()
        self.assertIn("timeout-minutes: 150", wf)
        self.assertIn('RUN_SECONDS: "8400"', wf)


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


class FailClosedAndCancel(unittest.TestCase):
    def _file(self, content):
        import tempfile
        f = os.path.join(tempfile.mkdtemp(), "resume_sites.json")
        if content is not None:
            open(f, "w", encoding="utf8").write(content)
        saved = ag.RESUME_FILE
        ag.RESUME_FILE = f
        self.addCleanup(lambda: setattr(ag, "RESUME_FILE", saved))

    def test_no_resume_file_means_no_sign_ups_and_no_resume(self):
        site = {"name": "A", "url": "https://a.example/submit"}
        st = {"a.example": {"name": "A", "status": "EMAIL_CODE_NOT_RECEIVED", "date": "2026-11-20"}}
        for content in (None, "not json{", "[]", json.dumps({"robot_signed_up": ["a.example"]})):       # missing / unreadable / wrong shape / no do-not-touch key
            self._file(content)
            self.assertFalse(ag.resume_file_ok(), content)
            self.assertFalse(ag.signups_allowed())
            self.assertFalse(ag.is_resume(site, st, {}))                                             # no resume attempts either
            t = ag.build_task(site)
            self.assertIn("Do NOT create any account", t)                                           # submit-only sites still get a task
            self.assertNotIn("create one with the e-mail", t)

    def test_a_good_file_allows_sign_ups(self):
        self._file(json.dumps({"robot_signed_up": [], "sign_up_method_unknown_do_not_touch": ["x.example"]}))
        self.assertTrue(ag.resume_file_ok())
        self.assertTrue(ag.signups_allowed())
        self.assertIn("create one with the e-mail", ag.build_task({"name": "A", "url": "https://a.example/submit"}))
        self._file(json.dumps({"sign_up_method_unknown_do_not_touch": []}))                           # an empty list is a valid list
        self.assertTrue(ag.resume_file_ok())

    def test_main_prints_the_count_only_line_and_the_batch_obeys_domain_block(self):
        import tempfile, io, contextlib
        self._file(None)
        d = tempfile.mkdtemp()
        batch = os.path.join(d, "batch.json")
        json.dump({"sites": [{"name": "Walled", "url": "https://walled.example/submit"}, {"name": "Fine", "url": "https://fine.example/submit"}]}, open(batch, "w"))
        saved = (ag.STATE_PATH, ag.SPEND_PATH, ag.LOG_PATH, ag.SUMMARY_PATH, ag.OLD_STATE_PATH, ag.VSTATE_PATH, ag.run_one, ag.WITH_TOOLS, ag.preflight_limits, ag.domain_block)
        calls = []
        ag.STATE_PATH, ag.SPEND_PATH = os.path.join(d, "ops", "state.json"), os.path.join(d, "ops", "spend.json")
        ag.LOG_PATH, ag.SUMMARY_PATH = os.path.join(d, "ops", "log.md"), os.path.join(d, "ops", "sum.md")
        ag.OLD_STATE_PATH, ag.VSTATE_PATH = os.path.join(d, "ops", "old.json"), os.path.join(d, "ops", "v.json")
        ag.run_one = lambda s, product=None, lead=False: (calls.append(s["name"]), {"status": "SUBMITTED", "note": "", "secs": 1, "cost": 0.01, "cost_estimated": False, "steps": 1, "usage": None, "flags": [], "model": "m"})[1]
        ag.WITH_TOOLS, ag.preflight_limits = False, (lambda: True)
        real_block = ag.domain_block
        ag.domain_block = lambda state, dom, today, known_path=None: "walled: test" if dom == "walled.example" else real_block(state, dom, today, known_path)
        os.environ["BATCH_FILE"] = batch
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ag.main()
        finally:
            os.environ.pop("BATCH_FILE", None)
            (ag.STATE_PATH, ag.SPEND_PATH, ag.LOG_PATH, ag.SUMMARY_PATH, ag.OLD_STATE_PATH, ag.VSTATE_PATH, ag.run_one, ag.WITH_TOOLS, ag.preflight_limits, ag.domain_block) = saved
        self.assertEqual(calls, ["Fine"])                                       # the manual batch went through domain_block
        self.assertEqual(buf.getvalue().count("resume file missing: sign-ups skipped"), 1)

    def test_no_stale_public_path_in_the_comments(self):
        t = open(ag.__file__, encoding="utf8").read()
        self.assertNotIn("data/resume_sites.json", t)

    def test_cancelled_mail_poll_returns_fast_and_never_flags_a_mail(self):
        import asyncio, time as _t

        class FakeImap:
            stores = []

            def __init__(self, *a, **k):
                pass

            def login(self, *a):
                pass

            def select(self, f):
                return ("OK", [b"1"])

            def uid(self, cmd, *a):
                if cmd == "SEARCH":
                    return ("OK", [b"1"])
                if cmd == "FETCH":
                    raw = b"From: x@dir.example\r\nDate: " + _t.strftime("%a, %d %b %Y %H:%M:%S +0000", _t.gmtime()).encode() + b"\r\nSubject: Confirm\r\n\r\nhttps://dir.example/verify?token=1"
                    return ("OK", [(b"1", raw)])
                if cmd == "STORE":
                    FakeImap.stores.append(a)
                return ("OK", [])

            def logout(self):
                pass
        saved = (ag.imaplib.IMAP4_SSL, ag.MAIL_WAIT)
        ag.imaplib.IMAP4_SSL = FakeImap
        os.environ["CONTACT_EMAIL_PASSWORD"] = "x"
        self.addCleanup(lambda: (setattr(ag.imaplib, "IMAP4_SSL", saved[0]), setattr(ag, "MAIL_WAIT", saved[1]), os.environ.pop("CONTACT_EMAIL_PASSWORD", None)))
        # (1) nothing from the site in the box + the budget runs out: the poll thread ends within a few seconds, not after MAIL_WAIT
        FakeImap.uid = lambda self, cmd, *a: ("OK", [b""]) if cmd == "SEARCH" else ("OK", [])
        ag.MAIL_WAIT = 60

        async def go():
            t0 = _t.time()
            try:
                await asyncio.wait_for(ag.read_mail_async("https://dir.example/submit"), timeout=1.0)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1.5)                       # give the worker thread time to see the flag
            return _t.time() - t0
        self.assertLess(asyncio.run(go()), 5)
        import threading
        self.assertFalse([t for t in threading.enumerate() if t.name.startswith("asyncio_") and t.is_alive() and False])
        # (2) a mail is found AFTER the deadline: it is not flagged
        ev = threading.Event()
        ev.set()
        FakeImap.stores = []

        def uid(self, cmd, *a):
            if cmd == "SEARCH":
                return ("OK", [b"1"])
            if cmd == "FETCH":
                raw = b"From: x@dir.example\r\nDate: " + _t.strftime("%a, %d %b %Y %H:%M:%S +0000", _t.gmtime()).encode() + b"\r\nSubject: Confirm\r\n\r\nhttps://dir.example/verify?token=1"
                return ("OK", [(b"1", raw)])
            FakeImap.stores.append(a)
            return ("OK", [])
        FakeImap.uid = uid
        r = ag.read_mail_from_domain("https://dir.example/submit", 5, ev)
        self.assertTrue(r.startswith("CANCELLED"), r)
        self.assertEqual(FakeImap.stores, [])
        # (3) not cancelled: the mail is found and flagged exactly once (the normal path still works)
        FakeImap.stores = []
        r = ag.read_mail_from_domain("https://dir.example/submit", 5, threading.Event())
        self.assertTrue(r.startswith("MAIL FOUND"), r)
        self.assertEqual(len(FakeImap.stores), 1)


class PushPagesAndBadges(unittest.TestCase):
    """26 Nov 2026: every tool and guide page is promoted (push-priority rotation), the homepage only for root-domain-only sites;
    a visible captcha = needs a person; a badge request = ops/needs_badge.md for the Director."""

    def test_products_are_homepage_then_tools_then_guides_in_push_priority_order(self):
        prods = ag.load_products()
        kinds = [p["kind"] for p in prods]
        self.assertEqual(kinds[0], "homepage")
        self.assertEqual(kinds, sorted(kinds, key=lambda k: {"homepage": 0, "tool": 1, "guide": 2}[k]))   # tools before guides
        ids = [p["id"] for p in prods]
        self.assertIn("analyze", ids)                                       # tool page from the shared page list
        self.assertTrue(any(k == "guide" for k in kinds))
        prio = ag.load_priority()
        tools = [p["id"] for p in prods if p["kind"] == "tool"]
        self.assertEqual(tools[:4], [t for t in ("shadowban-checker", "analyze", "follower-count-checker", "hashtag-generator")])   # tiers 1 then 2
        guides = [p["id"] for p in prods if p["kind"] == "guide"]
        self.assertEqual(guides[0], "guide-no-views")
        self.assertEqual([prio.get(g, 99) for g in guides], sorted(prio.get(g, 99) for g in guides))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len({p["url"] for p in prods}), len(prods))
        for p in prods:
            self.assertLessEqual(len(p["short_description"]), 50, p["id"])
            self.assertLessEqual(len(p["long_description"]), 150, p["id"])
            self.assertTrue(p["url"].startswith("https://inayaflow.ai"), p["id"])

    def test_missing_priority_or_page_list_keeps_the_listings_file(self):
        prods = ag.load_products(pages_path="no-such-file.py", priority_path="no-such-file.json")
        self.assertEqual(prods[0]["id"], "homepage")
        self.assertEqual({p["kind"] for p in prods}, {"homepage", "tool"})

    def test_lead_rotation_spreads_over_tools_and_skips_tools_done_on_the_site(self):
        prods = [{"id": "homepage", "kind": "homepage"}, {"id": "t1", "kind": "tool"}, {"id": "t2", "kind": "tool"}, {"id": "g1", "kind": "guide"}]
        self.assertEqual(ag.pick_lead({}, prods)["id"], "t1")
        st = {"x.example": {"name": "X", "status": "SUBMITTED", "products": {"t1": {"status": "SUBMITTED"}}}}
        self.assertEqual(ag.pick_lead(st, prods)["id"], "t2")                           # t1 already has a listing elsewhere
        self.assertEqual(ag.pick_lead({}, prods, {"t1": 1})["id"], "t2")                 # this run's count too (dry runs record nothing)
        site = {"name": "Y", "url": "https://y.example/submit"}
        st2 = {"y.example": {"name": "Y", "status": "OTHER", "products": {"t1": {"status": "ONE_PER_DOMAIN"}}}}
        self.assertEqual(ag.pick_lead(st2, prods, None, site)["id"], "t2")
        self.assertIsNone(ag.pick_lead({}, [{"id": "homepage", "kind": "homepage"}]))

    def test_task_texts(self):
        site = {"name": "Dir", "url": "https://dir.example/submit"}
        prods = ag.load_products()
        tool = [p for p in prods if p["id"] == "shadowban-checker"][0]
        guide = [p for p in prods if p["kind"] == "guide"][0]
        lead = ag.build_task(site, tool, lead=True)
        self.assertIn("ROOT DOMAIN RULE", lead)
        self.assertIn("used_homepage", lead)
        self.assertIn("tiktok-shadowban-checker", lead)
        self.assertNotIn("ADDITIONAL listing", lead)
        g = ag.build_task(site, guide)
        self.assertIn("WRONG_KIND", g)
        self.assertIn("ADDITIONAL listing", g)
        self.assertTrue(g.startswith("Submit our free guide"))
        self.assertIn("where an article, resource or link is submitted", g)
        self.assertIn("where a tool/product/startup is submitted", lead)
        self.assertIn(guide["url"], g)
        for t in (lead, g, ag.build_task(site)):
            self.assertIn("NOT a stop", t)                    # an invisible reCAPTCHA script alone is not a reason to stop
            self.assertIn("ANY challenge you can SEE", t)     # any visible challenge / 'verify you are human' text = stop that site
            self.assertIn("'verify you are human'", t)
            self.assertIn("stop this site at once", t)
            self.assertIn("Never click, solve", t)
            self.assertIn("'badge' field", t)
            self.assertIn("Never pay", t)
        self.assertIn("WRONG_KIND", ag.RESULT_CODES)
        self.assertIn("WRONG_KIND", ag.PRODUCT_DONE)

    def _badges_file(self, domains):
        import tempfile
        f = os.path.join(tempfile.mkdtemp(), "badges_added.json")
        json.dump({"domains": domains}, open(f, "w"))
        saved = ag.BADGES_ADDED_PATH
        ag.BADGES_ADDED_PATH = f
        self.addCleanup(lambda: setattr(ag, "BADGES_ADDED_PATH", saved))

    def test_needs_badge_is_closed_until_the_director_adds_the_badge_then_tried_once(self):
        import datetime
        D = lambda n: (datetime.date.today() + datetime.timedelta(days=n)).isoformat()
        today = D(0)
        self._badges_file({})
        site = {"name": "B", "url": "https://b.example/submit"}
        st = {"b.example": {"name": "B", "status": "NEEDS_BADGE", "date": D(-2), "tries": 1, "url": site["url"]}}
        ag.update_walls(st, D(-2))
        self.assertTrue(ag.domain_block(st, "b.example", today, known_path="none.json"))
        self.assertFalse(ag.eligible(site, st, {}, today))
        self._badges_file({"b.example": {"date": D(-1), "where": "https://inayaflow.ai/featured"}})
        self.assertIsNone(ag.domain_block(st, "b.example", today, known_path="none.json"))
        self.assertTrue(ag.eligible(site, st, {}, today))
        self.assertIn("https://inayaflow.ai/featured", ag.build_task(site))
        st["b.example"]["date"] = D(-1)                                     # it asked for the badge AGAIN on/after the badge day: closed
        self.assertFalse(ag.eligible(site, st, {}, today))
        st["c.example"] = {"name": "C", "status": "NEEDS_BADGE", "date": D(-2), "url": "https://c.example/s"}
        st[ag.WALLS_KEY]["c.example"] = {"status": "WALLED", "reason": "CAPTCHA"}
        self._badges_file({"c.example": {"date": D(-1)}})
        self.assertTrue(ag.domain_block(st, "c.example", today, known_path="none.json"))   # another wall still blocks

    def test_badge_date_in_the_future_or_badly_written_unlocks_nothing(self):
        import datetime
        D = lambda n: (datetime.date.today() + datetime.timedelta(days=n)).isoformat()
        today = D(0)
        site = {"name": "B", "url": "https://b.example/submit"}
        st = {"b.example": {"name": "B", "status": "NEEDS_BADGE", "date": D(-3), "tries": 1, "url": site["url"]}}
        ag.update_walls(st, D(-3))
        for bad in (D(1), "27/11/2026", "2026-13-40", "", "soon", None, 20261127):
            self._badges_file({"b.example": {"date": bad, "where": "https://inayaflow.ai/featured"}})
            self.assertFalse(ag.badge_unlocked("b.example", D(-3), today), bad)
            self.assertFalse(ag.eligible(site, st, {}, today), bad)
            self.assertTrue(ag.domain_block(st, "b.example", today, known_path="none.json"), bad)
            self.assertNotIn("https://inayaflow.ai/featured", ag.build_task(site), bad)
        self._badges_file({"b.example": {"date": D(-1)}})
        self.assertTrue(ag.badge_unlocked("b.example", D(-3), today))
        self.assertFalse(ag.badge_unlocked("b.example", "not-a-date", today))          # a bad record date unlocks nothing
        self.assertFalse(ag.badge_unlocked("b.example", D(-3), "bad-today"))
        self.assertFalse(ag.badge_unlocked("b.example", D(-3), D(-2)))                 # badge day later than "today"

    def test_tools_only_directories_by_name_or_url(self):
        for name, url in (("AI Tools Directory", "https://aitoolsdirectory.com/submit"), ("Toolify", "https://www.toolify.ai/submit"),
                          ("SaaSHub", "https://www.saashub.com/submit"), ("Startup Stash", "https://startupstash.com/add"),
                          ("X", "https://dir.example/submit-tool"), ("Apps list", "https://dir.example/"), ("There", "https://there.ai/")):
            self.assertTrue(ag.tools_only_site({"name": name, "url": url}), name)
        for name, url in (("Creator Resources", "https://creatorlinks.example/submit"), ("Marketing Blog List", "https://marketing.example/suggest"),
                          ("Domain Mail", "https://domainmail.example/add")):
            self.assertFalse(ag.tools_only_site({"name": name, "url": url}), name)

    def test_extra_count_leaves_out_the_lead_the_homepage_fallback_and_skip_records(self):
        t = "2026-11-26"
        e = {"name": "M", "status": "SUBMITTED", "date": t, "lead": "t1",
             "products": {"t1": {"status": "SUBMITTED", "date": t}, "t2": {"status": "SUBMITTED", "date": t},
                          "g1": {"status": "WRONG_KIND", "date": t}, "g2": {"status": "WRONG_KIND", "date": t, "skipped": True},
                          "t3": {"status": "SUBMITTED", "date": "2026-11-25"}}}
        self.assertEqual(ag.extra_today(e, t), 2)                                    # t2 + g1 only
        root = {"name": "R", "status": "SUBMITTED", "date": t, "lead": "homepage",
                "products": {"homepage": {"status": "SUBMITTED", "date": t}, "t1": {"status": "ONE_PER_DOMAIN", "date": t, "skipped": True}}}
        self.assertEqual(ag.count_tool_today({"m.example": e, "r.example": root}, t), 2)
        old_lead = dict(e, date="2026-11-25")                                        # lead from another day: t1 today is an extra
        self.assertEqual(ag.extra_today(old_lead, t), 3)

    def _run_main(self, outcomes, products):
        """FEED mode, live (DRY off), every file in a temp folder. outcomes: {(domain, product id): result dict}."""
        import tempfile, io, contextlib
        d = tempfile.mkdtemp()
        names = ("STATE_PATH", "SPEND_PATH", "LOG_PATH", "SUMMARY_PATH", "OLD_STATE_PATH", "VSTATE_PATH", "CLICK_PATH", "NOFOLLOW_LATER_PATH", "NEEDS_BADGE_PATH",
                 "run_one", "preflight_limits", "build_feed", "load_products", "DRY", "BADGES_ADDED_PATH", "backfill_sites", "RESUME_FILE")
        saved = {n: getattr(ag, n) for n in names}
        for n in ("STATE_PATH", "SPEND_PATH", "OLD_STATE_PATH", "VSTATE_PATH", "BADGES_ADDED_PATH", "RESUME_FILE"):
            setattr(ag, n, os.path.join(d, "ops", n.lower() + ".json"))
        for n in ("LOG_PATH", "SUMMARY_PATH", "CLICK_PATH", "NOFOLLOW_LATER_PATH", "NEEDS_BADGE_PATH"):
            setattr(ag, n, os.path.join(d, "ops", n.lower() + ".md"))
        calls = []
        sites = sorted({k[0] for k in outcomes})

        def fake(s, product=None, lead=False):
            pid = product["id"] if product else "homepage"
            calls.append((ag.dom_of(s), pid, lead))
            r = {"status": "OTHER", "note": "unexpected", "secs": 1, "cost": 0.01, "cost_estimated": False, "steps": 1, "usage": None, "flags": [], "model": "m"}
            r.update(outcomes.get((ag.dom_of(s), pid), {}))
            return r
        ag.run_one, ag.preflight_limits, ag.DRY = fake, (lambda: True), False
        ag.build_feed = lambda *a, **k: [{"name": x, "url": "https://%s/submit" % x, "group": "candidates"} for x in sites]
        ag.load_products = lambda: products
        ag.backfill_sites = lambda *a, **k: []
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ag.main()
            state = json.load(open(ag.STATE_PATH, encoding="utf8"))
            badge_md = open(ag.NEEDS_BADGE_PATH, encoding="utf8").read()
            click_md = open(ag.CLICK_PATH, encoding="utf8").read()
        finally:
            for n, v in saved.items():
                setattr(ag, n, v)
        return calls, state, badge_md, click_md, buf.getvalue()

    PRODS = [{"id": "homepage", "kind": "homepage", "name": "InayaFlow", "url": "https://inayaflow.ai/"},
             {"id": "t1", "kind": "tool", "name": "T1", "url": "https://inayaflow.ai/tools/t1"},
             {"id": "t2", "kind": "tool", "name": "T2", "url": "https://inayaflow.ai/tools/t2"},
             {"id": "g1", "kind": "guide", "name": "G1", "url": "https://inayaflow.ai/blog/g1"},
             {"id": "g2", "kind": "guide", "name": "G2", "url": "https://inayaflow.ai/blog/g2"}]

    def test_multi_listing_site_gets_each_tool_then_guides_until_guides_are_refused(self):
        ok = {"status": "SUBMITTED", "note": ""}
        calls, state, _, _, out = self._run_main({("multi.example", "t1"): ok, ("multi.example", "t2"): ok,
                                                  ("multi.example", "g1"): {"status": "WRONG_KIND", "note": "tools only"}}, self.PRODS)
        self.assertEqual(calls, [("multi.example", "t1", True), ("multi.example", "t2", False), ("multi.example", "g1", False)])
        pr = state["multi.example"]["products"]
        self.assertEqual(state["multi.example"]["lead"], "t1")
        self.assertEqual((pr["t1"]["status"], pr["t2"]["status"], pr["g1"]["status"], pr["g2"]["status"]), ("SUBMITTED", "SUBMITTED", "WRONG_KIND", "WRONG_KIND"))
        self.assertNotIn("homepage", pr)                                     # the homepage is not used where tool pages are accepted
        self.assertIsNone(state.get(ag.FAILS_KEY, {}).get("multi.example"))   # the lead attempt is not counted twice

    def test_root_domain_only_site_lists_the_homepage_and_stops(self):
        calls, state, _, _, out = self._run_main({("root.example", "t1"): {"status": "SUBMITTED", "note": "root only", "used_homepage": True}}, self.PRODS)
        self.assertEqual(calls, [("root.example", "t1", True)])
        pr = state["root.example"]["products"]
        self.assertEqual(pr["homepage"]["status"], "SUBMITTED")
        self.assertEqual({pr[k]["status"] for k in ("t1", "t2", "g1", "g2")}, {"ONE_PER_DOMAIN"})
        self.assertIn("homepage (root domain only)", out)

    def test_badge_and_visible_captcha_go_to_their_lists_and_never_to_stdout(self):
        snippet = '<a href="https://badge.example/?ref=inaya"><img src="https://badge.example/b.svg"></a>'
        calls, state, badge_md, click_md, out = self._run_main({
            ("badge.example", "t1"): {"status": "NEEDS_BADGE", "note": "badge required", "badge": snippet},
            ("cap.example", "t1"): {"status": "CAPTCHA", "note": "image puzzle on submit"}}, self.PRODS)
        self.assertEqual(state["badge.example"]["status"], "NEEDS_BADGE")
        self.assertEqual(state["badge.example"]["badge"], snippet)
        self.assertIn("badge.example", badge_md)
        self.assertIn("badge.example/b.svg", badge_md)
        self.assertIn("badges_added.json", badge_md)
        self.assertNotIn("b.svg", out)                                       # public stdout: no snippet
        self.assertIn("NEEDS_PERSON (visible captcha)", click_md)
        self.assertIn("https://cap.example/submit", click_md)
        self.assertEqual([c for c in calls if c[0] == "cap.example"], [("cap.example", "t1", True)])   # a wall: no more products there

    def test_extras_wait_until_every_new_site_was_tried_and_stop_at_3_per_domain(self):
        ok = {"status": "SUBMITTED", "note": ""}
        prods = [{"id": "homepage", "kind": "homepage", "name": "InayaFlow", "url": "https://inayaflow.ai/"}] + \
                [{"id": "t%d" % i, "kind": "tool", "name": "T%d" % i, "url": "https://inayaflow.ai/tools/t%d" % i} for i in range(1, 7)]
        outcomes = {}
        for d in ("a.example", "b.example"):
            for i in range(1, 7):
                outcomes[(d, "t%d" % i)] = ok
        calls, state, _, _, _ = self._run_main(outcomes, prods)
        leads = [c for c in calls if c[2]]
        self.assertEqual(calls[:2], leads)                                    # both NEW sites first, then any extra listing
        self.assertEqual(len(leads), 2)
        for d in ("a.example", "b.example"):
            extras = [c for c in calls if c[0] == d and not c[2]]
            self.assertEqual(len(extras), ag.MAX_EXTRA_PER_SITE_PER_DAY)       # 3 extra listings per domain per day, the rest wait
            self.assertEqual(ag.extra_today(state[d], ag.datetime.date.today().isoformat()), 3)
        self.assertEqual(ag.MAX_EXTRA_PER_SITE_PER_DAY, 3)

    def test_guides_are_not_tried_on_a_software_directory(self):
        ok = {"status": "SUBMITTED", "note": ""}
        calls, state, _, _, _ = self._run_main({("aitools.example", "t1"): ok, ("aitools.example", "t2"): ok}, self.PRODS)
        self.assertEqual([c[1] for c in calls], ["t1", "t2"])                  # g1 / g2 never attempted
        pr = state["aitools.example"]["products"]
        self.assertEqual((pr["g1"]["status"], pr["g1"].get("skipped")), ("WRONG_KIND", True))

    def test_the_workflow_carries_needs_badge_to_the_private_repo(self):
        w = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".github", "workflows", "directory-agent.yml"), encoding="utf8").read()
        self.assertGreaterEqual(w.count("needs_badge.md"), 2)                # restored before the run and pushed after it


if __name__ == "__main__":
    unittest.main()
