from typing import Any

from personal_assistant.clients.jira import JiraIssue
from personal_assistant.settings import Settings

from personal_assistant.tools.jira import (
    combine_jql_with_updated_today,
    create_jira_issue,
    format_jira_issues,
    get_jira_tasks,
    update_jira_issue_fields,
)


def test_format_jira_issues() -> None:
    issues = [
        JiraIssue(
            key="PA-1",
            summary="Add Jira task fetching",
            status="To Do",
            priority="High",
            assignee="Oleg",
            url="https://example.atlassian.net/browse/PA-1",
        )
    ]

    output = format_jira_issues(issues)

    assert "PA-1: Add Jira task fetching" in output
    assert "To Do" in output
    assert "https://example.atlassian.net/browse/PA-1" in output


def test_combine_jql_with_updated_today_keeps_assignee_and_replaces_order() -> None:
    output = combine_jql_with_updated_today(
        "assignee = currentUser() ORDER BY priority DESC"
    )

    assert (
        output
        == "(assignee = currentUser()) AND (updated >= startOfDay()) ORDER BY updated DESC"
    )


def test_get_jira_tasks_filters_by_current_user_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubJiraClient:
        def __init__(self, settings: Settings) -> None:
            pass

        def search_issues(self, *, jql: str, limit: int = 5) -> list[JiraIssue]:
            captured["jql"] = jql
            captured["limit"] = limit
            return []

    monkeypatch.setattr("personal_assistant.tools.jira.JiraClient", StubJiraClient)

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"
    )

    get_jira_tasks(settings, limit=7)

    assert captured == {
        "jql": "assignee = currentUser() ORDER BY updated DESC",
        "limit": 7,
    }


def test_get_jira_tasks_filters_by_configured_account(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubJiraClient:
        def __init__(self, settings: Settings) -> None:
            pass

        def search_issues(self, *, jql: str, limit: int = 5) -> list[JiraIssue]:
            captured["jql"] = jql
            return []

    monkeypatch.setattr("personal_assistant.tools.jira.JiraClient", StubJiraClient)

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
        JIRA_ACCOUNT_ID="abc123",
    )

    get_jira_tasks(settings)

    assert captured["jql"] == 'assignee = "abc123" ORDER BY updated DESC'


def test_update_jira_issue_fields_maps_description_add_to_append(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class StubJiraClient:
        def __init__(self, settings: Settings) -> None:
            pass

        def append_description(self, issue_key: str, text: str) -> None:
            captured["append_description"] = {"issue_key": issue_key, "text": text}

        def update_issue_fields(self, issue_key: str, fields: dict[str, Any]) -> None:
            captured["update_issue_fields"] = {"issue_key": issue_key, "fields": fields}

    monkeypatch.setattr("personal_assistant.tools.jira.JiraClient", StubJiraClient)

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
        JIRA_PROJECT_KEY="CCO",
    )

    output = update_jira_issue_fields(
        settings,
        issue_key="2288",
        fields={"description_add": "websocket route = nchan/sub/1/pdf_signatures"},
    )

    assert output == "CCO-2288: обновлены поля description."
    assert captured == {
        "append_description": {
            "issue_key": "CCO-2288",
            "text": "websocket route = nchan/sub/1/pdf_signatures",
        }
    }


def test_update_jira_issue_fields_maps_string_description_to_append(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class StubJiraClient:
        def __init__(self, settings: Settings) -> None:
            pass

        def append_description(self, issue_key: str, text: str) -> None:
            captured["append_description"] = {"issue_key": issue_key, "text": text}

        def update_issue_fields(self, issue_key: str, fields: dict[str, Any]) -> None:
            captured["update_issue_fields"] = {"issue_key": issue_key, "fields": fields}

    monkeypatch.setattr("personal_assistant.tools.jira.JiraClient", StubJiraClient)

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
        JIRA_PROJECT_KEY="CCO",
    )

    output = update_jira_issue_fields(
        settings,
        issue_key="2288",
        fields={"description": "Добавить websocket route = nchan/sub/1/pdf_signatures"},
    )

    assert output == "CCO-2288: обновлены поля description."
    assert captured == {
        "append_description": {
            "issue_key": "CCO-2288",
            "text": "Добавить websocket route = nchan/sub/1/pdf_signatures",
        }
    }


def test_create_jira_issue_uses_parent_project_key(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class StubJiraClient:
        def __init__(self, settings: Settings) -> None:
            pass

        def create_issue(self, **kwargs: Any) -> JiraIssue:
            captured.update(kwargs)
            return JiraIssue(
                key="CCO-2000",
                summary=kwargs["summary"],
                status="To Do",
                priority=None,
                assignee=None,
                url="https://example.atlassian.net/browse/CCO-2000",
            )

    monkeypatch.setattr("personal_assistant.tools.jira.JiraClient", StubJiraClient)

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"
    )

    issue = create_jira_issue(
        settings,
        summary="Добавить DocumentUploads в просмотре документов",
        issue_type="Task",
        parent_key="CCO-1914",
    )

    assert issue.key == "CCO-2000"
    assert captured == {
        "project_key": "CCO",
        "summary": "Добавить DocumentUploads в просмотре документов",
        "issue_type": "Task",
        "description": None,
        "parent_key": "CCO-1914",
    }
