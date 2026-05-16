from typing import Protocol

from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from personal_assistant.agents.jira import JiraAgent
from personal_assistant.settings import Settings


class DomainAgent(Protocol):
    name: str

    def handle_text(self, text: str) -> str: ...

    def plan_command(self, text: str, *, context: str = "") -> BaseModel: ...


class AssistantAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        agents: list[DomainAgent] | None = None,
        default_agent: str = "jira",
    ) -> None:
        self._agents = {agent.name: agent for agent in agents or self._build_default_agents(settings)}
        self._default_agent_name = default_agent

    def handle_text(self, text: str) -> str:
        return self._default_agent.handle_text(text)

    def plan_command(self, text: str, *, context: str = "") -> BaseModel:
        return self._default_agent.plan_command(text, context=context)

    @property
    def _default_agent(self) -> DomainAgent:
        try:
            return self._agents[self._default_agent_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._agents)) or "none"
            raise ValueError(f"Unknown default agent '{self._default_agent_name}'. Available agents: {available}") from exc

    @classmethod
    def _build_default_agents(cls, settings: Settings) -> list[DomainAgent]:
        model = cls._build_model(settings)
        return [JiraAgent(settings, model=model)]

    @staticmethod
    def _build_model(settings: Settings) -> OpenAIChatModel | None:
        if not settings.OPENAI_API_KEY:
            return None
        return OpenAIChatModel(
            settings.OPENAI_MODEL.removeprefix("openai:"),
            provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY),
        )
