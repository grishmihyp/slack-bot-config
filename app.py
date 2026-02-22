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

# ── Authorized approver (only this Slack user ID can approve/reject)
APPROVER_SLACK_ID = os.environ.get("APPROVER_SLACK_ID", "")


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


# ── Helper: build approval card blocks ───────────────────────────
def approval_blocks(email, client_id, requested_by, description, pr):
    mention = f"<@{APPROVER_SLACK_ID}>" if APPROVER_SLACK_ID else "admin"
    details = f"🆕 *Production Access Request*\n\nHey {mention}, a publish to production access request has been raised.\n\n*Email:* `{email}`\n*Client ID:* `{client_id}`"
    if requested_by:
        details += f"\n*Requested By:* {requested_by}"
    if description:
        details += f"\n*Additional Comments:* {description}"
    details += f"\n*PR:* <{pr.html_url}|View on GitHub>"

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": details}
        },
        {
            "type": "actions",
            "block_id": f"approval_{pr.number}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": "approve_pr",
                    "value": str(pr.number)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Decline"},
                    "style": "danger",
                    "action_id": "reject_pr",
                    "value": str(pr.number)
                }
            ]
        }
    ]


# ── Listen for workflow messages ──────────────────────────────────
@app.event("message")
def handle_all_messages(body, say, client):
    event = body.get("event", {})
    text = event.get("text", "")

    if "email id:" not in text.lower():
        return

    user = event.get("user") or event.get("bot_id", "workflow")
    channel = event.get("channel")
    thread_ts = event.get("ts")  # reply in thread of this message

    email, client_id, requested_by, description = None, None, None, None

    for line in text.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        key, _, value = line.partition(':')
        key_clean = re.sub(r'[\s_\-]+', '', key).lower()
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

    try:
        pr = create_pr(email, client_id, requested_by or user, description)

        mention = f"<@{APPROVER_SLACK_ID}>" if APPROVER_SLACK_ID else "admin"

        # Post in thread of the workflow message
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"Hey {mention}, a publish to production access request has been raised. Please review and approve or decline."
                    }
                },
                {
                    "type": "actions",
                    "block_id": f"approval_{pr.number}",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Approve"},
                            "style": "primary",
                            "action_id": "approve_pr",
                            "value": str(pr.number)
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ Decline"},
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


# ── Approve button ────────────────────────────────────────────────
@app.action("approve_pr")
def handle_approve(ack, body, client):
    ack()
    approver = body["user"]["id"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]
    thread_ts = body["message"].get("thread_ts", ts)
    pr_number = int(body["actions"][0]["value"])

    # ── Restrict to authorized approver only ──
    if APPROVER_SLACK_ID and approver != APPROVER_SLACK_ID:
        client.chat_postEphemeral(
            channel=channel,
            user=approver,
            thread_ts=thread_ts,
            text="🚫 You're not authorized to approve this request."
        )
        return

    try:
        pr = repo.get_pull(pr_number)
        pr.merge(merge_method="squash")

        # Update the approval card to show approved state
        client.chat_update(
            channel=channel,
            ts=ts,
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
    except Exception as e:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"❌ Failed to merge PR #{pr_number}: `{str(e)}`"
        )


# ── Decline button ────────────────────────────────────────────────
@app.action("reject_pr")
def handle_reject(ack, body, client):
    ack()
    rejecter = body["user"]["id"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]
    thread_ts = body["message"].get("thread_ts", ts)
    pr_number = int(body["actions"][0]["value"])

    # ── Restrict to authorized approver only ──
    if APPROVER_SLACK_ID and rejecter != APPROVER_SLACK_ID:
        client.chat_postEphemeral(
            channel=channel,
            user=rejecter,
            thread_ts=thread_ts,
            text="🚫 You're not authorized to decline this request."
        )
        return

    try:
        pr = repo.get_pull(pr_number)
        pr.edit(state="closed")

        # Update the approval card to show declined state
        client.chat_update(
            channel=channel,
            ts=ts,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"❌ *Declined* by <@{rejecter}>\n*PR:* {pr.title}"
                    }
                }
            ]
        )
    except Exception as e:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"❌ Failed to decline PR #{pr_number}: `{str(e)}`"
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
