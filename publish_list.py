import logging
from slack_helpers import parse_fields, get_email, approval_message, get_mention
from github_client import create_publish_list_pr

logger = logging.getLogger(__name__)

TRIGGER_KEYWORD = "email id:"


def can_handle(text):
    return TRIGGER_KEYWORD in text.lower()


def handle(event, client):
    text = event.get("text", "")
    channel = event.get("channel")
    thread_ts = event.get("ts")
    user = event.get("user") or event.get("bot_id", "workflow")

    fields = parse_fields(text)
    email = get_email(fields)
    client_id = fields.get("clientid") or fields.get("client", "")
    requested_by = fields.get("requestedby") or fields.get("requestby", "")
    description = fields.get("additionalcomments") or fields.get("comments") or fields.get("description", "")

    if not email or not client_id:
        logger.warning("Publish list request missing email or client_id")
        return

    try:
        pr = create_publish_list_pr(email, client_id, requested_by or user, description)

        details = (
            f"a publish to production access request has been raised.\n\n"
            f"*Email:* `{email}`\n"
            f"*Client ID:* `{client_id}`\n"
            + (f"*Requested By:* {requested_by}\n" if requested_by else "")
            + (f"*Additional Comments:* {description}\n" if description else "")
            + f"*PR:* <{pr.html_url}|View on GitHub>"
        )

        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            blocks=approval_message(get_mention(), details, pr.number)
        )
        logger.info(f"Publish list PR #{pr.number} created for {email}")

    except Exception as e:
        logger.error(f"Publish list PR creation failed: {e}")
        client.chat_postMessage(
            channel=channel,
            text=f"❌ Something went wrong creating the PR: `{str(e)}`"
        )
