"""Unit tests (no network): python3 scripts/directory_verify/test_verify.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "directory_agent"))
import verify as v
import agent as a

K = {"fivetaco.com", "bufferapps.com", "opensaas.directory", "aiagentslive.com", "opentools.ai"}
n = 0


def ok(cond, name):
    global n
    n += 1
    if not cond:
        print("FAIL:", name)
        sys.exit(1)


ok(v.reg_domain("verify.opentools.ai") == "opentools.ai", "reg domain")
ok(v.sender_domain("OpenTools <noreply@verify.opentools.ai>") == "opentools.ai", "sender domain")
# own-domain confirm link is opened; off-domain and junk links never
t = ('<a href="https://fivetaco.com/auth/confirm?token=abc&amp;x=1">Confirm</a> <a href="https://evil.example/confirm?token=1">x</a> '
     '<a href="https://fivetaco.com/unsubscribe?verify=1">u</a> <img src="https://fivetaco.com/logo.png">')
own, off = v.confirm_links(t, "fivetaco.com")
ok(own == ["https://fivetaco.com/auth/confirm?token=abc&x=1"], "own link only")
ok(off == ["https://evil.example/confirm?token=1"], "off link separated")
ok(v.confirm_links("http://fivetaco.com/confirm?token=1", "fivetaco.com")[0] == [], "http (not https) never opened")
ok(v.confirm_links("https://fivetaco.com/checkout?token=1 https://fivetaco.com/pricing?confirm=1", "fivetaco.com")[0] == [], "pay pages never opened")
ok(v.decide("Confirm your email address for FiveTaco", "fivetaco.com", t, K, "OTHER", 60) == ("OPEN_LINK", "https://fivetaco.com/auth/confirm?token=abc&x=1"), "open link")
ok(v.decide("Confirm your email", "fivetaco.com", t, K, "OTHER", 5)[0] == "WAIT", "fresh mail left to a live session")
ok(v.decide("Confirm your email", "evil.example", t, K, None, 60)[0] == "UNKNOWN_SENDER", "unknown sender untouched")
ok(v.decide("Hello there", "evil.example", t, K, None, 60, routed_verify=False)[0] == "IGNORE", "unrouted non-verify ignored")
ok(v.decide("Confirm your subscription to Open Saas Directory", "opensaas.directory", "https://opensaas.directory/confirm?token=1", K, None, 60)[0] == "SKIPPED_NEWSLETTER", "newsletter skipped")
ok(v.decide("Confirm your email", "aiagentslive.com", "https://aiagentslive.com/verify?token=1", K, "PAID", 60)[0] == "SITE_CLOSED", "paid site closed")
ok(v.decide("024449 is your AI Agents Live code", "aiagentslive.com", "Your code is 024449", K, "PAID", 60)[0] == "SITE_CLOSED", "code on paid site")
ok(v.decide("Login One Time Password", "bufferapps.com", "Your OTP is 123456", K, "SUBMITTED", 60)[0] == "SESSION_ONLY_SITE_FINISHED", "otp, site finished")
ok(v.decide("Login One Time Password", "bufferapps.com", "Your OTP is 123456", K, "OTHER", 60)[0] == "SESSION_ONLY_EXPIRED", "otp expired -> retry")
ok(v.decide("Login One Time Password", "bufferapps.com", "Your OTP is 123456", K, "OTHER", 25)[0] == "WAIT", "otp maybe in use")
ok(v.decide("Your sign-in link", "fivetaco.com", "https://fivetaco.com/auth/magic?token=1", K, None, 60)[0] == "SESSION_ONLY_EXPIRED", "magic link is session only")
ok(v.decide("Confirm your signup", "fivetaco.com", "https://abc.supabase.co/auth/v1/verify?token=1", K, None, 60)[0] == "LINK_OFF_DOMAIN", "off-domain link not opened")
SB = "Confirm your email https://abc.supabase.co/auth/v1/verify?token=xyz&type=signup&redirect_to=https://fivetaco.com/welcome"
ok(v.supabase_site(SB, K) == "fivetaco.com", "supabase confirm link maps back to the known directory")
ok(v.decide("Confirm your email address", "fivetaco.com", SB, K, None, 60)[0] == "OPEN_LINK", "supabase link back to the same site is opened")
ok(v.supabase_site(SB.replace("fivetaco.com", "evil.example"), K) == "", "supabase link to an unknown site is not trusted")
ok(v.decide("Confirm your email address", "fivetaco.com", SB.replace("fivetaco.com", "evil.example"), K, None, 60)[0] == "LINK_OFF_DOMAIN", "supabase link to another site stays closed")
ok(v.supabase_redirect_domain("https://abc.supabase.co/auth/v1/verify?token=1&redirect_to=http://fivetaco.com/") == "", "plain-http redirect is not trusted")
ok(v.decide("There's a problem with Your OpenTools Launch", "opentools.ai", "Please fix your listing. Year 2026.", K, None, 400)[0] == "NO_ACTION_FOUND", "problem mail -> human")
ok(v.decide("There's a problem with Your OpenTools Launch", "opentools.ai", "Your checkout was incomplete. Click here to complete checkout. Code for 20% off", K, None, 400)[0] == "SKIPPED_PAID_OFFER", "unpaid checkout mail closed, never opened")
ok(v.site_status_for("aiagentslive.com", {"aiagentslive": {"status": "PAID"}}) == "PAID", "state by short name")
ok(v.site_status_for("whatlaunched.today", {"whatlaunchedtoday": {"status": "SUBMITTED"}}) == "SUBMITTED", "state by squashed domain")
ok(v.decide("Your InayaFlow listing - one step left", "fivetaco.com", "One step left: add the badge to your site as a dofollow link. Or skip the queue with featured.", K, None, 400)[0] == "BADGE_ASK", "badge ask goes to a person")
ok(v.site_status_for("x.com", {}) is None, "no state")
ok(v.find_code("in 2026 your code is 024449") == "024449", "code, not a year")
s = v.scrub("go to https://x.com/confirm?token=SECRET code 123456 mail a@b.co")
ok("SECRET" not in s and "123456" not in s and "a@b.co" not in s and "[link:x.com]" in s, "scrub")
ok("SECRETTOKENVALUE12345" not in v.link_label("https://x.com/confirm/SECRETTOKENVALUE12345?token=zzz") and "zzz" not in v.link_label("https://x.com/c?token=zzz"), "link label hides tokens")
ok("fivetaco.com" in v.known_domains(agent_state={"fivetaco.com": {}, "fivetaco": {}}, extra="foo.io"), "known from state")

# agent: verify sweeper boost
site = {"name": "FiveTaco", "url": "https://fivetaco.com/submit"}
st = {"fivetaco": {"name": "FiveTaco", "status": "OTHER", "date": "2026-11-18", "tries": 1},
      "fivetaco.com": {"name": "FiveTaco", "status": "OTHER", "date": "2026-11-18", "tries": 1}}
ok(not a.eligible(site, st, {}, "2026-11-19"), "3-day wait without confirmation")
ok(a.eligible(site, st, {}, "2026-11-19", {"retry": {"fivetaco.com": "2026-11-18"}}), "confirmed account retried at once")
st3 = {k: dict(e, tries=3) for k, e in st.items()}
ok(not a.eligible(site, st3, {}, "2026-11-19", {"retry": {"fivetaco.com": "2026-11-18"}}), "boost is bounded (max 3 tries)")
stS = {k: dict(e, status="SUBMITTED") for k, e in st.items()}
ok(not a.eligible(site, stS, {}, "2026-11-19", {"retry": {"fivetaco.com": "2026-11-18"}}), "submitted never retried")
stP = {k: dict(e, status="PAID") for k, e in st.items()}
ok(not a.eligible(site, stP, {}, "2026-11-25", {"retry": {"fivetaco.com": "2026-11-24"}}), "paid never retried")
ok(not a.eligible(site, st, {}, "2026-11-19", {"retry": {"fivetaco.com": "2026-11-17"}}), "old confirmation gives no boost")
print("verify tests: %d passed" % n)
