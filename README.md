# inayaflow-bots

Small automation bots for InayaFlow, run on free GitHub Actions minutes.

- **Email replier** (`scripts/email_replier/`): reads replies to our link-outreach emails, classifies them with an LLM and sends short, rule-checked replies from our contact address. Public run output contains counts and status codes only.
- **Keepalive**: a monthly commit so GitHub keeps scheduled workflows enabled.

Code only. Credentials live in GitHub Actions secrets. Run logs are stored in a separate private repository, never here.

- **Report robots** (`scripts/reports/`, 19 Nov 2026): `ops-report.yml` (inbox + backlinks report every 3 h, read-only), `backlink-batch.yml` (directory list builder + at most 3 outreach e-mails a day, queued for the outbox robot) and `funnel-daily.yml` (daily counts through one read-only database function). All three are also started by the heartbeat. Results go to the private repository only.
- **Keep-alive check** (`keepalive-check.yml`, `scripts/keepalive/`, 23 Nov 2026): after any robot run and every 20 minutes it starts (workflow_dispatch) any scheduled robot whose newest run is older than 1.5 x its interval (never one already running; max one start per robot per hour). The 3-hourly report shows each robot's last-run age, RED above 2 x its interval.

- **DEV poster** (`dev-poster.yml`, `scripts/dev_poster/`, 23 Nov 2026): one original article a day to dev.to through the official Forem API (Claude Sonnet writer + Haiku QA, max $0.60 a run). Dry until the repository variable `DEV_POSTER_LIVE` is `true`. State, log and failed drafts go to the private repository only (branch `ops-dev-poster`).
