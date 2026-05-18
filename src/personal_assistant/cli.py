import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import ValidationError

from personal_assistant.agents.base import AgentDisplay, AgentResponse
from personal_assistant.assistant import AssistantAgent
from personal_assistant.settings import get_settings
from personal_assistant.ui import TerminalUI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Text-first personal assistant CLI.")
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Natural-language command, for example: достань мне задачи в jira",
    )
    return parser


class ChatSession:
    def __init__(
        self,
        agent: AssistantAgent,
        settings,
        *,
        confirm: Callable[[str], bool] | None = None,
        ui: TerminalUI | None = None,
    ) -> None:
        self._agent = agent
        self._ui = ui or TerminalUI()
        self._confirm = confirm or self._ui.confirm
        self._recent_turns: list[tuple[str, str]] = []
        self._last_display: AgentDisplay | None = None

    def run(self, initial_prompt: str | None = None) -> None:
        self._ui.print_banner()
        if initial_prompt:
            self.print_handled(initial_prompt)
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                self._ui.console.print()
                return
            if not prompt:
                continue
            if prompt == "/exit":
                return
            if prompt == "/clear":
                self._agent.reset_context()
                self._recent_turns.clear()
                self._ui.print_success("Контекст очищен.")
                continue
            self.print_handled(prompt)

    def handle(self, prompt: str) -> str:
        self._last_display = None
        response = self._agent.handle_prompt(
            prompt,
            context=self._context_summary(),
            confirm=self._confirm,
        )
        return self._handle_response(prompt, response).text

    def print_handled(self, prompt: str) -> None:
        asyncio.run(self._print_handled_async(prompt))

    async def _print_handled_async(self, prompt: str) -> None:
        streamed = False

        def print_delta(delta: str) -> None:
            nonlocal streamed
            streamed = True
            self._ui.print_assistant_delta(delta)

        try:
            response = await self._handle_stream(prompt, on_text_delta=print_delta)
            if streamed:
                self._ui.finish_assistant_stream()
            if self._last_display is not None:
                self._ui.print_display(self._last_display)
            elif not streamed:
                self._ui.print_assistant(response.text)
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
            if streamed:
                self._ui.finish_assistant_stream()
            self._ui.print_error(str(exc))

    async def _handle_stream(
        self,
        prompt: str,
        *,
        on_text_delta: Callable[[str], None],
    ) -> AgentResponse:
        self._last_display = None
        if not hasattr(self._agent, "handle_prompt_stream"):
            response = self._agent.handle_prompt(
                prompt,
                context=self._context_summary(),
                confirm=self._confirm,
            )
            return self._handle_response(prompt, response)

        response = await self._agent.handle_prompt_stream(
            prompt,
            context=self._context_summary(),
            confirm=self._confirm,
            on_text_delta=on_text_delta,
        )
        return self._handle_response(prompt, response)

    def _handle_response(self, prompt: str, response: AgentResponse) -> AgentResponse:
        output = response.text
        self._last_display = response.display
        self._remember_turn(prompt, output)
        return response

    def _context_summary(self) -> str:
        parts: list[str] = []
        agent_context = self._agent.context_summary()
        if agent_context:
            parts.append(agent_context)
        if self._recent_turns:
            lines = ["Recent conversation:"]
            for user_text, assistant_text in self._recent_turns:
                lines.append(f"User: {self._compact_context_text(user_text)}")
                lines.append(f"Assistant: {self._compact_context_text(assistant_text)}")
            parts.append("\n".join(lines))
        return "\n".join(parts)

    def _remember_turn(self, prompt: str, output: str) -> None:
        self._recent_turns.append((prompt, output))
        self._recent_turns = self._recent_turns[-10:]

    @staticmethod
    def _compact_context_text(text: str, *, limit: int = 500) -> str:
        compacted = " ".join(text.split())
        if len(compacted) <= limit:
            return compacted
        return compacted[: limit - 3].rstrip() + "..."


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    prompt = " ".join(args.prompt).strip()
    command_name = Path(sys.argv[0]).stem

    if not prompt:
        if command_name == "assistant":
            prompt = "chat"
        else:
            parser.error(
                "Передайте текстовую команду, например: jira достань мне задачи в jira"
            )

    try:
        settings = get_settings()
    except ValidationError as exc:
        missing = ", ".join(
            error["loc"][0] for error in exc.errors() if error["type"] == "missing"
        )
        if missing:
            parser.exit(2, f"Не хватает переменных окружения: {missing}\n")
        raise

    agent = AssistantAgent(settings)
    if prompt == "chat":
        ChatSession(agent, settings).run()
        return

    if command_name == "assistant":
        ChatSession(agent, settings).run(initial_prompt=prompt)
        return

    try:
        ChatSession(agent, settings).print_handled(prompt)
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")
