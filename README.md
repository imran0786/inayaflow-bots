# inayaflow-bots

Small automation bots for InayaFlow, run on free GitHub Actions minutes.

- **Email replier** (`scripts/email_replier/`): reads replies to our link-outreach emails, classifies them with an LLM and sends short, rule-checked replies from our contact address. Public run output contains counts and status codes only.
- **Keepalive**: a monthly commit so GitHub keeps scheduled workflows enabled.

Code only. Credentials live in GitHub Actions secrets. Run logs are stored in a separate private repository, never here.
