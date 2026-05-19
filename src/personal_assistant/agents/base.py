from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic_ai.models import KnownModelName, Model


ConfirmCallback = Callable[[str], bool]
TextStreamCallback = Callable[[str], None]
AgentModel = Model | KnownModelName | str


@dataclass(frozen=True)
class AgentDisplay:
    kind: str
    payload: Any


@dataclass(frozen=True)
class AgentResponse:
    text: str
    display: AgentDisplay | None = None


class DomainAgent(Protocol):
    name: str

    def handle_text(self, text: str) -> str: ...

    def plan_command(self, text: str, *, context: str = "") -> BaseModel: ...

    def handle_prompt(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
    ) -> AgentResponse: ...

    async def handle_prompt_stream(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
        on_text_delta: TextStreamCallback | None = None,
    ) -> AgentResponse: ...

    def context_summary(self) -> str: ...

    def reset_context(self) -> None: ...
