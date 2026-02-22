# Slack → GitHub Approval Bot

A Slack bot that lets users submit client data requests which get committed to GitHub as a PR, then approved or rejected via Slack buttons.

## How it works

1. User posts in Slack channel:
   ```
   email: user@company.com
   client_id: HV
   ```
2. Bot creates a new branch, updates `client-data.json`, and opens a PR
3. Bot posts an **Approve / Reject** card in the channel
4. Admin clicks Approve → PR is merged. Reject → PR is closed.

## Project structure

```
slack-github-bot/
├── app.py              # All bot logic
├── requirements.txt    # Python dependencies
├── Procfile            # Railway start command
├── .env.example        # Template for environment variables
└── .gitignore
```

## Environment variables

| Variable | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Bot User OAuth Token from api.slack.com (starts with `xoxb-`) |
| `SLACK_SIGNING_SECRET` | Signing Secret from your Slack App's Basic Information page |
| `GITHUB_TOKEN` | GitHub Personal Access Token with repo read/write access |
| `GITHUB_REPO` | Target repo in format `org/repo-name` e.g. `hyperverge/client-config` |
| `JSON_FILE_PATH` | Path to JSON file in repo (default: `client-data.json`) |
| `BASE_BRANCH` | Branch to merge PRs into (default: `main`) |

## Local development

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in your values in .env
python app.py
```

## Deploy to Railway

1. Push this repo to GitHub
2. Go to railway.app → New Project → Deploy from GitHub
3. Add all environment variables in Railway's Variables tab
4. Railway auto-assigns a public URL — copy it
5. Paste into your Slack App: Interactivity & Shortcuts → Request URL:
   `https://your-app.up.railway.app/slack/events`

## Slack App permissions required

Under OAuth & Permissions → Bot Token Scopes:
- `chat:write`
- `channels:history`
- `channels:read`
- `im:write`

Enable **Interactivity** and set the Request URL to your Railway URL + `/slack/events`
