"""Shared helpers for the outbox sender and the inbox reader (PUBLIC repo: never print
recipients, subjects or bodies; counts and error TYPE names only)."""
import datetime, os, re, subprocess, shutil

BRANCH = "ops-outbox"
PRIVATE = "imran0786/genie"
HOST = os.environ.get("IMAP_HOST", "mail.privateemail.com")
SMTP_HOST = os.environ.get("SMTP_HOST", "mail.privateemail.com")
USER = os.environ.get("CONTACT_EMAIL", "contact@inayaflow.ai")
WORK = os.environ.get("OPS_WORK", "/tmp/ops_outbox")


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp():
    return now().strftime("%Y%m%dT%H%M%SZ")


def git(*args, check=True):
    r = subprocess.run(["git", "-C", WORK, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed" % args[0])
    return r


def clone():
    """Clone branch ops-outbox of the private repo (create it, empty, if missing)."""
    tok = os.environ.get("GENIE_LOGS_TOKEN")
    if not tok:
        raise RuntimeError("no GENIE_LOGS_TOKEN")
    shutil.rmtree(WORK, ignore_errors=True)
    url = "https://x-access-token:%s@github.com/%s.git" % (tok, PRIVATE)
    r = subprocess.run(["git", "clone", "-q", "--branch", BRANCH, "--depth", "1", url, WORK],
                       capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", url, WORK], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("clone failed")
        git("checkout", "-q", "--orphan", BRANCH)
        git("rm", "-rqf", ".", check=False)
    git("config", "user.name", "inayaflow-bot")
    git("config", "user.email", "contact@inayaflow.ai")


def commit_push(msg):
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    git("commit", "-q", "-m", msg)
    for _ in range(3):
        if git("push", "-q", "origin", BRANCH, check=False).returncode == 0:
            return True
        git("pull", "-q", "--rebase", "origin", BRANCH, check=False)
    raise RuntimeError("push failed")


def safe_id(s):
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,80}", s or ""))


def find_sent_folder(imap):
    names = []
    typ, data = imap.list()
    for line in data or []:
        s = line.decode("utf-8", "replace")
        m = re.match(r'\((?P<fl>[^)]*)\)\s+"?(?P<sep>[^"\s]+)"?\s+(?P<name>.+)$', s)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if "\\Sent" in m.group("fl"):
            return name
        names.append(name)
    for cand in ("Sent", "INBOX.Sent", "Sent Items", "Sent Messages"):
        if cand in names:
            return cand
    return "Sent"
