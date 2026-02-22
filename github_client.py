import re
import json
import logging
from github import Github
from config import (
    GITHUB_TOKEN, BASE_BRANCH,
    GITHUB_REPO, JSON_FILE_PATH,
    JSON_FILE_PATH_2
)

logger = logging.getLogger(__name__)

gh = Github(GITHUB_TOKEN)
repo1 = gh.get_repo(GITHUB_REPO)
repo2 = repo1  # same repo, different file


def _get_file(repo, filepath):
    """Read a JSON file from a GitHub repo and return (data, sha)."""
    file = repo.get_contents(filepath, ref=BASE_BRANCH)
    return json.loads(file.decoded_content), file.sha


def _safe_branch_name(*parts):
    """Generate a clean git branch name from arbitrary strings."""
    combined = "-".join(parts)
    return re.sub(r'[^a-zA-Z0-9-]', '-', combined)


def _create_branch(repo, branch_name):
    """Create a new branch off BASE_BRANCH."""
    source = repo.get_branch(BASE_BRANCH)
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=source.commit.sha)


def _commit_file(repo, filepath, content, message, branch_name, sha):
    """Commit updated file content to a branch."""
    repo.update_file(
        path=filepath,
        message=message,
        content=content,
        sha=sha,
        branch=branch_name
    )


def _open_pr(repo, title, body, branch_name):
    """Open a pull request."""
    return repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=BASE_BRANCH
    )


def merge_pr(pr_number):
    """Merge a PR by number — works across both repos."""
    # Try repo1 first, then repo2
    for repo in [repo1, repo2]:
        try:
            pr = repo.get_pull(pr_number)
            pr.merge(merge_method="squash")
            return pr
        except Exception:
            continue
    raise Exception(f"PR #{pr_number} not found in any configured repo.")


def close_pr(pr_number):
    """Close (decline) a PR by number — works across both repos."""
    for repo in [repo1, repo2]:
        try:
            pr = repo.get_pull(pr_number)
            pr.edit(state="closed")
            return pr
        except Exception:
            continue
    raise Exception(f"PR #{pr_number} not found in any configured repo.")


# ── Workflow 1: Publish List ──────────────────────────────────────
def create_publish_list_pr(email, client_id, slack_user, description=None):
    """Add email + client_id to the publish list JSON and open a PR."""
    data, sha = _get_file(repo1, JSON_FILE_PATH)
    data[email] = [client_id]

    branch_name = f"update/{_safe_branch_name(email.split('@')[0], client_id)}"
    _create_branch(repo1, branch_name)
    _commit_file(
        repo1, JSON_FILE_PATH,
        json.dumps(data, indent=4),
        f"Add {email} with client ID {client_id}",
        branch_name, sha
    )

    body = f"Requested by {slack_user} via Slack.\n\n**Email:** {email}\n**Client ID:** {client_id}"
    if description:
        body += f"\n**Description:** {description}"

    return _open_pr(repo1, f"Add client: {email} [{client_id}]", body, branch_name)


# ── Workflow 2: Workflow Whitelist ────────────────────────────────
def create_whitelist_pr(workflow_id, app_id, slack_user, reason=None):
    """Add app_id to a workflow's whitelist and open a PR."""
    data, sha = _get_file(repo2, JSON_FILE_PATH_2)

    existing = data.get(workflow_id, [])
    if app_id not in existing:
        existing.append(app_id)
    data[workflow_id] = existing

    branch_name = f"update/{_safe_branch_name('wf', workflow_id, app_id)}"
    _create_branch(repo2, branch_name)
    _commit_file(
        repo2, JSON_FILE_PATH_2,
        json.dumps(data, indent=4),
        f"Add {app_id} to workflow {workflow_id}",
        branch_name, sha
    )

    body = f"Requested by {slack_user} via Slack.\n\n**Workflow ID:** {workflow_id}\n**App ID:** {app_id}"
    if reason:
        body += f"\n**Reason:** {reason}"

    return _open_pr(repo2, f"Add {app_id} to workflow {workflow_id}", body, branch_name), existing
