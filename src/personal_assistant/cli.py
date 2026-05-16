import argparse
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import ValidationError

from personal_assistant.agents.jira import JiraCommand
from personal_assistant.assistant import AssistantAgent
from personal_assistant.clients.jira import JiraIssue, JiraTransition
from personal_assistant.settings import get_settings
from personal_assistant.tools.jira import (
    add_jira_comment,
    combine_jql_with_updated_today,
    create_jira_issue,
    format_jira_issue,
    format_jira_issues,
    format_jira_transitions,
    get_jira_issue,
    get_jira_transitions,
    resolve_jira_issue_key,
    search_jira_issues,
    transition_jira_issue,
    update_jira_issue_fields,
)
from personal_assistant.ui import TerminalUI


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
        ui: TerminalUI | None = None,
    ) -> None:
        self._agent = agent
        self._settings = settings
        self._ui = ui or TerminalUI()
        self._confirm = confirm or self._ui.confirm
        self._last_issue_keys: list[str] = []
        self._current_issue_key: str | None = None
        self._recent_turns: list[tuple[str, str]] = []
        self._last_output_issues: list[JiraIssue] | None = None

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
                self._last_issue_keys.clear()
                self._current_issue_key = None
                self._recent_turns.clear()
                self._ui.print_success("Контекст очищен.")
                continue
            self.print_handled(prompt)

    def handle(self, prompt: str) -> str:
        self._last_output_issues = None
        command = self._agent.plan_command(prompt, context=self._context_summary())
        output = self._execute(command, fallback_prompt=prompt)
        self._remember_turn(prompt, output)
        return output

    def print_handled(self, prompt: str) -> None:
        try:
            output = self.handle(prompt)
            if self._last_output_issues is not None:
                self._ui.print_issues_table(self._last_output_issues)
            else:
                self._ui.print_assistant(output)
        except (httpx.HTTPStatusError, ValueError) as exc:
            self._ui.print_error(str(exc))

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
            self._last_output_issues = issues
            if issues:
                self._current_issue_key = issues[0].key
            return format_jira_issues(issues)

        if command.action == "analyze_productivity":
            jql = command.jql or combine_jql_with_updated_today(self._settings.default_jira_jql)
            issues = search_jira_issues(self._settings, jql=jql, limit=command.limit)
            self._last_issue_keys = [issue.key for issue in issues]
            if issues:
                self._current_issue_key = issues[0].key
            return self._format_productivity_analysis(issues)

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
        if command.action == "create_issue":
            return self._create_issue(command)

        issue_key = self._resolve_issue_key(command.issue_key)
        if not issue_key:
            return "Для изменения Jira нужно указать задачу, например PA-12."
        self._current_issue_key = issue_key

        if command.action == "transition":
            return self._execute_transition(command, issue_key)

        preview = self._preview_write(command, issue_key)
        if not self._confirm(preview):
            return "Изменение отменено."

        if command.action == "comment":
            if not command.comment:
                return "Не указан текст комментария."
            return add_jira_comment(self._settings, issue_key=issue_key, text=command.comment)
        if command.action == "update_fields":
            if not command.fields:
                return "Не указаны поля для обновления."
            return update_jira_issue_fields(self._settings, issue_key=issue_key, fields=command.fields)

        return "Эта write-команда пока не поддержана."

    def _execute_transition(self, command: JiraCommand, issue_key: str) -> str:
        if not command.transition:
            return "Не указан целевой статус или transition."

        transitions = get_jira_transitions(self._settings, issue_key=issue_key)
        transition = self._find_transition(transitions, command.transition)
        if not transition:
            return self._format_unknown_transition(issue_key, command.transition, transitions)

        preview = self._preview_transition(issue_key, transition)
        if not self._confirm(preview):
            return "Изменение отменено."

        return transition_jira_issue(self._settings, issue_key=issue_key, transition_name=transition.name)

    def _create_issue(self, command: JiraCommand) -> str:
        if not command.summary:
            return "Для создания Jira-задачи нужно указать название."

        parent_key = self._resolve_issue_key(command.parent_key) if command.parent_key else None
        issue_type = self._create_issue_type(command.issue_type, parent_key=parent_key)
        preview = self._preview_create_issue(command, issue_type=issue_type, parent_key=parent_key)
        if not self._confirm(preview):
            return "Изменение отменено."

        issue = create_jira_issue(
            self._settings,
            summary=command.summary,
            issue_type=issue_type,
            description=command.description,
            parent_key=parent_key,
        )
        self._current_issue_key = issue.key
        self._last_issue_keys = [issue.key]
        return f"{issue.key}: задача создана.\n{issue.url}"

    def _preview_write(self, command: JiraCommand, issue_key: str) -> str:
        if command.action == "transition":
            return f"Изменить статус {issue_key}: {command.transition}"
        if command.action == "comment":
            return f"Добавить комментарий в {issue_key}:\n{command.comment}"
        fields = ", ".join(f"{key}={value!r}" for key, value in command.fields.items())
        return f"Обновить поля {issue_key}: {fields}"

    @staticmethod
    def _preview_transition(issue_key: str, transition: JiraTransition) -> str:
        if transition.target_status and transition.target_status != transition.name:
            return f"Изменить статус {issue_key}: {transition.name} -> {transition.target_status}"
        return f"Изменить статус {issue_key}: {transition.name}"

    @staticmethod
    def _find_transition(transitions: list[JiraTransition], requested: str) -> JiraTransition | None:
        normalized = requested.strip().lower()
        for transition in transitions:
            if transition.name.lower() == normalized or (transition.target_status or "").lower() == normalized:
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

    @staticmethod
    def _format_unknown_transition(issue_key: str, requested: str, transitions: list[JiraTransition]) -> str:
        lines = [
            f"Не могу изменить статус {issue_key} на {requested!r}: такого доступного transition/status сейчас нет.",
            "",
            format_jira_transitions(transitions),
            "",
            "Укажи один из доступных вариантов.",
        ]
        return "\n".join(lines)

    def _preview_create_issue(self, command: JiraCommand, *, issue_type: str, parent_key: str | None) -> str:
        lines = [
            f"Создать Jira-задачу: {command.summary}",
            f"Тип: {issue_type}",
        ]
        if parent_key:
            lines.append(f"Родитель: {parent_key}")
        if command.description:
            lines.append(f"Описание:\n{command.description}")
        return "\n".join(lines)

    @staticmethod
    def _create_issue_type(issue_type: str | None, *, parent_key: str | None) -> str:
        normalized = (issue_type or "").strip().lower()
        if parent_key and normalized in {"", "task", "задача"}:
            return "Sub-task"
        return issue_type or "Task"

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

    @staticmethod
    def _format_productivity_analysis(issues: list[JiraIssue]) -> str:
        if not issues:
            return (
                "За сегодня в Jira не нашлось обновленных задач по твоему фильтру. "
                "По этим данным нельзя подтвердить активность; стоит проверить worklog, календарь или коммиты, "
                "если работа не отражалась обновлениями задач."
            )

        status_counts = Counter(issue.status for issue in issues)
        completed = [issue for issue in issues if ChatSession._is_completed_status(issue.status)]
        active = [issue for issue in issues if ChatSession._is_active_status(issue.status)]
        blocked = [issue for issue in issues if ChatSession._is_blocked_status(issue.status)]
        high_priority = [
            issue for issue in issues if (issue.priority or "").strip().lower() in {"highest", "high", "высокий", "критический"}
        ]

        lines = [
            "Анализ производительности за сегодня по Jira:",
            f"- Затронуто задач: {len(issues)}.",
            f"- Статусы: {ChatSession._format_counts(status_counts)}.",
        ]
        if completed:
            lines.append(f"- Завершено: {ChatSession._format_issue_refs(completed)}.")
        if active:
            lines.append(f"- В активной работе или на проверке: {ChatSession._format_issue_refs(active)}.")
        if high_priority:
            lines.append(f"- Фокус на высоком приоритете: {ChatSession._format_issue_refs(high_priority)}.")
        if blocked:
            lines.append(f"- Есть возможные блокеры: {ChatSession._format_issue_refs(blocked)}.")

        if completed and not blocked:
            lines.append("Вывод: день выглядит продуктивным: есть завершенные задачи и нет явных блокеров в найденных задачах.")
        elif active and not completed:
            lines.append("Вывод: день больше похож на продвижение текущей работы, чем на закрытие задач.")
        elif blocked:
            lines.append("Вывод: продуктивность может проседать из-за блокеров; их лучше разобрать первыми.")
        else:
            lines.append("Вывод: активность есть, но по одним статусам Jira сложно оценить реальный результат.")

        lines.append("Ограничение: анализ основан на задачах, обновленных сегодня, а не на worklog или истории статусов.")
        return "\n".join(lines)

    @staticmethod
    def _format_counts(counts: Counter[str]) -> str:
        return ", ".join(f"{status} - {count}" for status, count in counts.most_common())

    @staticmethod
    def _format_issue_refs(issues: list[JiraIssue], *, limit: int = 5) -> str:
        refs = [f"{issue.key} ({issue.status})" for issue in issues[:limit]]
        if len(issues) > limit:
            refs.append(f"еще {len(issues) - limit}")
        return ", ".join(refs)

    @staticmethod
    def _is_completed_status(status: str) -> bool:
        normalized = status.strip().lower()
        return any(token in normalized for token in ("done", "closed", "resolved", "готов", "закрыт", "выполн"))

    @staticmethod
    def _is_active_status(status: str) -> bool:
        normalized = status.strip().lower()
        return any(token in normalized for token in ("progress", "review", "testing", "работ", "ревью", "тест"))

    @staticmethod
    def _is_blocked_status(status: str) -> bool:
        normalized = status.strip().lower()
        return any(token in normalized for token in ("blocked", "blocker", "блок", "заблок"))

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
        ChatSession(agent, settings).print_handled(prompt)
    except (httpx.HTTPStatusError, ValueError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")
