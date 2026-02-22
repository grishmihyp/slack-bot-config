import os
import json
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from github import Github
from dotenv import load_dotenv

load_dotenv()

# ── Slack + Flask setup ──────────────────────────────────────────
app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"]
)
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# ── GitHub setup ─────────────────────────────────────────────────
gh = Github(os.environ["GITHUB_TOKEN"])
repo = gh.get_repo(os.environ["GITHUB_REPO"])  # e.g. "yourorg/client-config"
JSON_FILE_PATH = os.environ.get("JSON_FILE_PATH", "client-data.json")
BASE_BRANCH = os.environ.get("BASE_BRANCH", "main")


# ── Helper: read current JSON from GitHub ────────────────────────
def get_current_data():
    file = repo.get_contents(JSON_FILE_PATH, ref=BASE_BRANCH)
    return json.loads(file.decoded_content), file.sha


# ── Helper: create branch + commit + PR ──────────────────────────
def create_pr(email, client_id, slack_user):
    # 1. Read current file
    current_data, file_sha = get_current_data()

    # 2. Add new entry
    current_data[email] = [client_id]
    updated_content = json.dumps(current_data, indent=4)

    # 3. Create a new branch
    import re
    safe_email = re.sub(r'[^a-zA-Z0-9-]', '-', email.split('@')[0])
    safe_client = re.sub(r'[^a-zA-Z0-9-]', '-', client_id)
    branch_name = f"update/{safe_email}-{safe_client}"
    source = repo.get_branch(BASE_BRANCH)
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=source.commit.sha)

    # 4. Commit updated file to new branch
    repo.update_file(
        path=JSON_FILE_PATH,
        message=f"Add {email} with client ID {client_id}",
        content=updated_content,
        sha=file_sha,
        branch=branch_name
    )

    # 5. Open PR
    pr = repo.create_pull(
        title=f"Add client: {email} [{client_id}]",
        body=f"Requested by <@{slack_user}> via Slack.\n\n**Email:** {email}\n**Client ID:** {client_id}",
        head=branch_name,
        base=BASE_BRANCH
    )
    return pr


# ── Slack: listen for messages containing "email:" ───────────────
# Users post in this format:
#   email: user@company.com
#   client_id: HV
@app.message("email:")
def handle_request(message, say, client):
    text = message.get("text", "")
    user = message.get("user")

    # Parse email and client_id from message
    email, client_id = None, None
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("email:"):
            raw = line.split(":", 1)[1].strip()
            # Strip Slack's mailto formatting: <mailto:user@co.com|user@co.com>
            import re
            match = re.search(r'mailto:([^\|>]+)', raw)
            email = match.group(1) if match else raw
        elif line.lower().startswith("client_id:"):
            client_id = line.split(":", 1)[1].strip()

    if not email or not client_id:
        say(f"<@{user}> ⚠️ Please use this format:\n```email: user@company.com\nclient_id: HV```")
        return

    say(f"⏳ Got it <@{user}>! Creating a PR for `{email}` with client ID `{client_id}`...")

    try:
        pr = create_pr(email, client_id, user)

        # Post approval card with buttons
        say(
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"✅ *PR Ready for Review*\n*Email:* `{email}`\n*Client ID:* `{client_id}`\n*Requested by:* <@{user}>\n*PR:* <{pr.html_url}|View on GitHub>"
                    }
                },
                {
                    "type": "actions",
                    "block_id": f"approval_{pr.number}",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Approve & Merge"},
                            "style": "primary",
                            "action_id": "approve_pr",
                            "value": str(pr.number)
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ Reject"},
                            "style": "danger",
                            "action_id": "reject_pr",
                            "value": str(pr.number)
                        }
                    ]
                }
            ]
        )
    except Exception as e:
        say(f"❌ Something went wrong creating the PR: `{str(e)}`")


# ── Slack: Approve button ─────────────────────────────────────────
@app.action("approve_pr")
def handle_approve(ack, body, say, client):
    ack()
    pr_number = int(body["actions"][0]["value"])
    approver = body["user"]["id"]

    try:
        pr = repo.get_pull(pr_number)
        pr.merge(merge_method="squash")

        client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"✅ *Approved & Merged* by <@{approver}>\n*PR:* {pr.title}"
                    }
                }
            ]
        )
        say(f"🎉 PR #{pr_number} merged successfully by <@{approver}>!")
    except Exception as e:
        say(f"❌ Failed to merge PR #{pr_number}: `{str(e)}`")


# ── Slack: Reject button ──────────────────────────────────────────
@app.action("reject_pr")
def handle_reject(ack, body, say, client):
    ack()
    pr_number = int(body["actions"][0]["value"])
    rejecter = body["user"]["id"]

    try:
        pr = repo.get_pull(pr_number)
        pr.edit(state="closed")

        client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"❌ *Rejected* by <@{rejecter}>\n*PR:* {pr.title}"
                    }
                }
            ]
        )
        say(f"🚫 PR #{pr_number} rejected by <@{rejecter}>.")
    except Exception as e:
        say(f"❌ Failed to reject PR #{pr_number}: `{str(e)}`")


# ── Flask routes ──────────────────────────────────────────────────
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/", methods=["GET"])
def health():
    return "Bot is running ✅", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
