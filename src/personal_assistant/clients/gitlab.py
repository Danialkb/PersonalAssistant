from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from personal_assistant.clients.base import HttpApiClient
from personal_assistant.settings import Settings


@dataclass(frozen=True)
class GitlabUser:
    id: int
    username: str
    name: str
    web_url: str | None = None


@dataclass(frozen=True)
class GitlabProject:
    id: int
    name: str
    path_with_namespace: str
    web_url: str
    default_branch: str | None = None


@dataclass(frozen=True)
class GitlabMergeRequest:
    id: int
    iid: int
    title: str
    state: str
    source_branch: str
    target_branch: str
    web_url: str
    author: GitlabUser | None = None
    assignees: tuple[GitlabUser, ...] = ()
    reviewers: tuple[GitlabUser, ...] = ()
    draft: bool = False
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class GitlabIssue:
    id: int
    iid: int
    title: str
    state: str
    web_url: str
    author: GitlabUser | None = None
    assignees: tuple[GitlabUser, ...] = ()
    labels: tuple[str, ...] = ()
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class GitlabPipeline:
    id: int
    status: str
    ref: str
    sha: str
    web_url: str
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class GitlabMergeRequestChange:
    old_path: str
    new_path: str
    diff: str
    new_file: bool = False
    renamed_file: bool = False
    deleted_file: bool = False

    @property
    def file_path(self) -> str:
        return self.new_path or self.old_path


