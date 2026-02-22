import logging
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request
from config import SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, APPROVER_SLACK_ID
from slack_helpers import is_authorized
from github_client import merge_pr, close_pr
import handlers.publish_list as publish_list
import handlers.whitelist as whitelist

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── Slack + Flask setup ───────────────────────────────────────────
app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)

# ── Registry: add new workflows here ─────────────────────────────
# To add a new workflow: create handlers/your_workflow.py and add it here
HANDLERS = [
    publish_list,
    whitelist,
    # handlers.new_workflow,  ← just add new ones here
]


# ── Message router ────────────────────────────────────────────────
@app.event("message")
def route_message(body, client):
    event = body.get("event", {})
    text = event.get("text", "")

    for h in HANDLERS:
        if h.can_handle(text):
            logger.info(f"Routing to handler: {h.__name__}")
            h.handle(event, client)
            return  # stop after first match


# ── Approve button ────────────────────────────────────────────────
@app.action("approve_pr")
def handle_approve(ack, body, client):
    ack()
    approver = body["user"]["id"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]
    pr_number = int(body["actions"][0]["value"])

    if not is_authorized(approver):
        client.chat_postEphemeral(
            channel=channel,
            user=approver,
            text="🚫 You're not authorized to approve this request."
        )
        return

    try:
        pr = merge_pr(pr_number)
        client.chat_update(
            channel=channel,
            ts=ts,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"✅ Approved by <@{approver}>"}
            }]
        )
        logger.info(f"PR #{pr_number} approved by {approver}")
    except Exception as e:
        logger.error(f"Approve failed for PR #{pr_number}: {e}")
        client.chat_postMessage(channel=channel, text=f"❌ Failed to merge PR #{pr_number}: `{str(e)}`")


# ── Decline button ────────────────────────────────────────────────
@app.action("reject_pr")
def handle_decline(ack, body, client):
    ack()
    decliner = body["user"]["id"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]
    pr_number = int(body["actions"][0]["value"])

    if not is_authorized(decliner):
        client.chat_postEphemeral(
            channel=channel,
            user=decliner,
            text="🚫 You're not authorized to decline this request."
        )
        return

    try:
        pr = close_pr(pr_number)
        client.chat_update(
            channel=channel,
            ts=ts,
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"❌ Declined by <@{decliner}>"}
            }]
        )
        logger.info(f"PR #{pr_number} declined by {decliner}")
    except Exception as e:
        logger.error(f"Decline failed for PR #{pr_number}: {e}")
        client.chat_postMessage(channel=channel, text=f"❌ Failed to decline PR #{pr_number}: `{str(e)}`")


# ── Flask routes ──────────────────────────────────────────────────
@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)


@flask_app.route("/", methods=["GET"])
def health():
    approver = APPROVER_SLACK_ID or "NOT SET"
    return f"Bot is running ✅ | APPROVER_SLACK_ID: {approver}", 200


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 3000))
    flask_app.run(host="0.0.0.0", port=port)
