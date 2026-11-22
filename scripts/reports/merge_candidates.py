#!/usr/bin/env python3
"""Merge two versions of data/directories_candidates.json after a push conflict (23 Nov 2026).

usage: merge_candidates.py OURS_FILE THEIRS_FILE   -> writes the union into THEIRS_FILE
Rows are matched by domain. A domain in both: OUR row wins (it was checked later, in the run that just finished), but a status that
starts with "TRIED" or "SUBMITTED" on THEIR side is never overwritten by a plainer one. Rows only in one file are kept. Nothing is lost.
"""
import json
import sys


def merge(ours, theirs):
    out = dict(theirs)
    by = {}
    for s in theirs.get("sites", []):
        by[(s.get("domain") or s.get("submit_url") or s.get("name"))] = s
    for s in ours.get("sites", []):
        k = s.get("domain") or s.get("submit_url") or s.get("name")
        old = by.get(k)
        if old is not None and str(old.get("status", "")).startswith(("TRIED", "SUBMITTED")) and not str(s.get("status", "")).startswith(("TRIED", "SUBMITTED")):
            continue
        by[k] = s
    out["sites"] = list(by.values())
    out["generated"] = max(str(ours.get("generated", "")), str(theirs.get("generated", "")))
    return out


if __name__ == "__main__":
    o = json.load(open(sys.argv[1], encoding="utf-8"))
    t = json.load(open(sys.argv[2], encoding="utf-8"))
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(merge(o, t), f, indent=1, ensure_ascii=False)
        f.write("\n")
