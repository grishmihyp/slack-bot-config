import os
import re
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
repo = gh.get_repo(os.environ["GITHUB_REPO"])
JSON_FILE_PATH = os.environ.get("JSON_FILE_PATH", "client-data.json")
BASE_BRANCH = os.environ.get("BASE_BRANCH", "main")


# ── Helper: read current JSON from GitHub ────────────────────────
def get_current_data():
    file = repo.get_contents(JSON_FILE_PATH, ref=BASE_BRANCH)
    return json.loads(file.decoded_content), file.sha


# ── Helper: create branch + commit + PR ──────────────────────────
def create_pr(email, client_id, slack_user):
    current_data, file_sha = get_current_data()
    current_data[email] = [client_id]
    updated_content = json.dumps(current_data, indent=4)

    safe_email = re.sub(r'[^a-zA-Z0-9-]', '-', email.split('@')[0])
    safe_client = re.sub(r'[^a-zA-Z0-9-]', '-', client_id)
    branch_name = f"update/{safe_email}-{safe_client}"

    source = repo.get_branch(BASE_BRANCH)
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=source.commit.sha)

    repo.update_file(
        path=JSON_FILE_PATH,
        message=f"Add {email} with client ID {client_id}",
        content=updated_content,
        sha=file_sha,
        branch=branch_name
    )

    pr = repo.create_pull(
        title=f"Add client: {email} [{client_id}]",
        body=f"Requested by <@{slack_user}> via Slack.\n\n**Email:** {email}\n**Client ID:** {client_id}",
        head=branch_name,
        base=BASE_BRANCH
    )
    return pr


# ── Slash command: /setup-channel ────────────────────────────────
# Run this ONCE in your channel to post + pin the Submit button
@app.command("/setup-channel")
def setup_channel(ack, body, client):
    ack()
    channel_id = body["channel_id"]

    result = client.chat_postMessage(
        channel=channel_id,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*📋 Client Onboarding Requests*\nClick the button below to submit a new client request. An admin will review and approve it."
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "➕ Submit Request", "emoji": True},
                        "style": "primary",
                        "action_id": "open_request_modal"
                    }
                ]
            }
        ]
    )
    # Pin the message so it stays at the top
    client.pins_add(channel=channel_id, timestamp=result["ts"])


# ── Action: open modal when button clicked ───────────────────────
@app.action("open_request_modal")
def open_modal(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "submit_request",
            "title": {"type": "plain_text", "text": "Submit Client Request"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": body["channel"]["id"],
            "blocks": [
                {
                    "type": "input",
                    "block_id": "email_block",
                    "label": {"type": "plain_text", "text": "Client Email"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "email_input",
                        "placeholder": {"type": "plain_text", "text": "user@company.com"}
                    }
                },
                {
                    "type": "input",
                    "block_id": "client_id_block",
                    "label": {"type": "plain_text", "text": "Client ID"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "client_id_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. HV"}
                    }
                }
            ]
        }
    )


# ── Modal submission ──────────────────────────────────────────────
@app.view("submit_request")
def handle_submission(ack, body, client, say):
    ack()

    values = body["view"]["state"]["values"]
    email = values["email_block"]["email_input"]["value"].strip()
    client_id = values["client_id_block"]["client_id_input"]["value"].strip()
    user = body["user"]["id"]
    channel_id = body["view"]["private_metadata"]

    client.chat_postMessage(
        channel=channel_id,
        text=f"⏳ <@{user}> submitted a request for `{email}` — creating PR..."
    )

    try:
        pr = create_pr(email, client_id, user)

        client.chat_postMessage(
            channel=channel_id,
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
        client.chat_postMessage(
            channel=channel_id,
            text=f"❌ Something went wrong creating the PR: `{str(e)}`"
        )


# ── Approve button ────────────────────────────────────────────────
@app.action("approve_pr")
def handle_approve(ack, body, client):
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
        client.chat_postMessage(
            channel=body["channel"]["id"],
            text=f"🎉 PR #{pr_number} merged successfully by <@{approver}>!"
        )
    except Exception as e:
        client.chat_postMessage(
            channel=body["channel"]["id"],
            text=f"❌ Failed to merge PR #{pr_number}: `{str(e)}`"
        )


# ── Reject button ─────────────────────────────────────────────────
@app.action("reject_pr")
def handle_reject(ack, body, client):
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
        client.chat_postMessage(
            channel=body["channel"]["id"],
            text=f"🚫 PR #{pr_number} rejected by <@{rejecter}>."
        )
    except Exception as e:
        client.chat_postMessage(
            channel=body["channel"]["id"],
            text=f"❌ Failed to reject PR #{pr_number}: `{str(e)}`"
        )


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
