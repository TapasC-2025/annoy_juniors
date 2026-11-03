# Junior PR questions bot

When a PR contains commits by someone in `.github/juniors.json`, the bot comments on the PR, tagging them:

1. **"Just confirming, this change is…"**: a plain-English summary built from the diff (new/removed Terraform resources, k8s kinds, deleted files, line counts).
2. **Diff-specific questions**: e.g. `0.0.0.0/0` → "Who needs access, and why not a narrower CIDR?"; IAM `*`, deletion protection off, hard-coded secrets, instance sizing, destructive commands.
3. **Generic checklist**: did you read the diff, can you explain it, which environment, did you run plan, what's the rollback, who depends on it.

One comment per PR, updated when the changed files change (ticked boxes survive pushes that don't change the summary).

## Install

Copy into your infra repo, keeping paths:
- `.github/workflows/junior-review.yml`
- `.github/juniors.json` (put GitHub logins in it)
- `scripts/junior_review_bot.py`

No secrets or other setup needed; it uses the built-in `GITHUB_TOKEN`.

## Customise

Edit `GENERIC_QUESTIONS` and `RISK_QUESTIONS` at the top of the script.
