from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from jira.settings import Settings
from jira.tools import (
    format_jira_issue,
    format_jira_transitions,
    get_jira_issue as fetch_jira_issue,
    get_jira_tasks as fetch_jira_tasks,
    get_jira_transitions as fetch_jira_transitions,
)


INSTRUCTIONS = (
    "You are a text-first CLI assistant. Answer in the user's language. "
    "Use tools for Jira requests instead of inventing task data. "
    "When fetching Jira tasks, default to 5 issues unless the user asks for another amount. "
    "Do not pass jql for a generic Jira task request; let the tool use the configured assignee filter. "
    "If the user asks for extra filtering, keep the assignee filter unless they explicitly ask otherwise."
)

PLANNER_INSTRUCTIONS = (
    "You convert a user's terminal request into one Jira command. "
    "Return only structured output. Use Russian messages when the user writes Russian. "
    "Choose answer for non-Jira small talk or when you need clarification. "
    "Choose search for read-only requests like daily overview, blockers, stale tasks, bugs, priority lists. "
    "Choose get_issue when the user asks to inspect, explain, summarize, or open one issue. "
    "Choose transition for status changes. Choose comment for adding Jira comments. "
    "Choose update_fields for priority, assignee, labels, due date, summary, or other field updates. "
    "When the user asks to add or append information to an issue description, use fields.description_add. "
    "Use fields.description only when the user explicitly asks to replace the whole description. "
    "For generic personal task searches, do not set jql; the app will use the configured default. "
    "For filtered searches, keep assignee = currentUser() unless the user explicitly asks otherwise. "
    "For writes, set needs_confirmation=true and fill issue_key plus the field needed for that action. "
    "If a write request does not identify an issue, choose answer and ask the user which issue to change."
)


class JiraCommand(BaseModel):
    action: Literal["answer", "search", "get_issue", "transition", "comment", "update_fields"] = "answer"
    message: str = ""
    issue_key: str | None = None
    jql: str | None = None
    limit: int = Field(default=5, ge=1, le=50)
    transition: str | None = None
    comment: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    needs_confirmation: bool = False

    @property
    def is_write(self) -> bool:
        return self.action in {"transition", "comment", "update_fields"}


def get_jira_tasks(ctx: RunContext[Settings], limit: int = 10, jql: str | None = None) -> str:
    """Fetch Jira issues for the user.

    Use this when the user asks to show, fetch, list, analyze, or prioritize Jira tasks.
    Omit jql to use the configured default Jira query.
    """
    return fetch_jira_tasks(ctx.deps, limit=limit, jql=jql)


def get_jira_issue(ctx: RunContext[Settings], issue_key: str) -> str:
    """Fetch one Jira issue by key and format its details."""
    return format_jira_issue(fetch_jira_issue(ctx.deps, issue_key=issue_key))


def get_jira_transitions(ctx: RunContext[Settings], issue_key: str) -> str:
    """Fetch available Jira workflow transitions for one issue."""
    return format_jira_transitions(fetch_jira_transitions(ctx.deps, issue_key=issue_key))


class AssistantAgent:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._agent: Agent[Settings, str] | None = None
        self._planner: Agent[None, JiraCommand] | None = None
        if not settings.OPENAI_API_KEY:
            return

        model = OpenAIChatModel(
            self._openai_model_name(settings.OPENAI_MODEL),
            provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY),
        )
        self._agent = Agent(
            model,
            deps_type=Settings,
            tools=[get_jira_tasks, get_jira_issue, get_jira_transitions],
            instructions=INSTRUCTIONS,
        )
        self._planner = Agent(
            model,
            output_type=JiraCommand,
            instructions=PLANNER_INSTRUCTIONS,
        )

    def handle_text(self, text: str) -> str:
        if self._agent:
            result = self._agent.run_sync(text, deps=self._settings)
            return str(result.output)
        return self._handle_locally(text)

    def plan_command(self, text: str, *, context: str = "") -> JiraCommand:
        if not self._planner:
            return self._plan_locally(text)
        prompt = text
        if context:
            prompt = f"Conversation context:\n{context}\n\nUser request:\n{text}"
        result = self._planner.run_sync(prompt)
        return result.output

    def _handle_locally(self, text: str) -> str:
        normalized = text.lower()
        if "jira" in normalized or "джир" in normalized:
            return fetch_jira_tasks(self._settings, limit=5)
        return "Пока доступна тестовая команда для Jira, например: достань мне задачи в jira"

    def _plan_locally(self, text: str) -> JiraCommand:
        normalized = text.lower()
        if "comment" in normalized or "коммент" in normalized or "комментар" in normalized:
            return JiraCommand(action="answer", message="Для комментариев нужен OPENAI_API_KEY, чтобы безопасно разобрать команду.")
        if "status" in normalized or "статус" in normalized or "переведи" in normalized:
            return JiraCommand(action="answer", message="Для изменения статуса нужен OPENAI_API_KEY, чтобы безопасно разобрать команду.")
        if "jira" in normalized or "джир" in normalized or "задач" in normalized:
            return JiraCommand(action="search", limit=10)
        return JiraCommand(action="answer", message=self._handle_locally(text))

    @staticmethod
    def _openai_model_name(model: str) -> str:
        return model.removeprefix("openai:")
