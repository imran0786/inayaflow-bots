import json, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="jobs_test_")
os.environ["JOBS_WORK"] = os.path.join(TMP, "work")
os.environ["GENIE_MAIN_WORK"] = os.path.join(TMP, "genie")
os.environ["ANTHROPIC_API_KEY"] = "test"
os.environ.pop("JOB_DEEPSEEK_FALLBACK", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner as r

BRIEF = """---
id: 2026-11-19-test-1
role: research
model: haiku
max_cost: 0.15
allowed: read, write_result, web, send_email
max_searches: 3
output: my result
---
## Job
Find the thing and report it with sources.

## Approvals (word for word)
none
"""


def setup_files():
    os.makedirs(os.path.join(r.WORK, "context", "docs", "board"), exist_ok=True)
    with open(os.path.join(r.WORK, "context", "docs", "CORE_RULES.md"), "w", encoding="utf-8") as f:
        f.write("# CORE RULES\nNever send any message without Imran's yes.\n")
    with open(os.path.join(r.WORK, "context", "docs", "board", "TODAY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join("line %d about backlinks" % i for i in range(1, 3001)))
    with open(os.path.join(TMP, "secret.txt"), "w") as f:
        f.write("outside")
    os.makedirs(r.GENIE_MAIN, exist_ok=True)
    with open(os.path.join(r.GENIE_MAIN, "app.ts"), "w") as f:
        f.write("export const x = 1\n")
    with open(os.path.join(r.GENIE_MAIN, ".env.local"), "w") as f:
        f.write("KEY=1\n")


class Brief(unittest.TestCase):
    def test_parse(self):
        j = r.parse_brief(BRIEF)
        self.assertEqual((j["id"], j["role"], j["model"], j["max_cost"], j["max_searches"]), ("2026-11-19-test-1", "research", "haiku", 0.15, 3))
        self.assertEqual(j["allowed"], ["read", "web", "write_result"])
        self.assertEqual(j["refused_actions"], ["send_email"])   # outward actions are never granted
        self.assertEqual(j["output"], "my-result")
        self.assertIsNone(r.validate(j))

    def test_defaults_and_caps(self):
        j = r.parse_brief("---\nid: a\nmax_cost: 99\n---\nDo this job please, thank you.")
        self.assertEqual(j["allowed"], ["read", "write_result"])   # default: no web
        self.assertEqual(j["max_cost"], r.HARD_JOB_CAP)
        self.assertEqual(j["model"], "opus")
        self.assertEqual(r.parse_brief("---\nid: a\n---\nDo this job please, thank you.")["max_cost"], 0.20)

    def test_validate(self):
        self.assertEqual(r.validate(r.parse_brief("---\nid: bad id!\n---\n" + "x" * 30)), "bad id")
        self.assertEqual(r.validate(r.parse_brief("---\nid: a\nrole: hacker\n---\n" + "x" * 30)), "unknown role")
        self.assertEqual(r.validate(r.parse_brief("---\nid: a\nmodel: gpt\n---\n" + "x" * 30)), "unknown model")
        self.assertEqual(r.validate(r.parse_brief("---\nid: a\n---\nshort")), "empty job text")
        self.assertIsNone(r.validate(r.parse_brief("---\nid: a\nrole: qa\nmodel: sonnet\n---\n" + "x" * 30)))


class Tools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_files()

    def test_read_paging_and_escape(self):
        j = r.parse_brief(BRIEF)
        out = r.tool_read(j, "docs", "docs/board/TODAY.md")
        self.assertTrue(out.startswith("1\tline 1 about"))
        self.assertIn("call read_file again with start=", out)
        self.assertLess(len(out), r.READ_CHARS + 200)
        self.assertIn("2000\tline 2000", r.tool_read(j, "docs", "docs/board/TODAY.md", 2000))
        self.assertTrue(r.tool_read(j, "docs", "../../secret.txt").startswith("ERROR"))
        self.assertTrue(r.tool_read(j, "docs", "/etc/passwd").startswith("ERROR"))

    def test_genie_only_for_code_read(self):
        j = r.parse_brief(BRIEF)
        self.assertTrue(r.tool_read(j, "genie", "app.ts").startswith("ERROR"))
        j2 = r.parse_brief(BRIEF.replace("role: research", "role: code-read"))
        self.assertIn("export const x", r.tool_read(j2, "genie", "app.ts"))
        self.assertTrue(r.tool_read(j2, "genie", ".env.local").startswith("ERROR"))

    def test_search_and_list(self):
        j = r.parse_brief(BRIEF)
        self.assertIn("docs/CORE_RULES.md:2:", r.tool_search(j, "docs", "docs", "imran's YES"))
        self.assertIn("more matches not shown", r.tool_search(j, "docs", "docs/board/TODAY.md", "backlinks"))
        self.assertEqual(r.tool_search(j, "docs", "docs", "zzzz-not-there"), "no match")
        self.assertIn("board/", r.tool_list(j, "docs", "docs"))

    def test_prompt_and_tools(self):
        j = r.parse_brief(BRIEF)
        s = r.build_system(j)
        self.assertIn("Never send any message without Imran's yes.", s)     # core rules are always in the prompt
        self.assertIn("send_email", s)                                      # refused action is named
        names = [t.get("name") for t in r.build_tools(j)]
        self.assertEqual(names, ["read_file", "list_files", "search_files", "submit_result", "web_search", "web_fetch"])
        self.assertEqual([t for t in r.build_tools(j) if t.get("name") == "web_search"][0]["max_uses"], 3)
        noweb = r.parse_brief(BRIEF.replace(", web, send_email", ""))
        self.assertEqual([t.get("name") for t in r.build_tools(noweb)], ["read_file", "list_files", "search_files", "submit_result"])
        self.assertIn("NO web access", r.build_system(noweb))
        self.assertIn("first line of your\nresult must be exactly PASS or FAIL", r.build_system(r.parse_brief(BRIEF.replace("role: research", "role: qa"))))
        for t in r.build_tools(j):   # no tool can act outward
            self.assertNotRegex(t["name"], r"(send|post|publish|merge|deploy|pay|write_file|shell|bash)")


class Loop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        setup_files()

    def test_tool_then_submit(self):
        j = r.parse_brief(BRIEF)
        seen = []

        def post(url, body, headers, timeout=0):
            seen.append(body)
            if len(seen) == 1:
                return {"stop_reason": "tool_use", "usage": {"input_tokens": 1000, "output_tokens": 100, "server_tool_use": {"web_search_requests": 2}},
                        "content": [{"type": "tool_use", "id": "t1", "name": "search_files", "input": {"repo": "docs", "path": "docs", "text": "yes"}}]}
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 500, "cache_read_input_tokens": 1000, "output_tokens": 200},
                    "content": [{"type": "tool_use", "id": "t2", "name": "submit_result", "input": {"result": "FOUND IT"}}]}
        status, text, m = r.run_claude(j, post)
        self.assertEqual((status, text), ("DONE", "FOUND IT"))
        self.assertEqual(seen[0]["model"], "claude-haiku-5-5")
        self.assertNotIn("temperature", seen[0])
        self.assertEqual(seen[1]["messages"][2]["content"][0]["type"], "tool_result")
        self.assertIn("CORE_RULES.md:2", seen[1]["messages"][2]["content"][0]["content"])
        want = (1000 * 0.10 + 100 * 0.50 + 500 * 0.10 + 1000 * 0.01 + 200 * 0.50) / 1e6 + 2 * 0.01
        self.assertAlmostEqual(m.cost, want)
        self.assertEqual((m.searches, m.calls), (2, 2))

    def test_cost_cap_stops(self):
        j = r.parse_brief(BRIEF.replace("max_cost: 0.15", "max_cost: 0.02").replace(", web, send_email", ""))
        calls = []

        def post(url, body, headers, timeout=0):
            calls.append(1)
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 50000, "output_tokens": 40000},
                    "content": [{"type": "tool_use", "id": "t%d" % len(calls), "name": "list_files", "input": {"repo": "docs", "path": "docs"}}]}
        status, _t, m = r.run_claude(j, post)
        self.assertEqual(status, "COST_CAP")
        self.assertEqual(len(calls), 1)

    def test_refusal_and_plain_answer(self):
        j = r.parse_brief(BRIEF)
        self.assertEqual(r.run_claude(j, lambda *a, **k: {"stop_reason": "refusal", "usage": {}, "content": []})[0], "REFUSED")
        n = []

        def post(url, body, headers, timeout=0):
            n.append(1)
            return {"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 10}, "content": [{"type": "text", "text": "plain answer"}]}
        status, text, _m = r.run_claude(j, post)
        self.assertEqual((status, text, len(n)), ("DONE", "plain answer", 2))   # asked once to hand in, then accepted

    def test_cut_off_submit_is_asked_again(self):
        # 27 Nov: a submit_result cut off by max_tokens came back as DONE with no text; now it is asked again
        j = r.parse_brief(BRIEF)
        seen = []

        def post(url, body, headers, timeout=0):
            seen.append(body)
            if len(seen) == 1:
                return {"stop_reason": "max_tokens", "usage": {"input_tokens": 10, "output_tokens": 10},
                        "content": [{"type": "tool_use", "id": "s1", "name": "submit_result", "input": {}}]}
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 10},
                    "content": [{"type": "tool_use", "id": "s2", "name": "submit_result", "input": {"result": "FULL REPORT"}}]}
        status, text, _m = r.run_claude(j, post)
        self.assertEqual((status, text, len(seen)), ("DONE", "FULL REPORT", 2))
        self.assertTrue(seen[1]["messages"][2]["content"][0]["content"].startswith("EMPTY_RESULT"))

    def test_always_empty_submit_gives_no_result(self):
        j = r.parse_brief(BRIEF)
        n = []

        def post(url, body, headers, timeout=0):
            n.append(1)
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 10},
                    "content": [{"type": "tool_use", "id": "s%d" % len(n), "name": "submit_result", "input": {"result": ""}}]}
        status, text, _m = r.run_claude(j, post)
        self.assertEqual((status, len(n)), ("NO_RESULT", 3))

    def test_budget_nearly_used_gets_one_hand_in_turn(self):
        # 27 Nov: a finder stopped at $3.29 of $4 with nothing handed in; now the job is told to hand in what it has
        j = r.parse_brief(BRIEF.replace("max_cost: 0.15", "max_cost: 0.05"))   # web job: the web reserve is what blocks a normal turn
        seen = []

        def post(url, body, headers, timeout=0):
            seen.append(json.dumps(body["messages"]))
            if len(seen) == 1:
                return {"stop_reason": "tool_use", "usage": {"input_tokens": 80000, "output_tokens": 300},
                        "content": [{"type": "tool_use", "id": "t1", "name": "list_files", "input": {"repo": "docs", "path": "docs"}}]}
            return {"stop_reason": "tool_use", "usage": {"input_tokens": 100, "cache_read_input_tokens": 60000, "output_tokens": 300},
                    "content": [{"type": "tool_use", "id": "t2", "name": "submit_result", "input": {"result": "PARTIAL LIST"}}]}
        status, text, _m = r.run_claude(j, post)
        self.assertEqual((status, text, len(seen)), ("DONE", "PARTIAL LIST", 2))
        self.assertIn("BUDGET NEARLY USED", seen[1])

    def test_long_prompt_price_and_sonnet(self):
        m = r.Meter("haiku")
        m.add({"input_tokens": 150000, "output_tokens": 1000})
        self.assertAlmostEqual(m.cost, (150000 * 0.50 + 1000 * 2.50) / 1e6)
        s = r.Meter("sonnet")
        s.add({"input_tokens": 1000, "output_tokens": 1000})
        self.assertAlmostEqual(s.cost, (1000 * 2.0 + 1000 * 10.0) / 1e6)

    def test_deepseek_is_off(self):
        self.assertFalse(r.DEEPSEEK_ON)
        with self.assertRaises(RuntimeError):
            r.run_deepseek(r.parse_brief(BRIEF))

    def test_result_doc(self):
        j = r.parse_brief(BRIEF)
        m = r.Meter("haiku")
        d = r.result_doc(j, "COST_CAP", "partial", m, "Claude")
        self.assertIn("NOT COMPLETE", d)
        self.assertIn("nothing was sent, posted or changed", d)
        self.assertIn("CHECK BEFORE USE", r.result_doc(j, "DONE", "x", m, "DeepSeek"))
        self.assertEqual(r.clean('says (cite index="1-1">five per post</cite> (https://a.com) <cite index="2">x</cite>'), "says five per post (https://a.com) x")


