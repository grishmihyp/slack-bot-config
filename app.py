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
def create_pr(email, client_id, slack_user, description=None):
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

    pr_body = f"Requested by {slack_user} via Slack.\n\n**Email:** {email}\n**Client ID:** {client_id}"
    if description:
        pr_body += f"\n**Description:** {description}"

    pr = repo.create_pull(
        title=f"Add client: {email} [{client_id}]",
        body=pr_body,
        head=branch_name,
        base=BASE_BRANCH
    )
    return pr


# ── Listen for ALL messages (including bot/workflow messages) ─────
@app.event("message")
def handle_all_messages(body, say, client):
    event = body.get("event", {})
    text = event.get("text", "")
    subtype = event.get("subtype", "")

    # Only process messages that contain "email:"
    if "email id:" not in text.lower():
        return

    # Get user — for bot messages (Workflow Builder), use the channel instead
    user = event.get("user") or event.get("bot_id", "workflow")

    email, client_id, requested_by, description = None, None, None, None
    import re

    for line in text.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue

        key, _, value = line.partition(':')
        key_clean = re.sub(r'[\s_\-]+', '', key).lower()  # remove spaces/underscores/dashes
        value = value.strip()

        if key_clean in ('emailid', 'email'):
            match = re.search(r'mailto:([^\|>]+)', value)
            email = match.group(1) if match else value
        elif key_clean in ('clientid', 'client'):
            client_id = value
        elif key_clean in ('requestedby', 'requestby'):
            requested_by = value
        elif key_clean in ('description', 'additionalcomments', 'comments', 'comment'):
            description = value

    if not email or not client_id:
        return

    channel = event.get("channel")

    # Always post as a new message with a unique timestamp marker
    client.chat_postMessage(
        channel=channel,
        text=f"⏳ Creating a PR for `{email}` with client ID `{client_id}`..."
    )

    try:
        pr = create_pr(email, client_id, requested_by or user, description)

        # Build the PR details text
        details = f"✅ *PR Ready for Review*\n*Email:* `{email}`\n*Client ID:* `{client_id}`"
        if requested_by:
            details += f"\n*Requested By:* {requested_by}"
        if description:
            details += f"\n*Description:* {description}"
        details += f"\n*PR:* <{pr.html_url}|View on GitHub>"

        client.chat_postMessage(
            channel=channel,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": details
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
            channel=channel,
            text=f"❌ Something went wrong creating the PR: `{str(e)}`"
        )


# ── Slash command to post the pinned "Submit Request" button ─────
# Run /setup-request-button once in your channel to pin the button
@app.command("/setup-request-button")
def post_request_button(ack, body, client):
    ack()
    channel_id = body["channel_id"]

    result = client.chat_postMessage(
        channel=channel_id,
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*📋 Client Access Request*\nClick the button below to submit a new client request. A PR will be created and sent for approval."
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "📝 Submit Request", "emoji": True},
                        "style": "primary",
                        "action_id": "open_request_modal"
                    }
                ]
            }
        ]
    )

    # Pin the message to the channel
    client.pins_add(channel=channel_id, timestamp=result["ts"])


# ── Handle "Submit Request" button → open modal ──────────────────
@app.action("open_request_modal")
def open_modal(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "submit_request_modal",
            "title": {"type": "plain_text", "text": "Client Access Request"},
            "submit": {"type": "plain_text", "text": "Submit"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": body["channel"]["id"],
            "blocks": [
                {
                    "type": "input",
                    "block_id": "email_block",
                    "label": {"type": "plain_text", "text": "Email Address"},
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


# ── Handle modal submission ───────────────────────────────────────
@app.view("submit_request_modal")
def handle_modal_submit(ack, body, client, say):
    ack()

    values = body["view"]["state"]["values"]
    email = values["email_block"]["email_input"]["value"].strip()
    client_id = values["client_id_block"]["client_id_input"]["value"].strip()
    user = body["user"]["id"]
    channel_id = body["view"]["private_metadata"]

    client.chat_postMessage(
        channel=channel_id,
        text=f"⏳ <@{user}> submitted a request for `{email}` with client ID `{client_id}`. Creating PR..."
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
