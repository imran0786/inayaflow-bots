# inayaflow-bots

Small automation bots for InayaFlow, run on free GitHub Actions minutes.

- **Email replier** (`scripts/email_replier/`): reads replies to our link-outreach emails, classifies them with an LLM and sends short, rule-checked replies from our contact address. Public run output contains counts and status codes only.
- **Keepalive**: a monthly commit so GitHub keeps scheduled workflows enabled.

Code only. Credentials live in GitHub Actions secrets. Run logs are stored in a separate private repository, never here.

- **Report robots** (`scripts/reports/`, 19 Nov 2026): `ops-report.yml` (inbox + backlinks report every 3 h, read-only), `backlink-batch.yml` (directory list builder + at most 3 outreach e-mails a day, queued for the outbox robot) and `funnel-daily.yml` (daily counts through one read-only database function). All three are also started by the heartbeat. Results go to the private repository only.
- **Keep-alive check** (`keepalive-check.yml`, `scripts/keepalive/`, 23 Nov 2026): after any robot run and every 20 minutes it starts (workflow_dispatch) any scheduled robot whose newest run is older than 1.5 x its interval (never one already running; max one start per robot per hour). The 3-hourly report shows each robot's last-run age, RED above 2 x its interval.
- **Daily spend caps** (23 Nov 2026, Imran: "Spend 14, maybe $15 a day"): hard daily caps in code, per robot: directory agent $6.00, prospect hunter $6.00, backlink batch $1.00, ops-report 160 model calls (about $0.48), email replier 150 calls (about $0.45), mail router 150 calls (about $0.45), DEV poster $0.60. **$14.98 a day is the hard cap of THESE robots only.** The job runner's other jobs (Director-submitted QA, research and code helper jobs) have their own, separate $15/day cap (`job-runner.yml`) on top of that: $15 is not a total for everything. The hunter's jobs also run through that runner. The test `scripts/keepalive/test_keepalive.py` (WorstCase) adds the caps up from the code.

- **DEV poster** (`dev-poster.yml`, `scripts/dev_poster/`, 23 Nov 2026): one original article a day to dev.to through the official Forem API (Claude Sonnet writer + Haiku QA, hard cap $0.60 a UTC day over all runs, dry or live). Dry until the repository variable `DEV_POSTER_LIVE` is `true`. State, log and failed drafts go to the private repository only (branch `ops-dev-poster`).
