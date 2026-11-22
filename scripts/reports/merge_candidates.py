#!/usr/bin/env python3
"""Merge two versions of data/directories_candidates.json after a push conflict (23 Nov 2026).

usage: merge_candidates.py OURS_FILE THEIRS_FILE   -> writes the union into THEIRS_FILE
Rows are matched by domain. A domain in both: an UNCHECKED row never beats a checked row; otherwise the row with the newer "checked" date wins;
on a tie a TRIED / SUBMITTED status wins; then OUR row. Rows only in one file are kept. Nothing is lost.
"""
import json
import sys


def _pick(a, b):
    """The row to keep when both files have the domain (a = ours, b = theirs)."""
    ua, ub = str(a.get("status", "")).startswith("UNCHECKED"), str(b.get("status", "")).startswith("UNCHECKED")
    if ua != ub:
        return b if ua else a
    ca, cb = str(a.get("checked", "")), str(b.get("checked", ""))
    if ca != cb:
        return a if ca > cb else b
    ta, tb = str(a.get("status", "")).startswith(("TRIED", "SUBMITTED")), str(b.get("status", "")).startswith(("TRIED", "SUBMITTED"))
    return b if tb and not ta else a


def merge(ours, theirs):
    out = dict(theirs)
    by = {}
    for s in theirs.get("sites", []):
        by[(s.get("domain") or s.get("submit_url") or s.get("name"))] = s
    for s in ours.get("sites", []):
        k = s.get("domain") or s.get("submit_url") or s.get("name")
        old = by.get(k)
        by[k] = s if old is None else _pick(s, old)
    out["sites"] = list(by.values())
    out["generated"] = max(str(ours.get("generated", "")), str(theirs.get("generated", "")))
    return out


if __name__ == "__main__":
    o = json.load(open(sys.argv[1], encoding="utf-8"))
    t = json.load(open(sys.argv[2], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(merge(o, t), f, indent=1, ensure_ascii=False)
        f.write("\n")
