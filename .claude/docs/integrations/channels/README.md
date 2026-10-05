# Channels Integration — Outbound Slack / Discord / Teams Notifications

> Lets the harness send messages out to chat channels via incoming webhooks.
> Borrowed from Vercel Eve's "channels" idea, scoped to outbound-only (the
> harness is a local CLI, not a deployed bot — it sends, it does not listen).

## What It Is

A single stdlib-only Python script (`notify.py`) that POSTs a message to
whichever of Slack / Discord / Teams incoming webhooks you've configured. Plus
a `/notify` command to fire it by hand, and an opt-in wire into the daily
maintenance runner so each repo's daily report lands in your channels.

## How It Works

```
~/.env.channels  (chmod 600, lives in HOME — outside every git repo)
   SLACK_WEBHOOK_URL / DISCORD_WEBHOOK_URL / TEAMS_WEBHOOK_URL
        │
        ▼
notify.py "message"
   → reads ~/.env.channels
   → builds per-platform payload (Slack {"text"} · Discord {"content"} · Teams {"text"})
   → POSTs to each configured webhook  (unconfigured platforms skipped silently)
   → exit 0 if all sent; exit 1 if any failed; exit 0 no-op if no file
```

Auth model is the simplest possible: an incoming-webhook URL **is** the
credential. No OAuth, no tokens to refresh. Treat the URLs as secrets — anyone
with the URL can post to that channel.

## Setup

### 1. Create the credentials file

```bash
cp $SDD_HARNESS/templates/.env.channels.template ~/.env.channels
chmod 600 ~/.env.channels
# edit ~/.env.channels and paste in the webhook URLs you want
```

It lives in your home directory, not in any repo, so it cannot be committed.
(A defensive `*.env.channels` line is also in the harness `.gitignore`.)

### 2. Get the webhook URLs

| Platform | Where |
|---|---|
| **Slack** | Create an app → **Incoming Webhooks** → *Add New Webhook to Workspace* → copy URL. https://api.slack.com/messaging/webhooks |
| **Discord** | Server Settings → **Integrations** → **Webhooks** → *New Webhook* → *Copy Webhook URL*. |
| **Teams** | Channel → ⋯ → **Workflows** → *Post to a channel when a webhook request is received* (Power Automate). Older tenants: ⋯ → **Connectors** → *Incoming Webhook*. |

Configure only the platforms you use — the rest are skipped.

## Usage

```bash
/notify deploy finished on staging          # all configured channels
/notify --only slack build is red           # one platform
/notify --title "Release" shipped v1.2.3     # bold title line
```

Direct script invocation (e.g. from other scripts):

```bash
python3 .claude/scripts/integrations/channels/notify.py "message"
echo "piped message" | python3 .claude/scripts/integrations/channels/notify.py
```

## Daily Report Delivery

`daily-runner.sh` posts a summary of each repo's daily maintenance run to your
channels **only if `~/.env.channels` exists**. With no file, it's a silent
no-op. Opt out without removing the file:

```bash
export SDD_SKIP_CHANNEL_NOTIFY=1
```

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| "No channels configured" | `~/.env.channels` missing or all URLs blank | Create it from the template, fill in at least one URL |
| `✗ slack: HTTP 404` | Webhook URL revoked or wrong | Regenerate the webhook, update `~/.env.channels` |
| `✗ discord: HTTP 400` | Message too long / malformed | Script truncates to platform limits; check for an empty message |
| Teams posts nothing | Power Automate flow not mapping `.text` | In the flow, map the incoming `text` field to the message card body |
| Nothing in daily report | `~/.env.channels` absent on the machine running the runner | Create it there, or accept terminal-only reports |

## Files

| File | Purpose |
|---|---|
| `scripts/integrations/channels/notify.py` | Stdlib-only webhook sender. No external deps. |
| `templates/.env.channels.template` | Copy to `~/.env.channels` and fill in. |
| `commands/global/notify.md` | `/notify` command. |
| `scripts/orchestration/daily-runner.sh` | Posts the daily maintenance summary (opt-in via file presence). |
