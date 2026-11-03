#!/usr/bin/env python3
"""
Asks a junior engineer the "are you sure?" questions on their infra PR.

When a PR contains commits by someone in .github/juniors.json, the bot posts (and
keeps updated) ONE comment that tags them, summarises in plain words what the PR
changes ("just confirming, this change is ..."), and asks verification questions,
including extra ones triggered by risky patterns in the diff.

Stdlib only. Env: GITHUB_TOKEN, GITHUB_REPOSITORY, GITHUB_EVENT_PATH.
"""
import json
import os
import re
import urllib.error
import urllib.request

API = "https://api.github.com"
TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
CONFIG_PATH = os.environ.get("JUNIORS_CONFIG", ".github/juniors.json")
MARKER = "<!-- junior-review-bot -->"

# Always asked.
GENERIC_QUESTIONS = [
    "Did you go through the full diff yourself, line by line?",
    "Can you explain in one or two sentences what this change does and why it is needed?",
    "Do you know what will actually happen when this is applied or deployed?",
    "Which environment does this hit (dev / staging / prod)? Have you checked that it's the right one?",
    "Did you run `plan` / `diff` / a dry run, and did the output match what you expected?",
    "What breaks if this goes wrong, and how do we roll it back?",
    "Did anyone else (or any other service) depend on what you changed?",
]

# (regex on ADDED lines, question). Each question is asked at most once.
RISK_QUESTIONS = [
    (r"0\.0\.0\.0/0|::/0",
     "This opens something to the whole internet (`0.0.0.0/0`). Who needs access, and why can't it be a narrower CIDR or a VPN?"),
    (r'"Action"\s*:\s*"\*"|actions\s*=\s*\[\s*"\*"\s*\]|"Resource"\s*:\s*"\*"|resources\s*=\s*\[\s*"\*"\s*\]',
     "There's an IAM `*` here. What is the minimum permission this actually needs?"),
    (r"deletion_protection\s*=\s*false|force_destroy\s*=\s*true|skip_final_snapshot\s*=\s*true",
     "You've turned off a safety net (deletion protection / force destroy / final snapshot). Is there a backup, and is this intentional?"),
    (r"publicly_accessible\s*=\s*true|acl\s*=\s*\"public",
     "Something is marked public. Does it really need to be, and what data is in it?"),
    (r"encrypted\s*=\s*false",
     "Encryption is disabled. Why?"),
    (r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(password|secret|token)\s*[=:]\s*[\"'][^\"']+[\"']",
     "This looks like a hard-coded credential. Is it a real secret? If so it needs rotating and moving to a secrets manager."),
    (r"kubectl\s+delete|terraform\s+destroy|--auto-approve",
     "There's a destructive or unattended command here. What stops it from running against the wrong target?"),
    (r"instance_type|instance_class|node_count|desired_capacity|replicas\s*[=:]",
     "This changes capacity or instance sizing. Have you checked the cost impact?"),
]


def gh(method, path, body=None):
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        print(f"GitHub {method} {path} -> {e.code}: {e.read().decode()[:300]}")
        return None


def paginate(path):
    out, page = [], 1
    while True:
        chunk = gh("GET", f"{path}?per_page=100&page={page}") or []
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def describe_file(f):
    """One plain-English line about what happened to a file."""
    name, patch = f["filename"], f.get("patch") or ""
    if f["status"] == "removed":
        return f"`{name}` is **deleted**"
    added, removed = set(), set()
    for line in patch.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        # Terraform: resource "aws_x" "name"   |  Kubernetes: kind: Deployment
        m = re.search(r'(resource|module|data)\s+"([^"]+)"(?:\s+"([^"]+)")?', line[1:])
        k = re.match(r"[+-]\s*kind:\s*(\w+)", line)
        label = None
        if m:
            label = f"{m.group(2)}.{m.group(3)}" if m.group(3) else m.group(2)
        elif k:
            label = f"k8s {k.group(1)}"
        if label and line.startswith("+"):
            added.add(label)
        elif label and line.startswith("-"):
            removed.add(label)
    parts = []
    if f["status"] == "added":
        parts.append("is a **new file**")
    if added - removed:
        parts.append("adds " + ", ".join(f"`{x}`" for x in sorted(added - removed)))
    if removed - added:
        parts.append("**removes** " + ", ".join(f"`{x}`" for x in sorted(removed - added)))
    if not parts:
        parts.append(f"edits existing config (+{f['additions']}/-{f['deletions']} lines)")
    return f"`{name}` " + "; ".join(parts)


def build_comment(juniors_hit, pr, files):
    mention = " ".join(f"@{j}" for j in juniors_hit)
    body = [MARKER, f"### 👋 Hey {mention}, quick check before this goes anywhere\n"]

    body.append("**Just confirming, this change is:**")
    shown = files[:15]
    body += [f"- {describe_file(f)}" for f in shown]
    if len(files) > len(shown):
        body.append(f"- …and {len(files) - len(shown)} more files")
    body.append("\nIs that what you intended? If not, please fix it before asking for review.\n")

    added_text = "\n".join(
        l for f in files for l in (f.get("patch") or "").splitlines()
        if l.startswith("+") and not l.startswith("+++"))
    specific = [q for rx, q in RISK_QUESTIONS if re.search(rx, added_text, re.I)]
    if specific:
        body.append("**Things in your diff that need a real answer:**")
        body += [f"- ⚠️ {q}" for q in specific]
        body.append("")

    body.append("**Please reply to this comment answering:**")
    body += [f"- [ ] {q}" for q in GENERIC_QUESTIONS]
    body.append("\n_Answer in your own words, not copy/paste from the diff. "
                "This is an automated check; a human reviewer will still look at it._")
    return "\n".join(body)


def main():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    with open(os.environ["GITHUB_EVENT_PATH"]) as f:
        event = json.load(f)
    pr = event["pull_request"]
    if pr.get("draft"):
        return
    n = pr["number"]
    juniors = {j.lower() for j in cfg["juniors"]}

    logins = set()
    for c in paginate(f"/repos/{REPO}/pulls/{n}/commits"):
        for k in ("author", "committer"):
            if c.get(k) and c[k].get("login"):
                logins.add(c[k]["login"])
    hit = sorted(l for l in logins if l.lower() in juniors)
    if not hit:
        print("No junior commits; nothing to do.")
        return

    body = build_comment(hit, pr, paginate(f"/repos/{REPO}/pulls/{n}/files"))
    existing = next((c for c in paginate(f"/repos/{REPO}/issues/{n}/comments")
                     if MARKER in c["body"]), None)
    if existing:
        # Don't wipe ticked boxes on every push: only rewrite if content changed
        # in the "this change is" section. Simple approach: skip if already posted
        # and the file list is unchanged.
        old_head = existing["body"].split("**Please reply")[0]
        if old_head == body.split("**Please reply")[0]:
            print("Comment already up to date.")
            return
        gh("PATCH", f"/repos/{REPO}/issues/comments/{existing['id']}", {"body": body})
    else:
        gh("POST", f"/repos/{REPO}/issues/{n}/comments", {"body": body})


if __name__ == "__main__":
    main()
