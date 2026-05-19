import asyncio
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from personal_assistant.agents.base import (
    AgentDisplay,
    AgentModel,
    AgentResponse,
    ConfirmCallback,
    TextStreamCallback,
)
from personal_assistant.clients.jira import JiraIssue, JiraTransition
from personal_assistant.settings import Settings
from personal_assistant.tools.jira import (
    add_jira_comment,
    combine_jql_with_updated_today,
    create_jira_issue,
    format_jira_issue,
    format_jira_issues,
    find_jira_transition,
    format_jira_transitions,
    get_jira_issue as fetch_jira_issue,
    get_jira_tasks as fetch_jira_tasks,
    get_jira_transitions as fetch_jira_transitions,
    resolve_jira_issue_key,
    search_jira_issues,
    transition_jira_issue,
    update_jira_issue_fields,
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
    "For free-form LLM answers, leave message empty so the assistant can stream the response. "
    "Set message only for deterministic clarification or error text. "
    "Choose analyze_productivity when the user asks to analyze personal productivity, today's performance, "
    "what they accomplished today, or how their work day is going; set limit=20 unless the user asks otherwise. "
    "Choose search for read-only requests like daily overview, blockers, stale tasks, bugs, priority lists. "
    "Choose get_issue when the user asks to inspect, explain, summarize, or open one issue. "
    "Choose create_issue when the user asks to create a new Jira issue, task, sub-task, or child task. "
    "Choose transition for status changes. Choose comment for adding Jira comments. "
    "Choose update_fields for priority, assignee, labels, due date, summary, or other field updates. "
    "When the user asks to add or append information to an issue description, use fields.description_add. "
    "Use fields.description only when the user explicitly asks to replace the whole description. "
    "For generic personal task searches, do not set jql; the app will use the configured default. "
    "For filtered searches, keep assignee = currentUser() unless the user explicitly asks otherwise. "
    "For create_issue, fill summary, description when available, issue_type, and parent_key when requested. "
    "If the user says a task is inside a story or parent task, set parent_key to that issue and issue_type to Sub-task. "
    "For writes, set needs_confirmation=true and fill issue_key plus the field needed for that action. "
    "If a write request does not identify an issue but context has Current issue, use that issue_key. "
    "Use Recent conversation to resolve short replies like 'давай', 'yes', or 'сделай так'. "
    "If there is no Current issue, choose answer and ask the user which issue to change."
)


class JiraCommand(BaseModel):
    action: Literal[
        "answer",
        "search",
        "analyze_productivity",
        "get_issue",
        "create_issue",
        "transition",
        "comment",
        "update_fields",
    ] = "answer"
    message: str = ""
    issue_key: str | None = None
    parent_key: str | None = None
    issue_type: str | None = None
    summary: str | None = None
    description: str | None = None
    jql: str | None = None
    limit: int = Field(default=5, ge=1, le=50)
    transition: str | None = None
    comment: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    needs_confirmation: bool = False

    @property
    def is_write(self) -> bool:
        return self.action in {"create_issue", "transition", "comment", "update_fields"}


def get_jira_tasks(
    ctx: RunContext[Settings], limit: int = 10, jql: str | None = None
) -> str:
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
    return format_jira_transitions(
        fetch_jira_transitions(ctx.deps, issue_key=issue_key)
    )


