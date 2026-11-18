#!/usr/bin/env python3
"""One-off builder: DIRECTORY_MASTER_LIST.md -> data/directories.json (TODO rows only, best first).
Usage: python3 build_list.py <path to DIRECTORY_MASTER_LIST.md> <out.json>"""
import json, re, sys

SKIP_NAMES = {"dang.ai", "magicbox", "toolpilot ai", "toolpilot", "saasaffiliate.net", "slashdot", "appsumo marketplace"}
SKIP_TYPES = {"affiliate-dir"}
FIRST = ["futuretools", "aitoolsdirectory.com", "ai tools by neil patel", "creator economy tools", "startories",
         "sideprojectstack", "toolfinder", "altern.ai"]


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def norm_url(u):
    u = re.sub(r"\s*\(.*$", "", u.strip())
    u = u.split(" ")[0]
    if not u or u.startswith("not given"):
        return ""
    return u if u.startswith("http") else "https://" + u


def build(md):
    rows, section = [], ""
    for line in md.splitlines():
        if line.startswith("#"):
            section = line
        if not line.startswith("|"):
            continue
        c = cells(line)
        if len(c) == 10 and c[-1].upper().startswith("TODO") and "(after" not in c[-1]:
            name, url, typ, free, note = c[1], norm_url(c[2]), c[3], c[4], c[8]
            rows.append({"name": name, "submit_url": url, "type": typ, "free": free[:40], "notes": note[:200]})
        elif len(c) == 6 and c[4].upper() == "TODO" and re.match(r"G\d+$", c[0]):
            if c[3] in SKIP_TYPES:
                continue
            rows.append({"name": c[1], "submit_url": norm_url(c[2]), "type": c[3], "free": "unknown", "notes": c[5][:200]})
    out, seen = [], set()
    for r in rows:
        key = r["name"].lower()
        if key in SKIP_NAMES or not r["submit_url"] or key in seen:
            continue
        if "producthunt.com" in r["submit_url"] or r["submit_url"] == "https://neilpatel.com":
            continue  # a Product Hunt page is not a submit form
        seen.add(key)
        out.append(r)

    def rank(r):
        k = r["name"].lower()
        for i, f in enumerate(FIRST):
            if f in k:
                return (0, i)
        free_yes = r["free"].lower().startswith("yes")
        return (1 if free_yes else 2, 0)
    out.sort(key=rank)
    return out


if __name__ == "__main__":
    data = build(open(sys.argv[1], encoding="utf-8").read())
    json.dump(data, open(sys.argv[2], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(len(data), "directories")
