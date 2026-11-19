import os, shutil, subprocess, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="jobs_code_test_")
os.environ["JOBS_WORK"] = os.path.join(TMP, "work")
os.environ["CODE_WORK"] = os.path.join(TMP, "code")
os.environ["GENIE_MAIN_WORK"] = os.path.join(TMP, "genie")
os.environ["ANTHROPIC_API_KEY"] = "test"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner as r
assert r.CODE_ROLE_ON is False       # the default is OFF
OFF_VERDICT = r.validate(r.parse_brief(chr(10).join(["---", "id: a", "role: code", "---", "x" * 30])))
r.CODE_ROLE_ON = True                # the tests below exercise the role itself

BRIEF = """---
id: 2026-11-19-code-x
role: code
model: haiku
---
## Job
Add a comment line to tests/example.test.mjs. Nothing else.
"""
SPEC = """✔ one passes (1.2ms)
✖ two fails (3.4ms)
ℹ tests 10
ℹ pass 8
ℹ fail 2
✖ failing tests:
✖ two fails (3.4ms)
✖ three fails (0.5ms)
"""
TAP = "ok 1 - a\nnot ok 2 - b breaks\n# tests 2\n# pass 1\n# fail 1\n"


def sh(*a, cwd=None):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True)


def make_repo():
    """A bare 'remote' with main + a working copy on a robot branch, like prepare_code leaves it."""
    remote = os.path.join(TMP, "remote.git")
    for d in (remote, r.CODE_WORK):   # git marks object files read-only (Windows): clear the flag, then delete
        shutil.rmtree(d, onerror=lambda f, path, e: (os.chmod(path, 0o700), f(path))) if os.path.exists(d) else None
    sh("git", "init", "-q", "--bare", "-b", "main", remote)
    sh("git", "clone", "-q", remote, r.CODE_WORK)
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("core.autocrlf", "false")):
        sh("git", "-C", r.CODE_WORK, "config", k, v)
    os.makedirs(os.path.join(r.CODE_WORK, "tests"))
    os.makedirs(os.path.join(r.CODE_WORK, "supabase"))
    with open(os.path.join(r.CODE_WORK, "tests", "example.test.mjs"), "w", newline="\n") as f:
        f.write("// first\nconst a = 1\n")
    sh("git", "-C", r.CODE_WORK, "checkout", "-q", "-b", "main")
    sh("git", "-C", r.CODE_WORK, "add", "-A")
    sh("git", "-C", r.CODE_WORK, "commit", "-q", "-m", "base")
    sh("git", "-C", r.CODE_WORK, "push", "-q", "origin", "main")
    r._token_url = lambda: remote
    return remote