class GitlabClient(HttpApiClient):
    service_name = "GitLab"

    def __init__(self, settings: Settings) -> None:
        if not settings.GITLAB_BASE_URL:
            raise ValueError("GITLAB_BASE_URL is required to use GitLab")
        if not settings.GITLAB_TOKEN:
            raise ValueError("GITLAB_TOKEN is required to use GitLab")

        super().__init__(self._api_base_url(settings.GITLAB_BASE_URL))
        self._settings = settings

    def get_current_user(self) -> GitlabUser:
        response = self._get("/user", headers=self._headers())
        return self._parse_user(response.json())

    def search_projects(self, search: str, *, limit: int = 10) -> list[GitlabProject]:
        response = self._get(
            "/projects",
            params={"search": search, "simple": True, "per_page": limit},
            headers=self._headers(),
        )
        return [self._parse_project(project) for project in response.json()]

    def get_project(self, project: int | str) -> GitlabProject:
        response = self._get(
            f"/projects/{self._project_id(project)}", headers=self._headers()
        )
        return self._parse_project(response.json())

    def list_merge_requests(
        self,
        *,
        project: int | str | None = None,
        state: str = "opened",
        scope: str | None = None,
        author_username: str | None = None,
        assignee_username: str | None = None,
        reviewer_username: str | None = None,
        limit: int = 20,
    ) -> list[GitlabMergeRequest]:
        path = "/merge_requests"
        if project is not None:
            path = f"/projects/{self._project_id(project)}/merge_requests"

        params = self._clean_params(
            {
                "state": state,
                "scope": scope,
                "author_username": author_username,
                "assignee_username": assignee_username,
                "reviewer_username": reviewer_username,
                "per_page": limit,
            }
        )
        response = self._get(path, params=params, headers=self._headers())
        return [self._parse_merge_request(mr) for mr in response.json()]

    def get_merge_request(self, project: int | str, iid: int) -> GitlabMergeRequest:
        response = self._get(
            f"/projects/{self._project_id(project)}/merge_requests/{iid}",
            headers=self._headers(),
        )
        return self._parse_merge_request(response.json())

    def get_merge_request_changes(
        self, project: int | str, iid: int
    ) -> list[GitlabMergeRequestChange]:
        path = f"/projects/{self._project_id(project)}/merge_requests/{iid}/diffs"
        try:
            changes = self._get_paginated(path, params={"per_page": 100})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            changes = self._get_legacy_merge_request_changes(project, iid)

        return [self._parse_merge_request_change(change) for change in changes]

    def create_merge_request_note(
        self, project: int | str, iid: int, body: str
    ) -> None:
        self._post(
            f"/projects/{self._project_id(project)}/merge_requests/{iid}/notes",
            json={"body": body},
            headers=self._json_headers(),
        )

    def approve_merge_request(self, project: int | str, iid: int) -> None:
        self._post(
            f"/projects/{self._project_id(project)}/merge_requests/{iid}/approve",
            headers=self._headers(),
        )

    def list_issues(
        self,
        *,
        project: int | str | None = None,
        state: str = "opened",
        scope: str | None = None,
        assignee_username: str | None = None,
        author_username: str | None = None,
        labels: str | None = None,
        limit: int = 20,
    ) -> list[GitlabIssue]:
        path = "/issues"
        if project is not None:
            path = f"/projects/{self._project_id(project)}/issues"

        params = self._clean_params(
            {
                "state": state,
                "scope": scope,
                "assignee_username": assignee_username,
                "author_username": author_username,
                "labels": labels,
                "per_page": limit,
            }
        )
        response = self._get(path, params=params, headers=self._headers())
        return [self._parse_issue(issue) for issue in response.json()]

    def get_issue(self, project: int | str, iid: int) -> GitlabIssue:
        response = self._get(
            f"/projects/{self._project_id(project)}/issues/{iid}",
            headers=self._headers(),
        )
        return self._parse_issue(response.json())

    def create_issue_note(self, project: int | str, iid: int, body: str) -> None:
        self._post(
            f"/projects/{self._project_id(project)}/issues/{iid}/notes",
            json={"body": body},
            headers=self._json_headers(),
        )

    def list_pipelines(
        self,
        project: int | str,
        *,
        ref: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[GitlabPipeline]:
        response = self._get(
            f"/projects/{self._project_id(project)}/pipelines",
            params=self._clean_params(
                {"ref": ref, "status": status, "per_page": limit}
            ),
            headers=self._headers(),
        )
        return [self._parse_pipeline(pipeline) for pipeline in response.json()]

    def get_pipeline(self, project: int | str, pipeline_id: int) -> GitlabPipeline:
        response = self._get(
            f"/projects/{self._project_id(project)}/pipelines/{pipeline_id}",
            headers=self._headers(),
        )
        return self._parse_pipeline(response.json())

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "PRIVATE-TOKEN": self._settings.GITLAB_TOKEN or "",
        }

    def _json_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _api_base_url(base_url: str) -> str:
        base_url = base_url.rstrip("/")
        if base_url.endswith("/api/v4"):
            return base_url
        return f"{base_url}/api/v4"

    @staticmethod
    def _project_id(project: int | str) -> str:
        if isinstance(project, int):
            return str(project)
        return quote(project, safe="")

    @staticmethod
    def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in params.items() if value is not None}

    def _get_paginated(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        page = "1"
        items: list[dict[str, Any]] = []
        while page:
            page_params = dict(params or {})
            page_params["page"] = page
            response = self._get(path, params=page_params, headers=self._headers())
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError(f"Expected GitLab list response for {path}")
            items.extend(payload)
            page = response.headers.get("X-Next-Page", "").strip()
        return items

    def _get_legacy_merge_request_changes(
        self, project: int | str, iid: int
    ) -> list[dict[str, Any]]:
        response = self._get(
            f"/projects/{self._project_id(project)}/merge_requests/{iid}/changes",
            headers=self._headers(),
        )
        return list(response.json().get("changes", []))

    def _parse_project(self, project: dict[str, Any]) -> GitlabProject:
        return GitlabProject(
            id=int(project["id"]),
            name=project.get("name") or "",
            path_with_namespace=project.get("path_with_namespace") or "",
            web_url=project.get("web_url") or "",
            default_branch=project.get("default_branch"),
        )

    def _parse_merge_request(self, mr: dict[str, Any]) -> GitlabMergeRequest:
        return GitlabMergeRequest(
            id=int(mr["id"]),
            iid=int(mr["iid"]),
            title=mr.get("title") or "",
            state=mr.get("state") or "",
            source_branch=mr.get("source_branch") or "",
            target_branch=mr.get("target_branch") or "",
            web_url=mr.get("web_url") or "",
            author=self._parse_optional_user(mr.get("author")),
            assignees=tuple(
                self._parse_user(user) for user in mr.get("assignees") or ()
            ),
            reviewers=tuple(
                self._parse_user(user) for user in mr.get("reviewers") or ()
            ),
            draft=bool(mr.get("draft") or mr.get("work_in_progress")),
            created_at=mr.get("created_at"),
            updated_at=mr.get("updated_at"),
        )

    @staticmethod
    def _parse_merge_request_change(change: dict[str, Any]) -> GitlabMergeRequestChange:
        return GitlabMergeRequestChange(
            old_path=change.get("old_path") or "",
            new_path=change.get("new_path") or "",
            diff=change.get("diff") or "",
            new_file=bool(change.get("new_file")),
            renamed_file=bool(change.get("renamed_file")),
            deleted_file=bool(change.get("deleted_file")),
        )

    def _parse_issue(self, issue: dict[str, Any]) -> GitlabIssue:
        return GitlabIssue(
            id=int(issue["id"]),
            iid=int(issue["iid"]),
            title=issue.get("title") or "",
            state=issue.get("state") or "",
            web_url=issue.get("web_url") or "",
            author=self._parse_optional_user(issue.get("author")),
            assignees=tuple(
                self._parse_user(user) for user in issue.get("assignees") or ()
            ),
            labels=tuple(str(label) for label in issue.get("labels") or ()),
            created_at=issue.get("created_at"),
            updated_at=issue.get("updated_at"),
        )

    def _parse_pipeline(self, pipeline: dict[str, Any]) -> GitlabPipeline:
        return GitlabPipeline(
            id=int(pipeline["id"]),
            status=pipeline.get("status") or "",
            ref=pipeline.get("ref") or "",
            sha=pipeline.get("sha") or "",
            web_url=pipeline.get("web_url") or "",
            created_at=pipeline.get("created_at"),
            updated_at=pipeline.get("updated_at"),
        )

    def _parse_optional_user(self, user: Any) -> GitlabUser | None:
        return self._parse_user(user) if isinstance(user, dict) else None

    @staticmethod
    def _parse_user(user: dict[str, Any]) -> GitlabUser:
        return GitlabUser(
            id=int(user["id"]),
            username=user.get("username") or "",
            name=user.get("name") or "",
            web_url=user.get("web_url"),
        )

    def _response_error_details(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip()

        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, dict):
            return "; ".join(
                f"{field}: {', '.join(map(str, errors))}"
                for field, errors in message.items()
            )
        if isinstance(message, list):
            return "; ".join(str(item) for item in message)
        if message:
            return str(message)
        return str(payload)
