import re
from typing import Any

from personal_assistant.clients.jira import JiraClient, JiraIssue, JiraTransition
from personal_assistant.settings import Settings


def combine_jql_with_text_search(default_jql: str, search_text: str) -> str:
    base_jql = _strip_order_by(default_jql)
    text_jql = f'text ~ "{_escape_jql_string(search_text)}"'
    return f"({base_jql}) AND ({text_jql}) ORDER BY updated DESC"


def combine_jql_with_updated_today(default_jql: str) -> str:
    base_jql = _strip_order_by(default_jql)
    return f"({base_jql}) AND (updated >= startOfDay()) ORDER BY updated DESC"


def format_jira_issues(issues: list[JiraIssue]) -> str:
    if not issues:
        return "Jira не вернула задач по текущему запросу."

    lines = ["Задачи из Jira:"]
    for issue in issues:
        priority = f", priority: {issue.priority}" if issue.priority else ""
        assignee = f", assignee: {issue.assignee}" if issue.assignee else ""
        lines.append(
            f"- {issue.key}: {issue.summary} [{issue.status}{priority}{assignee}]"
        )
        lines.append(f"  {issue.url}")
    return "\n".join(lines)


def format_jira_issue(issue: JiraIssue) -> str:
    lines = [
        f"{issue.key}: {issue.summary}",
        f"Status: {issue.status}",
        f"URL: {issue.url}",
    ]
    if issue.issue_type:
        lines.append(f"Type: {issue.issue_type}")
    if issue.priority:
        lines.append(f"Priority: {issue.priority}")
    if issue.assignee:
        lines.append(f"Assignee: {issue.assignee}")
    if issue.reporter:
        lines.append(f"Reporter: {issue.reporter}")
    if issue.due_date:
        lines.append(f"Due date: {issue.due_date}")
    if issue.labels:
        lines.append(f"Labels: {', '.join(issue.labels)}")
    if issue.updated:
        lines.append(f"Updated: {issue.updated}")
    if issue.comments_count is not None:
        lines.append(f"Comments: {issue.comments_count}")
    if issue.description:
        lines.append("")
        lines.append(issue.description)
    return "\n".join(lines)


def format_jira_transitions(transitions: list[JiraTransition]) -> str:
    if not transitions:
        return "Для этой задачи нет доступных переходов статуса."
    return "Доступные переходы:\n" + "\n".join(
        f"- {transition.name}"
        + (f" -> {transition.target_status}" if transition.target_status else "")
        for transition in transitions
    )


def get_jira_tasks(
    settings: Settings, *, limit: int = 5, jql: str | None = None
) -> str:
    client = JiraClient(settings)
    issues = client.search_issues(jql=jql or settings.default_jira_jql, limit=limit)
    return format_jira_issues(issues)


def resolve_jira_issue_key(settings: Settings, issue_key: str) -> str:
    match = re.search(r"[A-Z][A-Z0-9]+-\d+", issue_key.upper())
    if match:
        return match.group(0)

    number_match = re.search(r"\b\d+\b", issue_key)
    if number_match and settings.JIRA_PROJECT_KEY:
        return f"{settings.JIRA_PROJECT_KEY.upper()}-{number_match.group(0)}"

    return issue_key.strip().upper()


def search_jira_issues(
    settings: Settings, *, jql: str, limit: int = 10
) -> list[JiraIssue]:
    return JiraClient(settings).search_issues(jql=jql, limit=limit)


def get_jira_issue(settings: Settings, *, issue_key: str) -> JiraIssue:
    issue_key = resolve_jira_issue_key(settings, issue_key)
    return JiraClient(settings).get_issue(issue_key)


def create_jira_issue(
    settings: Settings,
    *,
    summary: str,
    issue_type: str = "Task",
    description: str | None = None,
    parent_key: str | None = None,
    project_key: str | None = None,
) -> JiraIssue:
    if parent_key:
        parent_key = resolve_jira_issue_key(settings, parent_key)
    resolved_project_key = project_key or settings.JIRA_PROJECT_KEY
    if not resolved_project_key and parent_key and "-" in parent_key:
        resolved_project_key = parent_key.split("-", 1)[0]
    if not resolved_project_key:
        raise ValueError(
            "JIRA_PROJECT_KEY is required to create a Jira issue without a parent key"
        )

    return JiraClient(settings).create_issue(
        project_key=resolved_project_key,
        summary=summary,
        issue_type=issue_type,
        description=description,
        parent_key=parent_key,
    )


def get_jira_transitions(settings: Settings, *, issue_key: str) -> list[JiraTransition]:
    issue_key = resolve_jira_issue_key(settings, issue_key)
    return JiraClient(settings).get_transitions(issue_key)


def find_jira_transition(
    transitions: list[JiraTransition], requested: str
) -> JiraTransition | None:
    normalized = requested.strip().lower()
    for transition in transitions:
        if (
            transition.name.lower() == normalized
            or (transition.target_status or "").lower() == normalized
        ):
            return transition
    partial_matches = [
        transition
        for transition in transitions
        if normalized
        and (
            normalized in transition.name.lower()
            or normalized in (transition.target_status or "").lower()
            or transition.name.lower() in normalized
            or (transition.target_status or "").lower() in normalized
        )
    ]
    if len(partial_matches) == 1:
        return partial_matches[0]
    return None


def transition_jira_issue(
    settings: Settings, *, issue_key: str, transition_name: str
) -> str:
    issue_key = resolve_jira_issue_key(settings, issue_key)
    transition = JiraClient(settings).transition_issue(issue_key, transition_name)
    if transition.target_status:
        return f"{issue_key}: статус изменен на {transition.target_status} через transition {transition.name}."
    return f"{issue_key}: выполнен transition {transition.name}."


def add_jira_comment(settings: Settings, *, issue_key: str, text: str) -> str:
    issue_key = resolve_jira_issue_key(settings, issue_key)
    JiraClient(settings).add_comment(issue_key, text)
    return f"{issue_key}: комментарий добавлен."


def update_jira_issue_fields(
    settings: Settings, *, issue_key: str, fields: dict[str, Any]
) -> str:
    issue_key = resolve_jira_issue_key(settings, issue_key)
    client = JiraClient(settings)
    fields = dict(fields)
    description_add = fields.pop("description_add", None)
    description = fields.pop("description", None)
    if description_add:
        client.append_description(issue_key, str(description_add))
    if isinstance(description, str):
        client.append_description(issue_key, description)
    elif description is not None:
        fields["description"] = description
    if fields:
        client.update_issue_fields(issue_key, fields)
    changed_fields = sorted(fields)
    if description_add or description is not None:
        changed_fields.insert(0, "description")
    changed = ", ".join(changed_fields) or "fields"
    return f"{issue_key}: обновлены поля {changed}."


def _strip_order_by(jql: str) -> str:
    marker = " ORDER BY "
    upper_jql = jql.upper()
    marker_index = upper_jql.rfind(marker)
    if marker_index == -1:
        return jql
    return jql[:marker_index].strip()


def _escape_jql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
