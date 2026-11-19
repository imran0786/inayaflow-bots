"""OUTBOX: sends the JSON files in ops-outbox outbox/pending/ through Namecheap SMTP.
Public repo: stdout prints counts only."""
import imaplib, json, os, re, smtplib, ssl, sys, time
from email.message import EmailMessage
from email.utils import make_msgid, formatdate, parseaddr
import common as c

MAX_SENDS = 20
ADDR = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def validate(job, root):
    if not isinstance(job, dict) or not c.safe_id(job.get("id")):
        return "bad id"
    for k in ("attachments", "attachment", "files"):
        if job.get(k):
            return "attachments not allowed"
    to = job.get("to")
    cc = job.get("cc") or []
    bcc = job.get("bcc") or []
    if not isinstance(to, list) or not to or not isinstance(cc, list) or not isinstance(bcc, list):
        return "to must be a non-empty list"
    allr = to + cc + bcc
    if len(allr) > 10 or not all(isinstance(a, str) and ADDR.match(parseaddr(a)[1] or a) for a in allr):
        return "bad recipient"
    if not isinstance(job.get("subject"), str) or not job["subject"].strip() or "\n" in job["subject"]:
        return "bad subject"
    body = job.get("body")
    if not isinstance(body, str) or not body.strip():
        return "empty body"
    if "google.com/url" in body:
        return "body contains google.com/url"
    for k in ("in_reply_to", "references"):
        v = job.get(k)
        if v is not None and (not isinstance(v, str) or "\n" in v):
            return "bad " + k
    if os.path.exists(os.path.join(root, "outbox", "sent", job["id"] + ".json")):
        return "duplicate id (already sent)"
    return None


def build(job):
    m = EmailMessage()
    m["From"] = "InayaFlow <%s>" % c.USER
    m["To"] = ", ".join(job["to"])
    if job.get("cc"):
        m["Cc"] = ", ".join(job["cc"])
    m["Subject"] = job["subject"].strip()
    m["Date"] = formatdate(localtime=False)
    mid = make_msgid(domain="inayaflow.ai")
    m["Message-ID"] = mid
    irt = (job.get("in_reply_to") or "").strip()
    refs = (job.get("references") or "").strip()
    if irt:
        m["In-Reply-To"] = irt
        m["References"] = refs if irt in refs else (refs + " " + irt).strip()
    elif refs:
        m["References"] = refs
    m.set_content(job["body"])
    return m, mid


def main():
    pw = os.environ.get("CONTACT_EMAIL_PASSWORD")
    if not pw:
        print("outbox: no password")
        return 2
    c.clone()
    root = c.WORK
    pend = os.path.join(root, "outbox", "pending")
    files = sorted(f for f in os.listdir(pend) if f.endswith(".json")) if os.path.isdir(pend) else []
    if not files:
        print("outbox: pending=0")
        return 0
    for d in ("sent", "failed"):
        os.makedirs(os.path.join(root, "outbox", d), exist_ok=True)
    sent = failed = deferred = 0
    imap = None
    sent_folder = None

    def fail(fn, why, job=None):
        with open(os.path.join(root, "outbox", "failed", fn), "w", encoding="utf-8") as fh:
            json.dump({"error": why, "failed_at": c.stamp(), "job": job}, fh, indent=2, ensure_ascii=False)
        os.remove(os.path.join(pend, fn))
        c.commit_push("outbox: failed")

    for fn in files:
        path = os.path.join(pend, fn)
        try:
            job = json.load(open(path, encoding="utf-8"))
        except Exception:
            fail(fn, "invalid JSON")
            failed += 1
            continue
        bad = validate(job, root)
        if bad:
            fail(fn, bad, job)
            failed += 1
            continue
        if sent >= MAX_SENDS:
            deferred += 1
            continue
        msg, mid = build(job)
        rcpt = [parseaddr(a)[1] or a for a in job["to"] + (job.get("cc") or []) + (job.get("bcc") or [])]
        try:
            with smtplib.SMTP_SSL(c.SMTP_HOST, 465, context=ssl.create_default_context(), timeout=60) as s:
                s.login(c.USER, pw)
                s.send_message(msg, to_addrs=rcpt)
        except Exception as e:
            fail(fn, "send failed: %s %s" % (type(e).__name__, str(e)[:300].replace(pw, "***")), job)
            failed += 1
            continue
        try:
            if imap is None:
                imap = imaplib.IMAP4_SSL(c.HOST, 993)
                imap.login(c.USER, pw)
                sent_folder = c.find_sent_folder(imap)
            imap.append('"%s"' % sent_folder, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            in_sent = True
        except Exception:
            in_sent = False
        job.update({"sent_at": c.stamp(), "message_id": mid, "copy_in_sent_folder": in_sent})
        with open(os.path.join(root, "outbox", "sent", fn), "w", encoding="utf-8") as fh:
            json.dump(job, fh, indent=2, ensure_ascii=False)
        os.remove(path)
        sent += 1
        c.commit_push("outbox: sent")  # push after every send so a crash cannot cause a resend
    if imap:
        try:
            imap.logout()
        except Exception:
            pass
    print("outbox: sent=%d failed=%d deferred=%d" % (sent, failed, deferred))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("outbox: ERROR %s" % type(e).__name__)
        sys.exit(1)
