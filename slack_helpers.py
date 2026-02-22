import logging
from config import APPROVER_SLACK_ID

logger = logging.getLogger(__name__)


def parse_fields(text):
    """
    Flexibly parse key: value pairs from a message.
    Ignores case, spaces, underscores and dashes in keys.
    Returns a dict of cleaned_key -> value.
    """
    import re
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ':' not in line:
            continue
        key, _, value = line.partition(':')
        key_clean = re.sub(r'[\s_\-]+', '', key).lower()
        result[key_clean] = value.strip()
    return result


def get_email(fields):
    raw = fields.get('emailid') or fields.get('email', '')
    import re
    match = re.search(r'mailto:([^\|>]+)', raw)
    return match.group(1) if match else raw


def approval_message(mention, details_text, pr_number):
    """Build the standard approval card blocks."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Hey {mention}, {details_text}"
            }
        },
        {
            "type": "actions",
            "block_id": f"approval_{pr_number}",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "action_id": "approve_pr",
                    "value": str(pr_number)
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Decline"},
                    "style": "danger",
                    "action_id": "reject_pr",
                    "value": str(pr_number)
                }
            ]
        }
    ]


def is_authorized(user_id):
    """Check if user is allowed to approve/decline."""
    if not APPROVER_SLACK_ID:
        return True
    return user_id == APPROVER_SLACK_ID


def get_mention():
    return f"<@{APPROVER_SLACK_ID}>" if APPROVER_SLACK_ID else "admin"
