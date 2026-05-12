from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from jira.settings import Settings
from jira.tools import get_jira_tasks as fetch_jira_tasks


INSTRUCTIONS = (
    "You are a text-first CLI assistant. Answer in the user's language. "
    "Use tools for Jira requests instead of inventing task data. "
    "When fetching Jira tasks, default to 5 issues unless the user asks for another amount. "
    "Do not pass jql for a generic Jira task request; let the tool use the configured assignee filter. "
    "If the user asks for extra filtering, keep the assignee filter unless they explicitly ask otherwise."
)


def get_jira_tasks(ctx: RunContext[Settings], limit: int = 10, jql: str | None = None) -> str:
    """Fetch Jira issues for the user.

    Use this when the user asks to show, fetch, list, analyze, or prioritize Jira tasks.
    Omit jql to use the configured default Jira query.
    """
    return fetch_jira_tasks(ctx.deps, limit=limit, jql=jql)


class AssistantAgent:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._agent: Agent[Settings, str] | None = None
        if settings.openai_api_key:
            model = OpenAIChatModel(
                self._openai_model_name(settings.openai_model),
                provider=OpenAIProvider(api_key=settings.openai_api_key),
            )
            self._agent = Agent(
                model,
                deps_type=Settings,
                tools=[get_jira_tasks],
                instructions=INSTRUCTIONS,
            )

    def handle_text(self, text: str) -> str:
        if self._agent:
            result = self._agent.run_sync(text, deps=self._settings)
            return str(result.output)
        return self._handle_locally(text)

    def _handle_locally(self, text: str) -> str:
        normalized = text.lower()
        if "jira" in normalized or "джир" in normalized:
            return fetch_jira_tasks(self._settings, limit=5)
        return "Пока доступна тестовая команда для Jira, например: достань мне задачи в jira"

    @staticmethod
    def _openai_model_name(model: str) -> str:
        return model.removeprefix("openai:")
