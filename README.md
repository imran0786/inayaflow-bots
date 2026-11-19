# inayaflow-bots

Small automation bots for InayaFlow, run on free GitHub Actions minutes.

- **Email replier** (`scripts/email_replier/`): reads replies to our link-outreach emails, classifies them with an LLM and sends short, rule-checked replies from our contact address. Public run output contains counts and status codes only.
- **Keepalive**: a monthly commit so GitHub keeps scheduled workflows enabled.

Code only. Credentials live in GitHub Actions secrets. Run logs are stored in a separate private repository, never here.

- **Report robots** (`scripts/reports/`, 19 Nov 2026): `ops-report.yml` (inbox + backlinks report every 3 h, read-only), `backlink-batch.yml` (directory list builder + at most 3 outreach e-mails a day, queued for the outbox robot) and `funnel-daily.yml` (daily counts through one read-only database function). All three are also started by the heartbeat. Results go to the private repository only.
