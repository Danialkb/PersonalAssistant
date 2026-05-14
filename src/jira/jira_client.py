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
    issue_type: str | None = None
    description: str | None = None
    reporter: str | None = None
    updated: str | None = None
    due_date: str | None = None
    labels: tuple[str, ...] = ()
    comments_count: int | None = None


@dataclass(frozen=True)
class JiraTransition:
    id: str
    name: str
    target_status: str | None = None


class JiraClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.JIRA_BASE_URL.rstrip("/")

    def search_issues(self, *, jql: str, limit: int = 5) -> list[JiraIssue]:
        response = httpx.get(
            f"{self._base_url}/rest/api/3/search/jql",
            params={
                "jql": jql,
                "maxResults": limit,
                "fields": "summary,status,priority,assignee,issuetype,reporter,updated,duedate,labels,comment",
            },
            auth=self._auth(),
            headers=self._headers(),
            timeout=20,
        )
        self._raise_for_status(response)
        payload = response.json()
        return [self._parse_issue(issue) for issue in payload.get("issues", [])]

    def get_issue(self, issue_key: str) -> JiraIssue:
        response = httpx.get(
            f"{self._base_url}/rest/api/3/issue/{issue_key}",
            params={
                "fields": (
                    "summary,status,priority,assignee,issuetype,description,"
                    "reporter,updated,duedate,labels,comment"
                ),
            },
            auth=self._auth(),
            headers=self._headers(),
            timeout=20,
        )
        self._raise_for_status(response)
        return self._parse_issue(response.json())

    def create_issue(
        self,
        *,
        project_key: str,
        summary: str,
        issue_type: str,
        description: str | None = None,
        parent_key: str | None = None,
    ) -> JiraIssue:
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = self._adf_from_text(description)
        if parent_key:
            fields["parent"] = {"key": parent_key}

        response = httpx.post(
            f"{self._base_url}/rest/api/3/issue",
            json={"fields": fields},
            auth=self._auth(),
            headers=self._json_headers(),
            timeout=20,
        )
        self._raise_for_status(response)
        return self.get_issue(response.json()["key"])

    def get_issue_description(self, issue_key: str) -> dict[str, Any] | None:
        response = httpx.get(
            f"{self._base_url}/rest/api/3/issue/{issue_key}",
            params={"fields": "description"},
            auth=self._auth(),
            headers=self._headers(),
            timeout=20,
        )
        self._raise_for_status(response)
        description = response.json().get("fields", {}).get("description")
        return description if isinstance(description, dict) else None

    def get_transitions(self, issue_key: str) -> list[JiraTransition]:
        response = httpx.get(
            f"{self._base_url}/rest/api/3/issue/{issue_key}/transitions",
            auth=self._auth(),
            headers=self._headers(),
            timeout=20,
        )
        self._raise_for_status(response)
        transitions = response.json().get("transitions", [])
        return [
            JiraTransition(
                id=str(transition["id"]),
                name=transition.get("name") or "",
                target_status=(transition.get("to") or {}).get("name"),
            )
            for transition in transitions
        ]

    def transition_issue(self, issue_key: str, transition_name: str) -> JiraTransition:
        transitions = self.get_transitions(issue_key)
        transition = self._find_transition(transitions, transition_name)
        response = httpx.post(
            f"{self._base_url}/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": transition.id}},
            auth=self._auth(),
            headers=self._json_headers(),
            timeout=20,
        )
        self._raise_for_status(response)
        return transition

    def add_comment(self, issue_key: str, text: str) -> None:
        response = httpx.post(
            f"{self._base_url}/rest/api/3/issue/{issue_key}/comment",
            json={"body": self._adf_from_text(text)},
            auth=self._auth(),
            headers=self._json_headers(),
            timeout=20,
        )
        self._raise_for_status(response)

    def update_issue_fields(self, issue_key: str, fields: dict[str, Any]) -> None:
        response = httpx.put(
            f"{self._base_url}/rest/api/3/issue/{issue_key}",
            json={"fields": fields},
            auth=self._auth(),
            headers=self._json_headers(),
            timeout=20,
        )
        self._raise_for_status(response)

    def append_description(self, issue_key: str, text: str) -> None:
        current_description = self.get_issue_description(issue_key)
        new_description = self._append_adf_content(current_description, self._adf_from_text(text))
        self.update_issue_fields(issue_key, {"description": new_description})

    def _auth(self) -> httpx.Auth | None:
        if self._settings.JIRA_AUTH_MODE == "basic" or (
            self._settings.JIRA_AUTH_MODE == "auto" and self._settings.JIRA_EMAIL
        ):
            if not self._settings.JIRA_EMAIL:
                raise ValueError("JIRA_EMAIL is required when JIRA_AUTH_MODE=basic")
            return httpx.BasicAuth(self._settings.JIRA_EMAIL, self._settings.JIRA_API_KEY)
        return None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._settings.JIRA_AUTH_MODE == "bearer" or (
            self._settings.JIRA_AUTH_MODE == "auto" and not self._settings.JIRA_EMAIL
        ):
            headers["Authorization"] = f"Bearer {self._settings.JIRA_API_KEY}"
        return headers

    def _json_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        return headers

    def _parse_issue(self, issue: dict[str, Any]) -> JiraIssue:
        fields = issue.get("fields", {})
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        assignee = fields.get("assignee") or {}
        issue_type = fields.get("issuetype") or {}
        reporter = fields.get("reporter") or {}
        comment = fields.get("comment") or {}
        key = issue["key"]

        return JiraIssue(
            key=key,
            summary=fields.get("summary") or "",
            status=status.get("name") or "Unknown",
            priority=priority.get("name"),
            assignee=assignee.get("displayName"),
            url=f"{self._base_url}/browse/{key}",
            issue_type=issue_type.get("name"),
            description=self._plain_text(fields.get("description")),
            reporter=reporter.get("displayName"),
            updated=fields.get("updated"),
            due_date=fields.get("duedate"),
            labels=tuple(fields.get("labels") or ()),
            comments_count=comment.get("total"),
        )

    @staticmethod
    def _find_transition(transitions: list[JiraTransition], transition_name: str) -> JiraTransition:
        normalized = transition_name.strip().lower()
        for transition in transitions:
            if transition.name.lower() == normalized or (transition.target_status or "").lower() == normalized:
                return transition
        available = ", ".join(transition.name for transition in transitions) or "none"
        raise ValueError(f"Transition '{transition_name}' is not available. Available transitions: {available}")

    @classmethod
    def _plain_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            parts: list[str] = []
            cls._collect_text(value, parts)
            text = " ".join(part for part in parts if part).strip()
            return text or None
        return str(value)

    @classmethod
    def _collect_text(cls, node: Any, parts: list[str]) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and node.get("text"):
                parts.append(str(node["text"]))
            for child in node.get("content") or ():
                cls._collect_text(child, parts)
        elif isinstance(node, list):
            for child in node:
                cls._collect_text(child, parts)

    @classmethod
    def _adf_from_text(cls, text: str) -> dict[str, Any]:
        return {
            "type": "doc",
            "version": 1,
            "content": cls._adf_blocks(text),
        }

    @classmethod
    def _adf_blocks(cls, text: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        paragraph_lines: list[str] = []
        code_lines: list[str] = []
        code_language: str | None = None
        in_code = False

        def flush_paragraph() -> None:
            if paragraph_lines:
                blocks.append(cls._adf_paragraph("\n".join(paragraph_lines)))
                paragraph_lines.clear()

        def flush_code() -> None:
            if code_lines:
                block: dict[str, Any] = {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": "\n".join(code_lines)}],
                }
                if code_language:
                    block["attrs"] = {"language": code_language}
                blocks.append(block)
                code_lines.clear()

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if in_code:
                    flush_code()
                    in_code = False
                    code_language = None
                else:
                    flush_paragraph()
                    in_code = True
                    code_language = stripped.removeprefix("```").strip() or None
                continue
            if in_code:
                code_lines.append(line)
            elif stripped:
                paragraph_lines.append(line)
            else:
                flush_paragraph()

        if in_code:
            flush_code()
        flush_paragraph()
        return blocks or [cls._adf_paragraph(text)]

    @staticmethod
    def _adf_paragraph(text: str) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for index, line in enumerate(text.splitlines()):
            if index:
                content.append({"type": "hardBreak"})
            if line:
                content.append({"type": "text", "text": line})
        return {"type": "paragraph", "content": content}

    @staticmethod
    def _append_adf_content(current: dict[str, Any] | None, addition: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if current and current.get("type") == "doc":
            content.extend(current.get("content") or [])
        content.extend(addition.get("content") or [])
        return {
            "type": "doc",
            "version": 1,
            "content": content,
        }

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            details = JiraClient._response_error_details(response)
            message = str(exc)
            if details:
                message = f"{message}\nJira response: {details}"
            raise httpx.HTTPStatusError(message, request=exc.request, response=exc.response) from exc

    @staticmethod
    def _response_error_details(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip()

        parts: list[str] = []
        error_messages = payload.get("errorMessages")
        if error_messages:
            parts.extend(str(message) for message in error_messages)
        errors = payload.get("errors")
        if isinstance(errors, dict):
            parts.extend(f"{field}: {message}" for field, message in errors.items())
        return "; ".join(parts)
