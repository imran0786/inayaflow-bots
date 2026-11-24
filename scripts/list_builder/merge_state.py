#!/usr/bin/env python3
"""Merge two versions of data/list_builder_state.json after a push conflict (24 Nov 2026, code QA finding 3a).

usage: merge_state.py OURS_FILE THEIRS_FILE   -> writes the merge into THEIRS_FILE (one provider's own state file; the provider is read from the file name), then
rebuilds that provider's sign-up sheet and daily report and the side-by-side compare file from the merged states (so they never disagree).
Each provider has its own state file now, so this only guards against a second run of the SAME provider.

Nothing a robot booked is lost: the cursor is the larger one; every day's spend (Sonnet and DeepSeek) is the larger of the two values (a spend
booking only ever grows within a day); "rejected" is the union; every per-day counter, per provider, is the larger of the two (two runs never see
each other's counters, so max is the safe side). The summary of the day comes from OURS when we ran more calls that day, else THEIRS.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import builder as b  # noqa: E402


def _max_dict(a, c):
    out = dict(a or {})
    for k, v in (c or {}).items():
        if isinstance(v, (int, float)) and isinstance(out.get(k), (int, float)):
            out[k] = max(out[k], v)
        else:
            out.setdefault(k, v)
    return out


def merge_day(a, t):
    out = dict(t)
    for k in ("added", "runs", "calls"):
        out[k] = max(int(a.get(k, 0) or 0), int(t.get(k, 0) or 0))
    out["by_category"] = _max_dict(a.get("by_category"), t.get("by_category"))
    prov = {}
    for p in set((a.get("providers") or {})) | set((t.get("providers") or {})):
        prov[p] = _max_dict((a.get("providers") or {}).get(p), (t.get("providers") or {}).get(p))
    if prov:
        out["providers"] = prov
    mine = (a.get("calls", 0) or 0) >= (t.get("calls", 0) or 0)
    for k in ("summary", "category_summary", "total"):
        src = a if mine else t
        if k in src:
            out[k] = src[k]
    return out


def merge_state(ours, theirs):
    out = dict(theirs)
    out["cursor"] = max(int(ours.get("cursor", 0) or 0), int(theirs.get("cursor", 0) or 0))
    for key in ("spent",):
        out[key] = _max_dict(ours.get(key), theirs.get(key))
    out["days"] = {}
    for d in set((ours.get("days") or {})) | set((theirs.get("days") or {})):
        a, t = (ours.get("days") or {}).get(d), (theirs.get("days") or {}).get(d)
        out["days"][d] = merge_day(a, t) if a and t else dict(a or t)
    out["rejected"] = list(dict.fromkeys(list(theirs.get("rejected") or []) + list(ours.get("rejected") or [])))[-b.REJECTED_KEPT:]
    if ours.get("last_summary") and (ours.get("last_summary") != theirs.get("last_summary")):
        out["last_summary"] = ours["last_summary"] if max(ours.get("days") or {"": 0}) >= max(theirs.get("days") or {"": 0}) else theirs.get("last_summary")
    return out


def main(argv):
    ours = b.read_json(argv[1], {})
    theirs_path = argv[2]
    theirs = b.read_json(theirs_path, {})
    merged = merge_state(ours if isinstance(ours, dict) else {}, theirs if isinstance(theirs, dict) else {})
    b.write_json(theirs_path, merged)
    prov = "deepseek" if "deepseek" in os.path.basename(theirs_path) else "claude"      # the provider is read from the state file name
    P = b.paths(prov)
    cand = b.read_json(P["cand"], None)
    if isinstance(cand, dict) and isinstance(cand.get("sites"), list):
        b.write_needs_signup(cand["sites"], P["signup"])
    b.write_report(prov, merged.get("days") or {}, P["report"])
    other = b.read_json(P["other_state"], {})
    b.write_compare({prov: merged.get("days") or {}, ("claude" if prov == "deepseek" else "deepseek"): (other.get("days") if isinstance(other, dict) else {}) or {}})
    print("list-builder: state merged (cursor %d, %d days)" % (merged["cursor"], len(merged["days"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
