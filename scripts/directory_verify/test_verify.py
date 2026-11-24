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
ok(v.HOURS >= 336, "lookback covers at least 14 days")
ok(v.decide("Confirm your email", "buildlist.io", "Confirm that you want to receive our weekly newsletter: https://buildlist.io/confirm?token=1", K | {"buildlist.io"}, None, 60)[0] == "SKIPPED_NEWSLETTER", "newsletter wording in body skipped")
ok(v.decide("Confirm your email", "buildlist.io", "Welcome! Confirm your account: https://buildlist.io/confirm?token=1", K | {"buildlist.io"}, None, 9000)[0] == "OPEN_LINK", "5-day-old confirm link still opened")
# 24 Nov 2026: mail follow-through fixes
import json as _json
KD = v.known_domains(root=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
for _d in ("aidirs.best", "agenthunter.io", "opentools.ai", "shipgrowth.dev", "launchpadindia.org", "findstack.com", "tinystartups.co", "diigo.com",
           "hackmd.io", "codepen.io", "hackernoon.com", "devsuite.co", "sitepatent.com", "aiagentslisting.com", "tinylaunchpad.com",
           "directree.io", "earlyhunt.com", "saashub.com", "shipboost.io", "about.me"):
    ok(_d in KD, "signup domain known: " + _d)
for _d in ("github.com", "gitlab.com", "google.com", "openai.com", "supabase.io"):
    ok(_d not in KD, "never a sign-up site: " + _d)
# known_sites.json and own_backlinks.json sources count too
ok("awesomeindie.com" in KD and "inayaflow.blogspot.com" not in KD and "blogspot.com" in KD, "known_sites + own_backlinks feed the list")
# invisible characters before / inside the link
ZW = "​‌⁠﻿"
AH = '<p>Hi</p>' + ZW + '<a href="https://agenthunter.io/e/Zx81qpLm">' + ZW + 'Confirm your email</a>'
ok(v.strip_invisible("a" + ZW + "b") == "ab", "invisible chars stripped")
ok(v.confirm_links(AH, "agenthunter.io")[0] == ["https://agenthunter.io/e/Zx81qpLm"], "button link found though the URL has no confirm word")
ok(v.decide("Please confirm your email", "agenthunter.io", AH, KD, None, 300) == ("OPEN_LINK", "https://agenthunter.io/e/Zx81qpLm"), "agenthunter mail now opens")
ZWURL = "Confirm: https://agenthunter.io/verify/" + ZW + "abc123"
ok(v.confirm_links(ZWURL, "agenthunter.io")[0] == ["https://agenthunter.io/verify/abc123"], "zero-width inside the URL removed")
ok(v.anchor_links('<a href="https://x.io/a">Sign in</a><a href="https://x.io/b">Pricing</a><a href="http://x.io/c">Verify</a>') == ["https://x.io/a"], "anchor text rule, https only")
ok(v.confirm_links('<a href="https://x.io/pricing">Confirm</a>', "x.io")[0] == [], "button text never beats the pay-page block")
ok(v.confirm_links('<a href="https://evil.example/a">Confirm</a>', "x.io") == ([], ["https://evil.example/a"]), "button on another domain is not opened")
# sender rules
ok(v.decide("Confirm your email", "shipgrowth.dev", "https://shipgrowth.dev/confirm?token=1", KD, None, 90)[0] == "OPEN_LINK", "shipgrowth confirm opened")
ok(v.decide("Verify your account", "findstack.com", "https://findstack.com/verify?token=1", KD, None, 400)[0] == "OPEN_LINK", "findstack confirm opened")
ok(v.decide("Confirm your email", "supabase.io", "https://inayaflow.ai/auth/confirm?token=1", KD, None, 90) == ("IGNORE_OUR_APP", None), "supabase.io = our own app, ignored")
ok(v.decide("Confirm your email", "supabase.io", "https://abc.supabase.co/auth/v1/verify?token=1&type=signup&redirect_to=https://inayaflow.ai/", KD, None, 90)[0] == "IGNORE_OUR_APP", "supabase link back to inayaflow.ai ignored")
ok(v.decide("Confirm your email", "supabase.io", "https://abc.supabase.co/auth/v1/verify?token=1&redirect_to=https://other-site.example/", KD, None, 90)[0] == "UNKNOWN_SENDER", "supabase mail for another site goes to a person")
for _g in ("github.com", "gitlab.com", "google.com", "openai.com"):
    ok(v.decide("Confirm your email", _g, "https://%s/confirm?token=1" % _g, KD | {_g}, None, 90) == ("ACCOUNT_MAIL_DIRECTOR", None), "never opened: " + _g)
ok(v.decide("Your DeepSeek verification code", "deepseek.com", "Your code is 123456", KD | {"deepseek.com"}, None, 90)[0] == "SESSION_ONLY_EXPIRED", "numeric code mail is never opened")
ok("IGNORE_OUR_APP" in v.HANDLED_OUTCOMES and "ACCOUNT_MAIL_DIRECTOR" in v.HUMAN_OUTCOMES, "outcomes flagged")
# 24 Nov 2026 (second pass): mail filed elsewhere by the router is ours only for a confirm-type subject from a known sign-up site, never a person's reply
KK = {"newdir.io"}
ok(v.ours_when_filed_elsewhere("newdir.io", KK, "Verify your email - NewDir", True), "other route + verify subject + known site = ours")
ok(not v.ours_when_filed_elsewhere("newdir.io", KK, "Re: Verify your email", True), "other route + a Re: reply = a person, never ours")
ok(not v.ours_when_filed_elsewhere("newdir.io", KK, "Re: Confirm your email", False), "Re: is never ours on the account-mail route either")
ok(not v.ours_when_filed_elsewhere("newdir.io", KK, "Fwd: Verify your email", True) and not v.ours_when_filed_elsewhere("newdir.io", KK, "FW: Verify your email", False), "Fwd:/FW: never ours")
ok(all(v.REPLY_FWD_RE.match(x) for x in ("Re: a", "RE: a", "Fwd: a", "FW: a", "AW: a", "SV: a")) and not v.REPLY_FWD_RE.match("Verify your email"), "reply/forward prefix regex")
ok(not v.ours_when_filed_elsewhere("stranger.com", KK, "Verify your email", True), "unknown site = not ours")
ok(not v.ours_when_filed_elsewhere("newdir.io", KK, "Weekly digest", True), "no verify subject = not ours")
print("verify tests (24 Nov): ok")
print("verify tests: %d passed" % n)
