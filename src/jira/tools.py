from jira.jira_client import JiraClient, JiraIssue
from jira.settings import Settings


def format_jira_issues(issues: list[JiraIssue]) -> str:
    if not issues:
        return "Jira не вернула задач по текущему запросу."

    lines = ["Задачи из Jira:"]
    for issue in issues:
        priority = f", priority: {issue.priority}" if issue.priority else ""
        assignee = f", assignee: {issue.assignee}" if issue.assignee else ""
        lines.append(f"- {issue.key}: {issue.summary} [{issue.status}{priority}{assignee}]")
        lines.append(f"  {issue.url}")
    return "\n".join(lines)


def get_jira_tasks(settings: Settings, *, limit: int = 5, jql: str | None = None) -> str:
    client = JiraClient(settings)
    issues = client.search_issues(jql=jql or settings.default_jira_jql, limit=limit)
    return format_jira_issues(issues)
