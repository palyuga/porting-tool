from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class BitbucketError(Exception):
    pass


@dataclass
class PullRequestInfo:
    pr_id: int
    title: str
    description: str
    source_branch: str
    commit_hash: str
    approved_reviewers: list[dict[str, str]] = field(default_factory=list)
    base_url: str = ""
    project_key: str = ""
    repo_slug: str = ""

    @property
    def url(self) -> str:
        return (
            f"{self.base_url}/projects/{self.project_key}"
            f"/repos/{self.repo_slug}/pull-requests/{self.pr_id}"
        )


def parse_pr_url(url: str) -> tuple[str, str, str, int]:
    """Parse a Bitbucket PR URL into (base_url, project_key, repo_slug, pr_id).

    Expected format:
      https://host/projects/PROJ/repos/repo-name/pull-requests/123
    """
    match = re.match(
        r"^(https?://[^/]+)/projects/([^/]+)/repos/([^/]+)/pull-requests/(\d+)",
        url.strip(),
    )
    if not match:
        raise BitbucketError(
            f"Invalid Bitbucket PR URL: {url}\n"
            "Expected format: https://<host>/projects/<PROJECT>/repos/<REPO>/pull-requests/<ID>"
        )
    return (
        match.group(1),
        match.group(2),
        match.group(3),
        int(match.group(4)),
    )


class BitbucketClient:
    """Minimal Bitbucket Data Center REST API client using only urllib."""

    API_PATH = "/rest/api/latest"

    def __init__(self, base_url: str, pat: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.pat = pat
        self._ssl_ctx = ssl.create_default_context()

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{self.API_PATH}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.pat}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, context=self._ssl_ctx) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                detail = exc.read().decode()
            except Exception:
                detail = ""

            if status == 401:
                raise BitbucketError(
                    "Authentication failed (HTTP 401).\n"
                    "Your Personal Access Token may be invalid or expired.\n"
                    "Generate a new token in Bitbucket: "
                    "Account > Manage account > Personal access tokens"
                ) from exc
            if status == 404:
                raise BitbucketError(
                    f"Resource not found (HTTP 404): {url}\n"
                    "Check that the PR URL, project, and repository are correct."
                ) from exc

            raise BitbucketError(
                f"Bitbucket API error (HTTP {status}): {url}\n{detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise BitbucketError(
                f"Cannot connect to Bitbucket at {self.base_url}\n"
                f"Error: {exc.reason}"
            ) from exc

    def get_pull_request(
        self, project: str, repo: str, pr_id: int
    ) -> PullRequestInfo:
        path = f"/projects/{project}/repos/{repo}/pull-requests/{pr_id}"
        data = self._request("GET", path)

        title = data.get("title", "")
        description = data.get("description", "")
        source_branch = data["fromRef"]["displayId"]

        commit_hash = data["fromRef"].get("latestCommit", "")
        if not commit_hash:
            commit_hash = data["fromRef"].get("id", "")

        approved = []
        for reviewer in data.get("reviewers", []):
            if reviewer.get("status") == "APPROVED":
                user = reviewer.get("user", {})
                approved.append({
                    "slug": user.get("slug", ""),
                    "name": user.get("name", user.get("slug", "")),
                    "displayName": user.get("displayName", user.get("name", "")),
                })
        for participant in data.get("participants", []):
            if (
                participant.get("status") == "APPROVED"
                and participant.get("role") == "REVIEWER"
            ):
                user = participant.get("user", {})
                slug = user.get("slug", "")
                if not any(r["slug"] == slug for r in approved):
                    approved.append({
                        "slug": slug,
                        "name": user.get("name", user.get("slug", "")),
                        "displayName": user.get("displayName", user.get("name", "")),
                    })

        return PullRequestInfo(
            pr_id=pr_id,
            title=title,
            description=description,
            source_branch=source_branch,
            commit_hash=commit_hash,
            approved_reviewers=approved,
            base_url=self.base_url,
            project_key=project,
            repo_slug=repo,
        )

    def create_pull_request(
        self,
        project: str,
        repo: str,
        title: str,
        description: str,
        from_branch: str,
        to_branch: str,
        reviewers: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        path = f"/projects/{project}/repos/{repo}/pull-requests"
        body: dict[str, Any] = {
            "title": title,
            "description": description,
            "fromRef": {"id": f"refs/heads/{from_branch}"},
            "toRef": {"id": f"refs/heads/{to_branch}"},
        }
        if reviewers:
            body["reviewers"] = [
                {"user": {"name": r["name"], "slug": r["slug"]}}
                for r in reviewers
            ]

        data = self._request("POST", path, body=body)
        return data

    def validate_token(self) -> bool:
        """Quick check that the PAT is valid by hitting a lightweight endpoint."""
        try:
            self._request("GET", "/application-properties")
            return True
        except BitbucketError:
            return False
