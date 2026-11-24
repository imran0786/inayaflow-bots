"""READER: fetches full messages (INBOX + Junk, read-only, BODY.PEEK) matching request files in
ops-outbox reader/requests/ and writes reader/results/<id>.md in the PRIVATE repo.
Public repo: stdout prints counts only."""
import datetime, email, html, imaplib, json, os, re, sys
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
import common as c

MAX_MSGS = 40
MAX_BODY = 20000


def dec(v):
    try:
        return str(make_header(decode_header(v or ""))).replace("\n", " ").strip()
    except Exception:
        return v or ""


def body_text(msg):
    plain = html_part = None
    for p in msg.walk():
        if p.get_content_maintype() != "text" or p.get_filename():
            continue
        try:
            t = p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if p.get_content_subtype() == "plain" and plain is None:
            plain = t
        elif p.get_content_subtype() == "html" and html_part is None:
            html_part = t
    if plain and (html_part is None or "http" in plain):
        return plain
    if html_part:
        t = re.sub(r"(?is)<(script|style).*?</\1>", "", html_part)
        t = re.sub(r"""(?is)<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", r"\2 [\1]", t)   # 24 Nov: keep link targets (confirm links)
        t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        return re.sub(r"\n{3,}", "\n\n", html.unescape(t)).strip()
    return "(no text body)"


def folders(imap):
    names = ["INBOX"]
    typ, data = imap.list()
    for raw in data or []:
        line = raw.decode(errors="ignore")
        m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
        if m and re.search(r"junk|spam", m.group(1), re.I):
            names.append(m.group(1))
    return names


def crit(s):
    return s.replace('"', "").replace("\\", "")


def run_request(imap, req):
    s = req.get("search") or {}
    parts = []
    if s.get("since"):
        d = datetime.date.fromisoformat(s["since"])
        parts += ["SINCE", d.strftime("%d-%b-%Y")]
    if s.get("from"):
        parts += ["FROM", '"%s"' % crit(s["from"])]
    if s.get("subject"):
        parts += ["SUBJECT", '"%s"' % crit(s["subject"])]
    if not parts:
        raise ValueError("search needs from, subject or since")
    out = []
    for f in folders(imap):
        if imap.select('"%s"' % f, readonly=True)[0] != "OK":
            continue
        typ, data = imap.search(None, *parts)
        for i in (data[0] or b"").split():
            typ, msg = imap.fetch(i, "(BODY.PEEK[])")
            if typ != "OK" or not msg or not isinstance(msg[0], tuple):
                continue
            m = email.message_from_bytes(msg[0][1])
            try:
                d = parsedate_to_datetime(m.get("Date")).astimezone(datetime.timezone.utc)
            except Exception:
                d = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
            out.append((d, f, m))
    out.sort(key=lambda x: x[0])
    return out[-MAX_MSGS:]


def main():
    pw = os.environ.get("CONTACT_EMAIL_PASSWORD")
    if not pw:
        print("reader: no password")
        return 2
    c.clone()
    root = c.WORK
    rq = os.path.join(root, "reader", "requests")
    files = sorted(f for f in os.listdir(rq) if f.endswith(".json")) if os.path.isdir(rq) else []
    if not files:
        print("reader: requests=0")
        return 0
    for d in ("results", "done"):
        os.makedirs(os.path.join(root, "reader", d), exist_ok=True)
    imap = imaplib.IMAP4_SSL(c.HOST, 993)
    try:
        imap.login(c.USER, pw)
    except Exception:
        print("reader: IMAP LOGIN FAILED")
        return 3
    done = failed = msgs = 0
    for fn in files:
        path = os.path.join(rq, fn)
        try:
            req = json.load(open(path, encoding="utf-8"))
            rid = req.get("id") or fn[:-5]
            if not c.safe_id(rid):
                raise ValueError("bad id")
            res = run_request(imap, req)
            lines = ["# Reader result: %s\n\nRun %s. Request: `%s`\n\n%d message(s) (INBOX + Junk, read-only, nothing marked read).\n"
                     % (rid, c.stamp(), json.dumps(req.get("search")), len(res))]
            for d, f, m in res:
                lines.append("\n---\n\n## %s | %s\n\n- Folder: %s\n- From: %s\n- To: %s\n- Date: %s\n- Message-ID: %s\n- In-Reply-To: %s\n- References: %s\n\n%s\n"
                             % (d.strftime("%Y-%m-%d %H:%M UTC"), dec(m.get("Subject")), f, dec(m.get("From")), dec(m.get("To")),
                                m.get("Date", ""), (m.get("Message-ID") or "").strip(), (m.get("In-Reply-To") or "").strip(),
                                " ".join((m.get("References") or "").split()), body_text(m)[:MAX_BODY].strip()))
            with open(os.path.join(root, "reader", "results", rid + ".md"), "w", encoding="utf-8") as fh:
                fh.write("".join(lines))
            msgs += len(res)
            done += 1
        except Exception as e:
            with open(os.path.join(root, "reader", "results", fn[:-5] + ".md"), "w", encoding="utf-8") as fh:
                fh.write("# Reader FAILED\n\nError: %s %s\n" % (type(e).__name__, str(e)[:200]))
            failed += 1
        os.replace(path, os.path.join(root, "reader", "done", fn))
    imap.logout()
    c.commit_push("reader: results")
    print("reader: requests=%d done=%d failed=%d messages=%d" % (len(files), done, failed, msgs))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("reader: ERROR %s" % type(e).__name__)
        sys.exit(1)