class ModelsAndCaps(unittest.TestCase):
    @staticmethod
    def brief(**kw):
        nl = chr(10)
        head = nl.join("%s: %s" % kv for kv in kw.items())
        return nl.join(["---", "id: a", head, "---", "x" * 30])

    def test_new_models_valid_and_priced(self):
        for name, mid, pin, pout in (("opus", "claude-opus-5-5", 4.0, 20.0), ("fable", "claude-fable-5-2", 8.0, 40.0)):
            j = r.parse_brief(self.brief(model=name, max_cost=3))
            self.assertEqual(j["model"], name)
            self.assertIsNone(r.validate(j))
            self.assertEqual((r.MODELS[name]["id"], r.MODELS[name]["in"], r.MODELS[name]["out"]), (mid, pin, pout))
        m = r.Meter("fable")
        m.add({"input_tokens": 1000000, "output_tokens": 1000000})
        self.assertAlmostEqual(m.cost, 48.0, 4)
        m = r.Meter("opus")
        m.add({"cache_read_input_tokens": 1000000})
        self.assertAlmostEqual(m.cost, 0.20, 4)

    def test_default_model_is_opus(self):
        j = r.parse_brief(self.brief(role="research"))
        self.assertEqual(j["model"], "opus")

    def test_job_ceiling_and_search_ceiling(self):
        j = r.parse_brief(self.brief(model="opus", max_cost=9, max_searches=40))
        self.assertEqual((j["max_cost"], j["max_searches"]), (5.0, 15))

    def test_total_cap(self):
        spend = {"2026-11-26": 50.0, "2026-11-27": 60.0, "2026-11-28": 39.99}
        self.assertAlmostEqual(r.total_spent(spend), 99.99, 4)      # 26 Nov is before the start day: not counted
        self.assertLess(r.total_spent(spend), r.TOTAL_CAP)
        spend["2026-11-29"] = 0.02
        self.assertGreaterEqual(r.total_spent(spend), r.TOTAL_CAP)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
