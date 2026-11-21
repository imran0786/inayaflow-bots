"""Tests for fm_app_read.cjs + policy.compare_app_read: the robot's reading of a guide's frontmatter must be the
reading of the app's own parser (gray-matter + js-yaml), or the guide is refused (QA round 2, point 1).
Needs node and the `gray-matter` package (version 4.0.3 like the app): set NODE_PATH to a node_modules that has it.
In the workflow REQUIRE_TS=1 makes a missing parser a failure instead of a skip.
Run: NODE_PATH=/path/to/node_modules python3 scripts/deploy_robot/test_fm_app_read.py"""
import datetime, json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import policy as P

READER = os.path.join(HERE, "fm_app_read.cjs")
CFG = P.load_config()
TODAY = datetime.date(2026, 11, 21)
LF = chr(10)


def have_parser():
    try:
        r = subprocess.run(["node", "-e", "process.stdout.write(require('gray-matter/package.json').version)"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    except OSError:
        return ""


VERSION = have_parser()
if not VERSION and os.environ.get("REQUIRE_TS") == "1":
    print("FAILED: node or the gray-matter package is missing, the app-reading check was NOT tested")
    sys.exit(1)

BODY = ("Real words about scheduling. " * 30) + LF
GOOD = LF.join(["---", 'title: "How to schedule TikTok posts in TikTok Studio"', 'description: "Step by step."', 'slug: "new-guide"',
                'date: "2026-11-21"', 'tool: "analyze"', "faq:", '  - question: "Can I?"', "    answer: 'Yes, it''s fine.'", "---", ""]) + BODY

# Fable's exact sample (QA round 2): js-yaml reads the INDENTED lines as the real keys; the column-0 lines sit inside a quoted value.
FABLE_SAMPLE = LF.join([
    "---",
    '  slug: "tiktok-seo"',
    '  title: "Any other title here"',
    '  date: "2020-01-01"',
    '  tool: "analyze"',
    "  description: 'x",
    'title: "Title the robot reads"',
    'date: "2026-11-21"',
    'slug: "new-guide"',
    "  y'",
    "---",
    ""]) + BODY


@unittest.skipUnless(VERSION, "node + gray-matter not available here (the workflow installs them)")
class AppRead(unittest.TestCase):
    def read(self, text):
        d = tempfile.mkdtemp(prefix="fmread_")
        try:
            with open(os.path.join(d, "g0.new"), "w", encoding="utf-8", newline="") as f:
                f.write(text)
            with open(os.path.join(d, "fm_manifest.json"), "w", encoding="utf-8") as f:
                json.dump([{"path": "content/blog/new-guide.mdx", "file": "g0.new"}], f)
            r = subprocess.run(["node", READER, d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            return json.loads(r.stdout)["files"]["content/blog/new-guide.mdx"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def compare(self, text):
        fm, _body = P.split_frontmatter(text)
        return P.compare_app_read("content/blog/new-guide.mdx", fm or "", self.read(text))

    def test_parser_is_the_apps_version(self):
        self.assertEqual(VERSION, "4.0.3")

    def test_plain_guide_reads_the_same(self):
        app = self.read(GOOD)
        self.assertTrue(app["ok"])
        self.assertEqual(app["values"]["slug"], {"type": "string", "value": "new-guide"})
        self.assertEqual(sorted(app["keys"]), ["date", "description", "faq", "slug", "title", "tool"])
        self.assertEqual(self.compare(GOOD), [])
        self.assertEqual(P.check_guide("content/blog/new-guide.mdx", None, GOOD, TODAY, CFG)[0], [])

    def test_fable_sample_the_app_reads_other_keys_than_the_robot(self):
        app = self.read(FABLE_SAMPLE)
        self.assertTrue(app["ok"], app)
        # what the APP would publish:
        self.assertEqual(app["values"]["slug"]["value"], "tiktok-seo")
        self.assertEqual(app["values"]["title"]["value"], "Any other title here")
        self.assertEqual(app["values"]["date"]["value"], "2020-01-01")
        # what the robot's line reader saw:
        fm, _ = P.split_frontmatter(FABLE_SAMPLE)
        self.assertEqual((P.fm_value(fm, "slug")[1], P.fm_value(fm, "title")[1], P.fm_value(fm, "date")[1]), ("new-guide", "Title the robot reads", "2026-11-21"))
        # the comparison alone refuses it (slug, title, date, description, and the key set all differ) ...
        reasons = self.compare(FABLE_SAMPLE)
        for key in ("slug", "title", "date", "description"):
            self.assertTrue(any("the app reads %s = " % key in r for r in reasons), (key, reasons))
        # ... and so does the line rule (first line not at column 0), before any container is started
        self.assertTrue(any("must begin with" in r for r in P.check_guide("content/blog/new-guide.mdx", None, FABLE_SAMPLE, TODAY, CFG)[0]))

    def test_same_trick_with_a_column_zero_first_line(self):
        """Variants that pass the first-line rule: whatever YAML makes of them, robot and app must agree or it is refused."""
        variants = [
            GOOD.replace('description: "Step by step."', "description: 'x" + LF + '  slug: "tiktok-seo"' + LF + "  y'"),
            GOOD.replace('tool: "analyze"', 'tool: "analyze"' + LF + "extra:" + LF + '  slug: "tiktok-seo"'),
            GOOD.replace('slug: "new-guide"', "slug: new-guide # really"),
            GOOD.replace('title: "How to schedule TikTok posts in TikTok Studio"', "title: 12345678901234"),
            GOOD.replace('title: "How to schedule TikTok posts in TikTok Studio"', "title: yes it is a long enough title"),
            GOOD.replace('date: "2026-11-21"', "date: 2026-11-21T23:30:00-05:00"),
            GOOD.replace('description: "Step by step."', "description: Step by step. # hidden comment"),
            GOOD.replace('description: "Step by step."', 'description: "Step by step."' + LF + '"slug": "tiktok-seo"'),
        ]
        for i, text in enumerate(variants):
            self.assertNotEqual(text, GOOD, i)
            app = self.read(text)
            fm, _ = P.split_frontmatter(text)
            line_rules = P.check_guide("content/blog/new-guide.mdx", None, text, TODAY, CFG)[0]
            differ = P.compare_app_read("content/blog/new-guide.mdx", fm or "", app)
            agree = app.get("ok") and all(
                (P.fm_value(fm, k)[0] == 0 and app["values"][k]["type"] == "missing") or app["values"][k]["value"] == P.fm_value(fm, k)[1]
                for k in P.APP_KEYS) and sorted(app["keys"]) == sorted(P.top_level_keys(fm))
            # the property that matters: a guide is let through ONLY when the app reads exactly what the robot read
            if not (line_rules or differ):
                self.assertTrue(agree, (i, app))
        # and these, concretely, are refused by the comparison itself
        self.assertTrue(self.compare(variants[0]))          # multi-line quote: the app's description is not the robot's
        self.assertTrue(self.compare(variants[2]))          # "# really" is a YAML comment: the app's slug is new-guide, the robot read more
        self.assertTrue(self.compare(variants[3]))          # a number, not text
        self.assertTrue(self.compare(variants[6]))

    def test_unquoted_date_is_accepted_as_the_same_day(self):
        text = GOOD.replace('date: "2026-11-21"', "date: 2026-11-21")
        app = self.read(text)
        self.assertEqual(app["values"]["date"], {"type": "date", "value": "2026-11-21"})
        self.assertEqual(self.compare(text), [])

    def test_code_engines_and_broken_yaml_are_not_ok(self):
        self.assertFalse(self.read(GOOD.replace("---", "---js", 1))["ok"])
        self.assertFalse(self.read("no frontmatter at all" + LF)["ok"])
        dup = GOOD.replace('tool: "analyze"', 'tool: "analyze"' + LF + 'title: "Second title here"')
        self.assertFalse(self.read(dup)["ok"])               # js-yaml refuses a duplicated key
        self.assertTrue(self.compare(dup))
        marker = os.path.join(tempfile.gettempdir(), "fm_app_read_ran_code")
        if os.path.exists(marker):
            os.remove(marker)
        js = "---js" + LF + "{ title: require('fs').writeFileSync(" + json.dumps(marker) + ", 'x') }" + LF + "---" + LF + BODY
        self.assertFalse(self.read(js)["ok"])
        self.assertFalse(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main(verbosity=1)