class CodeRole(unittest.TestCase):
    def test_role_is_off_by_default(self):
        self.assertEqual(OFF_VERDICT, "the code role is switched off")

    def test_brief(self):
        j = r.parse_brief(BRIEF)
        self.assertEqual((j["model"], j["max_cost"], j["branch"]), ("sonnet", 2.0, "robot/2026-11-19-code-x"))   # always Sonnet, $2 default
        self.assertIsNone(r.validate(j))
        self.assertEqual(r.MODELS["sonnet"]["id"], "claude-sonnet-5-5")
        self.assertEqual(r.validate(r.parse_brief(BRIEF.replace("model: haiku", "branch: main"))), "code jobs work only on a new branch named robot/<name>")
        self.assertEqual(r.validate(r.parse_brief(BRIEF.replace("model: haiku", "branch: feat/x"))), "code jobs work only on a new branch named robot/<name>")
        self.assertEqual(r.validate(r.parse_brief(BRIEF.replace("model: haiku", "max_cost: 9"))), "max_cost is above the daily cap")

    def test_tools_and_prompt(self):
        j = r.parse_brief(BRIEF)
        names = [t["name"] for t in r.build_tools(j)]
        self.assertEqual(names, ["read_file", "list_files", "search_files", "submit_result", "write_file", "edit_file", "run_tests"])
        self.assertNotIn("write_file", [t["name"] for t in r.build_tools(r.parse_brief(BRIEF.replace("role: code", "role: research")))])
        self.assertIn("You never push, merge, deploy or run SQL", r.build_system(j))
        for t in r.build_tools(j):
            self.assertNotRegex(t["name"], r"(shell|bash|git|push|merge|deploy|sql|send|post)")

    def test_write_guards(self):
        make_repo()
        j = r.parse_brief(BRIEF)
        for bad in (".env.local", ".github/workflows/x.yml", "vercel.json", "supabase/migrations/x.sql", "ops/x.md", "../outside.txt", ".git/config", "package-lock.json"):
            self.assertTrue(r.tool_write(j, bad, "x").startswith("ERROR"), bad)
        self.assertTrue(r.tool_write(j, "lib/new.ts", "export const a = 1\n").startswith("written"))
        other = r.parse_brief(BRIEF.replace("role: code", "role: code-read"))
        self.assertTrue(r.tool_write(other, "lib/new2.ts", "x").startswith("ERROR"))     # only code jobs write
        self.assertEqual(r.tool_edit(j, "tests/example.test.mjs", "// first\n", "// first\n// added by the robot\n"), "edited tests/example.test.mjs")
        self.assertTrue(r.tool_edit(j, "tests/example.test.mjs", "nope", "x").startswith("ERROR"))
        self.assertIn("added by the robot", r.tool_read(j, "genie", "tests/example.test.mjs"))

    def test_parse_tests(self):
        t = r.parse_tests(SPEC)
        self.assertEqual((t["tests"], t["pass"], t["fail"]), (10, 8, 2))
        self.assertEqual(t["failing"], ["two fails", "three fails"])
        t = r.parse_tests(TAP)
        self.assertEqual((t["tests"], t["pass"], t["fail"], t["failing"]), (2, 1, 1, ["b breaks"]))
        self.assertIsNone(r.parse_tests("boom")["tests"])
        self.assertIn("did not run", r.tests_line({"tests": None, "note": "x"}))

    def test_finish_pushes_only_the_robot_branch(self):
        remote = make_repo()
        j = r.parse_brief(BRIEF)
        j["_base"] = "abc1234"
        sh("git", "-C", r.CODE_WORK, "checkout", "-q", "-b", j["branch"])
        r.tool_edit(j, "tests/example.test.mjs", "// first\n", "// first\n// added by the robot\n")
        info = r.finish_code(j)
        self.assertTrue(info["pushed"], info)
        heads = sh("git", "-C", remote, "for-each-ref", "--format=%(refname) %(objectname:short)", "refs/heads").stdout.split("\n")
        refs = dict(l.split(" ") for l in heads if l)
        self.assertEqual(sorted(refs), ["refs/heads/main", "refs/heads/robot/2026-11-19-code-x"])
        self.assertNotEqual(refs["refs/heads/main"], refs["refs/heads/robot/2026-11-19-code-x"])   # main did not move
        self.assertEqual(info["files"], ["tests/example.test.mjs"])
        j["_baseline"] = {"tests": 10, "pass": 9, "fail": 1, "failing": ["old one"]}
        j["_tests"] = {"tests": 10, "pass": 8, "fail": 2, "failing": ["old one", "new one"]}
        rep = r.code_report(j, info)
        self.assertIn("New failures (fail now, passed on main): new one.", rep)
        self.assertIn("https://genie-git-robot-2026-11-19-code-x-agentwork.vercel.app", rep)
        self.assertIn("main was not touched", rep)

    def test_finish_refuses_forbidden_and_main(self):
        make_repo()
        j = r.parse_brief(BRIEF)
        sh("git", "-C", r.CODE_WORK, "checkout", "-q", "-b", j["branch"])
        with open(os.path.join(r.CODE_WORK, "supabase", "x.sql"), "w") as f:   # written around the tool guard
            f.write("drop table x;")
        info = r.finish_code(j)
        self.assertFalse(info["pushed"])
        self.assertIn("REFUSED TO PUSH: forbidden paths", info["note"])
        make_repo()
        j2 = r.parse_brief(BRIEF)
        j2["branch"] = "main"
        r.tool_write(r.parse_brief(BRIEF), "lib/a.ts", "x")
        info = r.finish_code(j2)
        self.assertFalse(info["pushed"])
        self.assertIn("bad branch name", info["note"])
        make_repo()
        self.assertEqual(r.finish_code(r.parse_brief(BRIEF))["note"], "no file was changed, nothing pushed")

    def test_sonnet_cache_price(self):
        m = r.Meter("sonnet")
        m.add({"input_tokens": 1000, "cache_read_input_tokens": 100000, "cache_creation_input_tokens": 2000, "output_tokens": 500})
        self.assertAlmostEqual(m.cost, (1000 * 2.0 + 100000 * 0.10 + 2000 * 2.5 + 500 * 10.0) / 1e6)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
