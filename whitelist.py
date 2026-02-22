import logging
from slack_helpers import parse_fields, approval_message, get_mention
from github_client import create_whitelist_pr

logger = logging.getLogger(__name__)

TRIGGER_KEYWORD = "workflow id:"


def can_handle(text):
    return TRIGGER_KEYWORD in text.lower()


def handle(event, client):
    text = event.get("text", "")
    channel = event.get("channel")
    thread_ts = event.get("ts")
    user = event.get("user") or event.get("bot_id", "workflow")

    fields = parse_fields(text)
    workflow_id = fields.get("workflowid") or fields.get("workflow", "")
    app_id = fields.get("appid") or fields.get("app", "")
    requested_by = fields.get("requestedby") or fields.get("requestby", "")
    reason = fields.get("reason") or fields.get("description") or fields.get("comments", "")

    if not workflow_id or not app_id:
        logger.warning("Whitelist request missing workflow_id or app_id")
        return

    try:
        pr, updated_list = create_whitelist_pr(workflow_id, app_id, requested_by or user, reason)

        details = (
            f"a workflow whitelist request has been raised.\n\n"
            f"*Workflow ID:* `{workflow_id}`\n"
            f"*App ID:* `{app_id}`\n"
            + (f"*Requested By:* {requested_by}\n" if requested_by else "")
            + (f"*Reason:* {reason}\n" if reason else "")
            + f"*PR:* <{pr.html_url}|View on GitHub>"
        )

        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            blocks=approval_message(get_mention(), details, pr.number)
        )
        logger.info(f"Whitelist PR #{pr.number} created for {workflow_id} + {app_id}")

    except Exception as e:
        logger.error(f"Whitelist PR creation failed: {e}")
        client.chat_postMessage(
            channel=channel,
            text=f"❌ Something went wrong creating the PR: `{str(e)}`"
        )
