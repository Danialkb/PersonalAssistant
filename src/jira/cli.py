import argparse
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import ValidationError

from jira.agent import AssistantAgent, JiraCommand
from jira.settings import get_settings
from jira.tools import (
    add_jira_comment,
    format_jira_issue,
    format_jira_issues,
    get_jira_issue,
    resolve_jira_issue_key,
    search_jira_issues,
    transition_jira_issue,
    update_jira_issue_fields,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Text-first personal assistant CLI.")
    parser.add_argument("prompt", nargs="*", help="Natural-language command, for example: достань мне задачи в jira")
    return parser


class ChatSession:
    def __init__(
        self,
        agent: AssistantAgent,
        settings,
        *,
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        self._agent = agent
        self._settings = settings
        self._confirm = confirm or self._confirm_in_terminal
        self._last_issue_keys: list[str] = []
        self._current_issue_key: str | None = None

    def run(self, initial_prompt: str | None = None) -> None:
        print("Jira chat. /exit чтобы выйти, /clear чтобы очистить контекст.")
        if initial_prompt:
            self._print_handled(initial_prompt)
        while True:
            try:
                prompt = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not prompt:
                continue
            if prompt == "/exit":
                return
            if prompt == "/clear":
                self._last_issue_keys.clear()
                self._current_issue_key = None
                print("Контекст очищен.")
                continue
            self._print_handled(prompt)

    def handle(self, prompt: str) -> str:
        command = self._agent.plan_command(prompt, context=self._context_summary())
        return self._execute(command, fallback_prompt=prompt)

    def _print_handled(self, prompt: str) -> None:
        try:
            print(self.handle(prompt))
        except (httpx.HTTPStatusError, ValueError) as exc:
            print(f"Ошибка: {exc}")

    def _execute(self, command: JiraCommand, *, fallback_prompt: str) -> str:
        if command.action == "answer":
            return command.message or self._agent.handle_text(fallback_prompt)

        if command.action == "search":
            issues = search_jira_issues(
                self._settings,
                jql=command.jql or self._settings.default_jira_jql,
                limit=command.limit,
            )
            self._last_issue_keys = [issue.key for issue in issues]
            if issues:
                self._current_issue_key = issues[0].key
            return format_jira_issues(issues)

        if command.action == "get_issue":
            issue_key = self._resolve_issue_key(command.issue_key)
            if not issue_key:
                return "Укажи ключ задачи, например PA-12."
            issue = get_jira_issue(self._settings, issue_key=issue_key)
            self._current_issue_key = issue.key
            return format_jira_issue(issue)

        if command.is_write:
            return self._execute_write(command)

        return command.message or "Не понял команду. Попробуй сформулировать иначе."

    def _execute_write(self, command: JiraCommand) -> str:
        issue_key = self._resolve_issue_key(command.issue_key)
        if not issue_key:
            return "Для изменения Jira нужно указать задачу, например PA-12."

        preview = self._preview_write(command, issue_key)
        if not self._confirm(preview):
            return "Изменение отменено."

        if command.action == "transition":
            if not command.transition:
                return "Не указан целевой статус или transition."
            return transition_jira_issue(self._settings, issue_key=issue_key, transition_name=command.transition)
        if command.action == "comment":
            if not command.comment:
                return "Не указан текст комментария."
            return add_jira_comment(self._settings, issue_key=issue_key, text=command.comment)
        if command.action == "update_fields":
            if not command.fields:
                return "Не указаны поля для обновления."
            return update_jira_issue_fields(self._settings, issue_key=issue_key, fields=command.fields)

        return "Эта write-команда пока не поддержана."

    def _preview_write(self, command: JiraCommand, issue_key: str) -> str:
        if command.action == "transition":
            return f"Изменить статус {issue_key}: {command.transition}"
        if command.action == "comment":
            return f"Добавить комментарий в {issue_key}:\n{command.comment}"
        fields = ", ".join(f"{key}={value!r}" for key, value in command.fields.items())
        return f"Обновить поля {issue_key}: {fields}"

    def _resolve_issue_key(self, value: str | None) -> str | None:
        if not value:
            return self._current_issue_key

        normalized = value.strip().lower()
        if normalized in {"1", "first", "первая", "первую", "первой"} and self._last_issue_keys:
            return self._last_issue_keys[0]
        if normalized in {"2", "second", "вторая", "вторую", "второй"} and len(self._last_issue_keys) > 1:
            return self._last_issue_keys[1]
        if normalized in {"last", "последняя", "последнюю"} and self._last_issue_keys:
            return self._last_issue_keys[-1]
        return resolve_jira_issue_key(self._settings, value)

    def _context_summary(self) -> str:
        parts: list[str] = []
        if self._current_issue_key:
            parts.append(f"Current issue: {self._current_issue_key}")
        if self._last_issue_keys:
            parts.append(f"Last search results: {', '.join(self._last_issue_keys)}")
        return "\n".join(parts)

    @staticmethod
    def _confirm_in_terminal(preview: str) -> bool:
        print(preview)
        answer = input("Apply? [y/N] ").strip().lower()
        return answer in {"y", "yes", "д", "да"}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    prompt = " ".join(args.prompt).strip()
    command_name = Path(sys.argv[0]).stem

    if not prompt:
        if command_name == "assistant":
            prompt = "chat"
        else:
            parser.error("Передайте текстовую команду, например: jira достань мне задачи в jira")

    try:
        settings = get_settings()
    except ValidationError as exc:
        missing = ", ".join(error["loc"][0] for error in exc.errors() if error["type"] == "missing")
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
        print(ChatSession(agent, settings).handle(prompt))
    except (httpx.HTTPStatusError, ValueError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")
