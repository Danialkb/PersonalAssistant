from dataclasses import dataclass
from typing import Any

import httpx

from jira.settings import Settings


@dataclass(frozen=True)
class JiraIssue:
    key: str
    summary: str
    status: str
    priority: str | None
    assignee: str | None
    url: str


class JiraClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.jira_base_url.rstrip("/")

    def search_issues(self, *, jql: str, limit: int = 5) -> list[JiraIssue]:
        response = httpx.get(
            f"{self._base_url}/rest/api/3/search/jql",
            params={
                "jql": jql,
                "maxResults": limit,
                "fields": "summary,status,priority,assignee",
            },
            auth=self._auth(),
            headers=self._headers(),
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return [self._parse_issue(issue) for issue in payload.get("issues", [])]

    def _auth(self) -> httpx.Auth | None:
        if self._settings.jira_auth_mode == "basic" or (
            self._settings.jira_auth_mode == "auto" and self._settings.jira_email
        ):
            if not self._settings.jira_email:
                raise ValueError("JIRA_EMAIL is required when JIRA_AUTH_MODE=basic")
            return httpx.BasicAuth(self._settings.jira_email, self._settings.jira_api_key)
        return None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._settings.jira_auth_mode == "bearer" or (
            self._settings.jira_auth_mode == "auto" and not self._settings.jira_email
        ):
            headers["Authorization"] = f"Bearer {self._settings.jira_api_key}"
        return headers

    def _parse_issue(self, issue: dict[str, Any]) -> JiraIssue:
        fields = issue.get("fields", {})
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        assignee = fields.get("assignee") or {}
        key = issue["key"]

        return JiraIssue(
            key=key,
            summary=fields.get("summary") or "",
            status=status.get("name") or "Unknown",
            priority=priority.get("name"),
            assignee=assignee.get("displayName"),
            url=f"{self._base_url}/browse/{key}",
        )
