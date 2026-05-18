from typing import Any

import httpx
import pytest

from personal_assistant.clients.gitlab import GitlabClient
from personal_assistant.settings import Settings


def make_settings() -> Settings:
    return Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="jira-token",
        GITLAB_BASE_URL="https://gitlab.example.com",
        GITLAB_TOKEN="gitlab-token",
    )


def test_get_project_encodes_namespace_path_and_sends_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        request = httpx.Request(method, url)
        return httpx.Response(
            200,
            json={
                "id": 42,
                "name": "Assistant",
                "path_with_namespace": "team/assistant",
                "web_url": "https://gitlab.example.com/team/assistant",
                "default_branch": "main",
            },
            request=request,
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    project = GitlabClient(make_settings()).get_project("team/assistant")

    assert captured["method"] == "GET"
    assert (
        captured["url"] == "https://gitlab.example.com/api/v4/projects/team%2Fassistant"
    )
    assert captured["headers"]["PRIVATE-TOKEN"] == "gitlab-token"
    assert project.id == 42
    assert project.path_with_namespace == "team/assistant"
    assert project.default_branch == "main"


def test_base_url_can_already_point_to_api_v4(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        request = httpx.Request(method, url)
        return httpx.Response(
            200,
            json={
                "id": 1,
                "username": "oleg",
                "name": "Oleg",
                "web_url": "https://gitlab.example.com/oleg",
            },
            request=request,
        )

    monkeypatch.setattr(httpx, "request", fake_request)
    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="jira-token",
        GITLAB_BASE_URL="https://gitlab.example.com/api/v4",
        GITLAB_TOKEN="gitlab-token",
    )

    user = GitlabClient(settings).get_current_user()

    assert captured["url"] == "https://gitlab.example.com/api/v4/user"
    assert user.username == "oleg"


def test_list_merge_requests_parses_nested_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        request = httpx.Request(method, url)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 100,
                    "iid": 7,
                    "title": "Add GitLab client",
                    "state": "opened",
                    "source_branch": "feature/gitlab",
                    "target_branch": "main",
                    "web_url": "https://gitlab.example.com/team/assistant/-/merge_requests/7",
                    "author": {"id": 1, "username": "oleg", "name": "Oleg"},
                    "assignees": [{"id": 2, "username": "anna", "name": "Anna"}],
                    "reviewers": [{"id": 3, "username": "ivan", "name": "Ivan"}],
                    "draft": True,
                    "created_at": "2026-05-16T09:00:00.000Z",
                    "updated_at": "2026-05-16T10:00:00.000Z",
                }
            ],
            request=request,
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    merge_requests = GitlabClient(make_settings()).list_merge_requests(
        project=42, limit=5
    )

    assert len(merge_requests) == 1
    merge_request = merge_requests[0]
    assert merge_request.iid == 7
    assert merge_request.author is not None
    assert merge_request.author.username == "oleg"
    assert merge_request.assignees[0].username == "anna"
    assert merge_request.reviewers[0].username == "ivan"
    assert merge_request.draft is True


def test_get_merge_request_changes_parses_file_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["params"] = kwargs["params"]
        request = httpx.Request(method, url)
        return httpx.Response(
            200,
            json=[
                {
                    "old_path": "old.py",
                    "new_path": "new.py",
                    "diff": "@@ -1 +1 @@\n-old\n+new",
                    "new_file": False,
                    "renamed_file": True,
                    "deleted_file": False,
                }
            ],
            headers={"X-Next-Page": ""},
            request=request,
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    changes = GitlabClient(make_settings()).get_merge_request_changes(
        "team/assistant", 7
    )

    assert (
        captured["url"]
        == "https://gitlab.example.com/api/v4/projects/team%2Fassistant/merge_requests/7/diffs"
    )
    assert captured["params"] == {"per_page": 100, "page": "1"}
    assert len(changes) == 1
    assert changes[0].file_path == "new.py"
    assert changes[0].renamed_file is True
    assert changes[0].diff == "@@ -1 +1 @@\n-old\n+new"


def test_get_merge_request_changes_follows_diff_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages: list[str] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        page = kwargs["params"]["page"]
        pages.append(page)
        request = httpx.Request(method, url)
        return httpx.Response(
            200,
            json=[
                {
                    "old_path": f"old-{page}.py",
                    "new_path": f"new-{page}.py",
                    "diff": f"@@ page {page}",
                }
            ],
            headers={"X-Next-Page": "2" if page == "1" else ""},
            request=request,
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    changes = GitlabClient(make_settings()).get_merge_request_changes(
        "team/assistant", 7
    )

    assert pages == ["1", "2"]
    assert [change.file_path for change in changes] == ["new-1.py", "new-2.py"]


def test_gitlab_client_requires_settings() -> None:
    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="jira-token",
        GITLAB_BASE_URL=None,
        GITLAB_TOKEN=None,
    )

    with pytest.raises(ValueError, match="GITLAB_BASE_URL"):
        GitlabClient(settings)