class JiraAgent:
    name = "jira"

    def __init__(self, settings: Settings, *, model: AgentModel | None = None) -> None:
        self._settings = settings
        self._agent: Agent[Settings, str] | None = None
        self._planner: Agent[None, JiraCommand] | None = None
        self._last_issue_keys: list[str] = []
        self._current_issue_key: str | None = None

        if model is None:
            return

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

    def handle_prompt(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
    ) -> AgentResponse:
        command = self.plan_command(text, context=context)
        return self.execute_command(
            command,
            fallback_prompt=text,
            confirm=confirm or (lambda preview: False),
        )

    async def handle_prompt_stream(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
        on_text_delta: TextStreamCallback | None = None,
    ) -> AgentResponse:
        command = await self._plan_command_async(text, context=context)
        if command.action == "answer" and not command.message and self._agent:
            return await self._stream_text_answer(text, on_text_delta=on_text_delta)
        return self.execute_command(
            command,
            fallback_prompt=text,
            confirm=confirm or (lambda preview: False),
        )

    async def _plan_command_async(self, text: str, *, context: str = "") -> JiraCommand:
        if not self._planner:
            return self._plan_locally(text)
        return await asyncio.to_thread(self.plan_command, text, context=context)

    def execute_command(
        self,
        command: JiraCommand,
        *,
        fallback_prompt: str,
        confirm: ConfirmCallback,
    ) -> AgentResponse:
        if command.action == "answer":
            return AgentResponse(command.message or self.handle_text(fallback_prompt))

        if command.action == "search":
            issues = search_jira_issues(
                self._settings,
                jql=command.jql or self._settings.default_jira_jql,
                limit=command.limit,
            )
            self._last_issue_keys = [issue.key for issue in issues]
            if issues:
                self._current_issue_key = issues[0].key
            return AgentResponse(
                format_jira_issues(issues),
                display=AgentDisplay(kind="jira_issues", payload=issues),
            )

        if command.action == "analyze_productivity":
            jql = command.jql or combine_jql_with_updated_today(
                self._settings.default_jira_jql
            )
            issues = search_jira_issues(self._settings, jql=jql, limit=command.limit)
            self._last_issue_keys = [issue.key for issue in issues]
            if issues:
                self._current_issue_key = issues[0].key
            return AgentResponse(self._format_productivity_analysis(issues))

        if command.action == "get_issue":
            issue_key = self._resolve_issue_key(command.issue_key)
            if not issue_key:
                return AgentResponse("Укажи ключ задачи, например PA-12.")
            issue = fetch_jira_issue(self._settings, issue_key=issue_key)
            self._current_issue_key = issue.key
            return AgentResponse(format_jira_issue(issue))

        if command.is_write:
            return AgentResponse(self._execute_write(command, confirm=confirm))

        return AgentResponse(
            command.message or "Не понял команду. Попробуй сформулировать иначе."
        )

    async def _stream_text_answer(
        self,
        text: str,
        *,
        on_text_delta: TextStreamCallback | None = None,
    ) -> AgentResponse:
        if not self._agent:
            return AgentResponse(self._handle_locally(text))

        chunks: list[str] = []
        async with self._agent.run_stream(text, deps=self._settings) as result:
            async for delta in result.stream_text(delta=True):
                chunks.append(delta)
                if on_text_delta:
                    on_text_delta(delta)
        return AgentResponse("".join(chunks))

    def context_summary(self) -> str:
        parts: list[str] = []
        if self._current_issue_key:
            parts.append(f"Current issue: {self._current_issue_key}")
        if self._last_issue_keys:
            parts.append(f"Last search results: {', '.join(self._last_issue_keys)}")
        return "\n".join(parts)

    def reset_context(self) -> None:
        self._last_issue_keys.clear()
        self._current_issue_key = None

    def _execute_write(self, command: JiraCommand, *, confirm: ConfirmCallback) -> str:
        if command.action == "create_issue":
            return self._create_issue(command, confirm=confirm)

        issue_key = self._resolve_issue_key(command.issue_key)
        if not issue_key:
            return "Для изменения Jira нужно указать задачу, например PA-12."
        self._current_issue_key = issue_key

        if command.action == "transition":
            return self._execute_transition(command, issue_key, confirm=confirm)

        preview = self._preview_write(command, issue_key)
        if not confirm(preview):
            return "Изменение отменено."

        if command.action == "comment":
            if not command.comment:
                return "Не указан текст комментария."
            return add_jira_comment(
                self._settings, issue_key=issue_key, text=command.comment
            )
        if command.action == "update_fields":
            if not command.fields:
                return "Не указаны поля для обновления."
            return update_jira_issue_fields(
                self._settings, issue_key=issue_key, fields=command.fields
            )

        return "Эта write-команда пока не поддержана."

    def _execute_transition(
        self, command: JiraCommand, issue_key: str, *, confirm: ConfirmCallback
    ) -> str:
        if not command.transition:
            return "Не указан целевой статус или transition."

        transitions = fetch_jira_transitions(self._settings, issue_key=issue_key)
        transition = find_jira_transition(transitions, command.transition)
        if not transition:
            return self._format_unknown_transition(
                issue_key, command.transition, transitions
            )

        preview = self._preview_transition(issue_key, transition)
        if not confirm(preview):
            return "Изменение отменено."

        return transition_jira_issue(
            self._settings, issue_key=issue_key, transition_name=transition.name
        )

    def _create_issue(self, command: JiraCommand, *, confirm: ConfirmCallback) -> str:
        if not command.summary:
            return "Для создания Jira-задачи нужно указать название."

        parent_key = (
            self._resolve_issue_key(command.parent_key) if command.parent_key else None
        )
        issue_type = self._create_issue_type(command.issue_type, parent_key=parent_key)
        preview = self._preview_create_issue(
            command, issue_type=issue_type, parent_key=parent_key
        )
        if not confirm(preview):
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
    def _format_unknown_transition(
        issue_key: str, requested: str, transitions: list[JiraTransition]
    ) -> str:
        lines = [
            f"Не могу изменить статус {issue_key} на {requested!r}: такого доступного transition/status сейчас нет.",
            "",
            format_jira_transitions(transitions),
            "",
            "Укажи один из доступных вариантов.",
        ]
        return "\n".join(lines)

    def _preview_create_issue(
        self, command: JiraCommand, *, issue_type: str, parent_key: str | None
    ) -> str:
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
        if (
            normalized in {"1", "first", "первая", "первую", "первой"}
            and self._last_issue_keys
        ):
            return self._last_issue_keys[0]
        if (
            normalized in {"2", "second", "вторая", "вторую", "второй"}
            and len(self._last_issue_keys) > 1
        ):
            return self._last_issue_keys[1]
        if normalized in {"last", "последняя", "последнюю"} and self._last_issue_keys:
            return self._last_issue_keys[-1]
        return resolve_jira_issue_key(self._settings, value)

    @staticmethod
    def _format_productivity_analysis(issues: list[JiraIssue]) -> str:
        if not issues:
            return (
                "За сегодня в Jira не нашлось обновленных задач по твоему фильтру. "
                "По этим данным нельзя подтвердить активность; стоит проверить worklog, календарь или коммиты, "
                "если работа не отражалась обновлениями задач."
            )

        status_counts = Counter(issue.status for issue in issues)
        completed = [
            issue for issue in issues if JiraAgent._is_completed_status(issue.status)
        ]
        active = [
            issue for issue in issues if JiraAgent._is_active_status(issue.status)
        ]
        blocked = [
            issue for issue in issues if JiraAgent._is_blocked_status(issue.status)
        ]
        high_priority = [
            issue
            for issue in issues
            if (issue.priority or "").strip().lower()
            in {"highest", "high", "высокий", "критический"}
        ]

        lines = [
            "Анализ производительности за сегодня по Jira:",
            f"- Затронуто задач: {len(issues)}.",
            f"- Статусы: {JiraAgent._format_counts(status_counts)}.",
        ]
        if completed:
            lines.append(f"- Завершено: {JiraAgent._format_issue_refs(completed)}.")
        if active:
            lines.append(
                f"- В активной работе или на проверке: {JiraAgent._format_issue_refs(active)}."
            )
        if high_priority:
            lines.append(
                f"- Фокус на высоком приоритете: {JiraAgent._format_issue_refs(high_priority)}."
            )
        if blocked:
            lines.append(
                f"- Есть возможные блокеры: {JiraAgent._format_issue_refs(blocked)}."
            )

        if completed and not blocked:
            lines.append(
                "Вывод: день выглядит продуктивным: есть завершенные задачи и нет явных блокеров в найденных задачах."
            )
        elif active and not completed:
            lines.append(
                "Вывод: день больше похож на продвижение текущей работы, чем на закрытие задач."
            )
        elif blocked:
            lines.append(
                "Вывод: продуктивность может проседать из-за блокеров; их лучше разобрать первыми."
            )
        else:
            lines.append(
                "Вывод: активность есть, но по одним статусам Jira сложно оценить реальный результат."
            )

        lines.append(
            "Ограничение: анализ основан на задачах, обновленных сегодня, а не на worklog или истории статусов."
        )
        return "\n".join(lines)

    @staticmethod
    def _format_counts(counts: Counter[str]) -> str:
        return ", ".join(
            f"{status} - {count}" for status, count in counts.most_common()
        )

    @staticmethod
    def _format_issue_refs(issues: list[JiraIssue], *, limit: int = 5) -> str:
        refs = [f"{issue.key} ({issue.status})" for issue in issues[:limit]]
        if len(issues) > limit:
            refs.append(f"еще {len(issues) - limit}")
        return ", ".join(refs)

    @staticmethod
    def _is_completed_status(status: str) -> bool:
        normalized = status.strip().lower()
        return any(
            token in normalized
            for token in ("done", "closed", "resolved", "готов", "закрыт", "выполн")
        )

    @staticmethod
    def _is_active_status(status: str) -> bool:
        normalized = status.strip().lower()
        return any(
            token in normalized
            for token in ("progress", "review", "testing", "работ", "ревью", "тест")
        )

    @staticmethod
    def _is_blocked_status(status: str) -> bool:
        normalized = status.strip().lower()
        return any(
            token in normalized for token in ("blocked", "blocker", "блок", "заблок")
        )

    def _handle_locally(self, text: str) -> str:
        normalized = text.lower()
        if "jira" in normalized or "джир" in normalized:
            return fetch_jira_tasks(self._settings, limit=5)
        return "Пока доступна тестовая команда для Jira, например: достань мне задачи в jira"

    def _plan_locally(self, text: str) -> JiraCommand:
        normalized = text.lower()
        if any(
            token in normalized
            for token in ("производитель", "продуктив", "performance", "productivity")
        ):
            return JiraCommand(action="analyze_productivity", limit=20)
        if (
            "comment" in normalized
            or "коммент" in normalized
            or "комментар" in normalized
        ):
            return JiraCommand(
                action="answer",
                message="Для комментариев нужен OPENAI_API_KEY, чтобы безопасно разобрать команду.",
            )
        if "status" in normalized or "статус" in normalized or "переведи" in normalized:
            return JiraCommand(
                action="answer",
                message="Для изменения статуса нужен OPENAI_API_KEY, чтобы безопасно разобрать команду.",
            )
        if "jira" in normalized or "джир" in normalized or "задач" in normalized:
            return JiraCommand(action="search", limit=10)
        return JiraCommand(action="answer", message=self._handle_locally(text))
