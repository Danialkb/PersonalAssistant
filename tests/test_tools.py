from jira.jira_client import JiraIssue
from jira.settings import Settings
from jira.tools import format_jira_issues, get_jira_tasks


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


def test_get_jira_tasks_filters_by_current_user_by_default(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StubJiraClient:
        def __init__(self, settings: Settings) -> None:
            pass

        def search_issues(self, *, jql: str, limit: int = 5) -> list[JiraIssue]:
            captured["jql"] = jql
            captured["limit"] = limit
            return []

    monkeypatch.setattr("jira.tools.JiraClient", StubJiraClient)

    settings = Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token")

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

    monkeypatch.setattr("jira.tools.JiraClient", StubJiraClient)

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
        JIRA_ACCOUNT_ID="abc123",
    )

    get_jira_tasks(settings)

    assert captured["jql"] == 'assignee = "abc123" ORDER BY updated DESC'
