import re

from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from personal_assistant.agents.base import (
    AgentResponse,
    ConfirmCallback,
    DomainAgent,
    TextStreamCallback,
)
from personal_assistant.agents.gitlab import GitLabMRReviewAgent
from personal_assistant.agents.jira import JiraAgent
from personal_assistant.settings import Settings


class AssistantAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        agents: list[DomainAgent] | None = None,
        default_agent: str = "jira",
    ) -> None:
        self._agents = {
            agent.name: agent
            for agent in agents or self._build_default_agents(settings)
        }
        self._default_agent_name = default_agent

    def handle_text(self, text: str) -> str:
        return self._agent_for_text(text).handle_text(text)

    def plan_command(self, text: str, *, context: str = "") -> BaseModel:
        return self._agent_for_text(text).plan_command(text, context=context)

    def handle_prompt(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
    ) -> AgentResponse:
        if self._is_unconfigured_gitlab_request(text):
            return AgentResponse(
                "GitLab не настроен. Укажи GITLAB_BASE_URL и GITLAB_TOKEN, "
                "затем повтори команду с MR ссылкой."
            )
        agent = self._agent_for_text(text)
        return agent.handle_prompt(text, context=context, confirm=confirm)

    async def handle_prompt_stream(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
        on_text_delta: TextStreamCallback | None = None,
    ) -> AgentResponse:
        if self._is_unconfigured_gitlab_request(text):
            return AgentResponse(
                "GitLab не настроен. Укажи GITLAB_BASE_URL и GITLAB_TOKEN, "
                "затем повтори команду с MR ссылкой."
            )
        agent = self._agent_for_text(text)
        return await agent.handle_prompt_stream(
            text,
            context=context,
            confirm=confirm,
            on_text_delta=on_text_delta,
        )

    def context_summary(self) -> str:
        return self._default_agent.context_summary()

    def reset_context(self) -> None:
        self._default_agent.reset_context()

    def _agent_for_text(self, text: str) -> DomainAgent:
        if "gitlab" in self._agents and self._looks_like_gitlab_mr_request(text):
            return self._agents["gitlab"]
        return self._default_agent

    def _is_unconfigured_gitlab_request(self, text: str) -> bool:
        return (
            "gitlab" not in self._agents
            and self._looks_like_gitlab_mr_request(text)
        )

    @property
    def _default_agent(self) -> DomainAgent:
        try:
            return self._agents[self._default_agent_name]
        except KeyError as exc:
            available = ", ".join(sorted(self._agents)) or "none"
            raise ValueError(
                f"Unknown default agent '{self._default_agent_name}'. Available agents: {available}"
            ) from exc

    @classmethod
    def _build_default_agents(cls, settings: Settings) -> list[DomainAgent]:
        model = cls._build_model(settings)
        agents: list[DomainAgent] = [JiraAgent(settings, model=model)]
        if settings.GITLAB_BASE_URL and settings.GITLAB_TOKEN:
            agents.append(GitLabMRReviewAgent.from_settings(settings, model=model))
        return agents

    @staticmethod
    def _build_model(settings: Settings) -> OpenAIChatModel | None:
        if not settings.OPENAI_API_KEY:
            return None
        return OpenAIChatModel(
            settings.OPENAI_MODEL.removeprefix("openai:"),
            provider=OpenAIProvider(api_key=settings.OPENAI_API_KEY),
        )

    @staticmethod
    def _looks_like_gitlab_mr_request(text: str) -> bool:
        lowered = text.lower()
        if re.search(r"/-/merge_requests/\d+", text):
            return True
        return "gitlab" in lowered and (
            re.search(r"\bmr\b", lowered) is not None or "merge request" in lowered
        )
