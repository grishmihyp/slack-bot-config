import os
from dotenv import load_dotenv

load_dotenv()

# ── Slack ─────────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
APPROVER_SLACK_ID = os.environ.get("APPROVER_SLACK_ID", "")

# ── GitHub ────────────────────────────────────────────────────────
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
BASE_BRANCH = os.environ.get("BASE_BRANCH", "main")

# ── Workflow 1: Publish List ──────────────────────────────────────
GITHUB_REPO = os.environ["GITHUB_REPO"]
JSON_FILE_PATH = os.environ.get("JSON_FILE_PATH", "client-data.json")

# ── Workflow 2: Whitelist (same repo, different file) ────────────
JSON_FILE_PATH_2 = os.environ.get("JSON_FILE_PATH_2", "workflow-whitelist.json")
