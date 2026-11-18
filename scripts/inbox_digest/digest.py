"""Read-only digest of the contact@ Namecheap mailbox (INBOX + Junk/Spam).

Writes ops/inbox_digest.md (date, folder, sender, subject, read/unread, tag) for the last
HOURS hours. Opens folders read-only and fetches headers with BODY.PEEK, so nothing is
marked as read and nothing is changed. PUBLIC repo: stdout prints counts only.
"""
import datetime, email, imaplib, os, re, sys
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

HOST = os.environ.get("IMAP_HOST", "mail.privateemail.com")
USER = os.environ.get("CONTACT_EMAIL", "contact@inayaflow.ai")
HOURS = int(os.environ.get("DIGEST_HOURS", "72"))
OUT = os.environ.get("DIGEST_OUT", "ops/inbox_digest.md")
VERIFY_RE = re.compile(r"verif|confirm|activat|magic link|sign.?in link|log.?in link|code|welcome|account", re.I)
LISTING_RE = re.compile(r"approv|listed|is live|published|submission|launch|review|reject", re.I)


def dec(v):
    try:
        return str(make_header(decode_header(v or ""))).replace("|", "/").replace("\n", " ").strip()
    except Exception:
        return (v or "").replace("|", "/").strip()


def tag(subject, sender):
    s = subject + " " + sender
    if LISTING_RE.search(s):
        return "LISTING"
    if VERIFY_RE.search(s):
        return "VERIFY/ACCOUNT"
    return ""


def folders(imap):
    names = ["INBOX"]
    typ, data = imap.list()
    for raw in data or []:
        line = raw.decode(errors="ignore")
        m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
        if m and re.search(r"junk|spam", m.group(1), re.I):
            names.append(m.group(1))
    return names


def rows_for(imap, folder, since_dt):
    rows = []
    if imap.select('"%s"' % folder, readonly=True)[0] != "OK":
        return rows
    typ, data = imap.search(None, "SINCE", since_dt.strftime("%d-%b-%Y"))
    ids = (data[0] or b"").split()
    for i in ids[-300:]:
        typ, msg = imap.fetch(i, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK" or not msg or not isinstance(msg[0], tuple):
            continue
        flags = msg[0][0].decode(errors="ignore")
        h = email.message_from_bytes(msg[0][1])
        try:
            d = parsedate_to_datetime(h.get("Date")).astimezone(datetime.timezone.utc)
        except Exception:
            continue
        if d < since_dt:
            continue
        frm, subj = dec(h.get("From")), dec(h.get("Subject"))
        rows.append((d, folder, frm, subj, "read" if "\Seen" in flags else "UNREAD", tag(subj, frm)))
    return rows


def main():
    pw = os.environ.get("CONTACT_EMAIL_PASSWORD")
    if not pw:
        print("digest: no password in env"); return 2
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=HOURS)
    imap = imaplib.IMAP4_SSL(HOST, 993)
    try:
        imap.login(USER, pw)
    except Exception:
        print("digest: IMAP LOGIN FAILED"); return 3
    rows = []
    for f in folders(imap):
        rows += rows_for(imap, f, since)
    imap.logout()
    rows.sort(key=lambda r: r[0], reverse=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("# contact@ inbox digest (Namecheap, read-only)\n\nUpdated %s. Last %d hours. Nothing is marked read.\n\n" % (now, HOURS))
        fh.write("| date (UTC) | folder | from | subject | status | tag |\n|---|---|---|---|---|---|\n")
        for d, f, frm, subj, st, t in rows:
            fh.write("| %s | %s | %s | %s | %s | %s |\n" % (d.strftime("%m-%d %H:%M"), f, frm, subj, st, t))
    n_ver = sum(1 for r in rows if r[5] == "VERIFY/ACCOUNT")
    n_lst = sum(1 for r in rows if r[5] == "LISTING")
    print("digest: messages=%d verify_or_account=%d listing=%d" % (len(rows), n_ver, n_lst))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("digest: ERROR %s" % type(e).__name__); sys.exit(1)
