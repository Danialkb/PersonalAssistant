import asyncio
from typing import Any

import httpx
from rich.console import Console

from personal_assistant.agents.base import AgentDisplay, AgentResponse, ConfirmCallback
from personal_assistant.agents.gitlab import (
    MRReviewResult,
    ReviewRecommendation,
    ReviewSeverity,
)
from personal_assistant.agents.jira import JiraAgent, JiraCommand
from personal_assistant.assistant import AssistantAgent
from personal_assistant.clients.jira import JiraIssue, JiraTransition
from personal_assistant.cli import ChatSession, main
from personal_assistant.settings import Settings
from personal_assistant.ui import TerminalUI


class StubAgent(JiraAgent):
    def __init__(self, command: JiraCommand, settings: Any) -> None:
        super().__init__(settings)
        self.command = command
        self.contexts: list[str] = []

    def plan_command(self, prompt: str, *, context: str = "") -> JiraCommand:
        self.contexts.append(context)
        return self.command

    def handle_text(self, text: str) -> str:
        return "fallback"


class StubAssistant:
    def __init__(self, settings: Any, command: JiraCommand | None = None) -> None:
        self._agent = StubAgent(command or JiraCommand(action="answer"), settings)

    def plan_command(self, prompt: str, *, context: str = "") -> JiraCommand:
        return self._agent.plan_command(prompt, context=context)

    def handle_text(self, text: str) -> str:
        return "fallback"

    def handle_prompt(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
    ) -> AgentResponse:
        return self._agent.handle_prompt(text, context=context, confirm=confirm)

    def context_summary(self) -> str:
        return self._agent.context_summary()

    def reset_context(self) -> None:
        self._agent.reset_context()


def stub_transitions(*args: Any, **kwargs: Any) -> list[JiraTransition]:
    return [
        JiraTransition(id="11", name="In Progress", target_status="In Progress"),
        JiraTransition(id="21", name="Code Review", target_status="Code Review"),
        JiraTransition(
            id="31", name="Ready for Testing", target_status="Ready for Testing"
        ),
    ]


def make_session(
    command: JiraCommand,
    settings: Settings,
    *,
    confirm: ConfirmCallback | None = None,
) -> ChatSession:
    return ChatSession(StubAgent(command, settings), settings, confirm=confirm)


def test_chat_session_does_not_apply_write_without_confirmation(monkeypatch) -> None:
    called: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        called["transition"] = kwargs
        return "changed"

    monkeypatch.setattr(
        "personal_assistant.agents.jira.transition_jira_issue", fake_transition
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.fetch_jira_transitions", stub_transitions
    )

    session = make_session(
        JiraCommand(action="transition", issue_key="PA-12", transition="In Progress"),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
        confirm=lambda preview: False,
    )

    output = session.handle("переведи PA-12 в работу")

    assert output == "Изменение отменено."
    assert called == {}


def test_chat_session_applies_write_after_confirmation(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "PA-12: выполнен transition In Progress."

    monkeypatch.setattr(
        "personal_assistant.agents.jira.transition_jira_issue", fake_transition
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.fetch_jira_transitions", stub_transitions
    )

    session = make_session(
        JiraCommand(action="transition", issue_key="PA-12", transition="In Progress"),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
        confirm=lambda preview: True,
    )

    output = session.handle("переведи PA-12 в работу")

    assert output == "PA-12: выполнен transition In Progress."
    assert captured["issue_key"] == "PA-12"
    assert captured["transition_name"] == "In Progress"


def test_chat_session_resolves_bare_issue_number_with_project_key(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "CCO-2284: выполнен transition Code Review."

    monkeypatch.setattr(
        "personal_assistant.agents.jira.transition_jira_issue", fake_transition
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.fetch_jira_transitions", stub_transitions
    )

    session = make_session(
        JiraCommand(action="transition", issue_key="2284", transition="Code Review"),
        Settings(
            JIRA_BASE_URL="https://example.atlassian.net",
            JIRA_API_KEY="token",
            JIRA_PROJECT_KEY="CCO",
        ),
        confirm=lambda preview: True,
    )

    output = session.handle("я сделал 2284, перенеси его в code review")

    assert output == "CCO-2284: выполнен transition Code Review."
    assert captured["issue_key"] == "CCO-2284"
    assert captured["transition_name"] == "Code Review"


def test_chat_session_remembers_issue_after_failed_write(monkeypatch) -> None:
    def fake_transition(*args: Any, **kwargs: Any) -> str:
        raise ValueError("Transition is not available")

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
        JIRA_PROJECT_KEY="CCO",
    )
    session = make_session(
        JiraCommand(
            action="transition", issue_key="2321", transition="Ready for Testing"
        ),
        settings,
        confirm=lambda preview: True,
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.transition_jira_issue", fake_transition
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.fetch_jira_transitions", stub_transitions
    )

    try:
        session.handle("закинь 2321 в ready for testing")
    except ValueError:
        pass

    assert session._context_summary() == "Current issue: CCO-2321"


def test_one_shot_prompt_uses_chat_executor(monkeypatch, capsys) -> None:
    class StubSettings:
        JIRA_PROJECT_KEY = "CCO"
        default_jira_jql = "assignee = currentUser() ORDER BY updated DESC"

    class OneShotAssistant(StubAssistant):
        def __init__(self, settings: StubSettings) -> None:
            super().__init__(
                settings,
                JiraCommand(
                    action="transition", issue_key="2284", transition="Code Review"
                ),
            )

    captured: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "CCO-2284: выполнен transition Code Review."

    monkeypatch.setattr(
        "sys.argv",
        ["jira", "перенеси", "задачу", "2284", "в", "jira", "установи", "Code review"],
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr("personal_assistant.cli.get_settings", lambda: StubSettings())
    monkeypatch.setattr("personal_assistant.cli.AssistantAgent", OneShotAssistant)
    monkeypatch.setattr(
        "personal_assistant.agents.jira.transition_jira_issue", fake_transition
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.fetch_jira_transitions", stub_transitions
    )

    main()

    output = capsys.readouterr().out
    assert "Изменить статус CCO-2284: Code Review" in output
    assert "CCO-2284: выполнен transition Code Review." in output
    assert captured["issue_key"] == "CCO-2284"


def test_chat_session_rejects_unknown_transition_before_confirmation(
    monkeypatch,
) -> None:
    called: dict[str, Any] = {}

    def fake_confirm(preview: str) -> bool:
        called["confirm"] = preview
        return True

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        called["transition"] = kwargs
        return "changed"

    monkeypatch.setattr(
        "personal_assistant.agents.jira.transition_jira_issue", fake_transition
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.fetch_jira_transitions",
        lambda *args, **kwargs: [
            JiraTransition(id="21", name="Code Review", target_status="Code Review"),
            JiraTransition(
                id="31", name="Ready for Testing", target_status="Ready for Testing"
            ),
        ],
    )

    session = make_session(
        JiraCommand(action="transition", issue_key="2329", transition="QA Review"),
        Settings(
            JIRA_BASE_URL="https://example.atlassian.net",
            JIRA_API_KEY="token",
            JIRA_PROJECT_KEY="CCO",
        ),
        confirm=fake_confirm,
    )

    output = session.handle("я передал на ревью 2329 обнови статус")

    assert "такого доступного transition/status сейчас нет" in output
    assert "Code Review" in output
    assert "Ready for Testing" in output
    assert "confirm" not in called
    assert "transition" not in called


def test_chat_session_normalizes_partial_transition_to_existing_option(
    monkeypatch,
) -> None:
    previews: list[str] = []
    captured: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "CCO-2329: выполнен transition Code Review."

    monkeypatch.setattr(
        "personal_assistant.agents.jira.transition_jira_issue", fake_transition
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.fetch_jira_transitions",
        lambda *args, **kwargs: [
            JiraTransition(id="21", name="Code Review", target_status="Code Review"),
            JiraTransition(
                id="31", name="Ready for Testing", target_status="Ready for Testing"
            ),
        ],
    )

    session = make_session(
        JiraCommand(action="transition", issue_key="2329", transition="Review"),
        Settings(
            JIRA_BASE_URL="https://example.atlassian.net",
            JIRA_API_KEY="token",
            JIRA_PROJECT_KEY="CCO",
        ),
        confirm=lambda preview: previews.append(preview) or True,
    )

    output = session.handle("я передал на ревью 2329 обнови статус")

    assert output == "CCO-2329: выполнен transition Code Review."
    assert previews == ["Изменить статус CCO-2329: Code Review"]
    assert captured["transition_name"] == "Code Review"


def test_assistant_prompt_starts_interactive_session(monkeypatch, capsys) -> None:
    class StubSettings:
        JIRA_PROJECT_KEY = "CCO"
        default_jira_jql = "assignee = currentUser() ORDER BY updated DESC"

    class AnswerAssistant(StubAssistant):
        def __init__(self, settings: StubSettings) -> None:
            super().__init__(
                settings, JiraCommand(action="answer", message="ответ: привет")
            )

    inputs = iter(["/exit"])
    monkeypatch.setattr("sys.argv", ["assistant", "привет"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("personal_assistant.cli.get_settings", lambda: StubSettings())
    monkeypatch.setattr("personal_assistant.cli.AssistantAgent", AnswerAssistant)

    main()

    output = capsys.readouterr().out
    assert "Assistant chat. /exit чтобы выйти, /clear чтобы очистить контекст." in output
    assert "ответ: привет" in output


def test_chat_session_search_stores_context_and_resolves_first_issue(
    monkeypatch,
) -> None:
    def fake_search(*args: Any, **kwargs: Any) -> list[JiraIssue]:
        return [
            JiraIssue(
                key="PA-1",
                summary="First",
                status="To Do",
                priority=None,
                assignee=None,
                url="https://example.atlassian.net/browse/PA-1",
            )
        ]

    captured: dict[str, Any] = {}

    def fake_comment(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "commented"

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"
    )
    agent = StubAgent(JiraCommand(action="search", limit=5), settings)
    session = ChatSession(
        agent,
        settings,
        confirm=lambda preview: True,
    )
    monkeypatch.setattr("personal_assistant.agents.jira.search_jira_issues", fake_search)

    output = session.handle("покажи задачи")

    assert "PA-1: First" in output

    agent.command = JiraCommand(
        action="comment", issue_key="первая", comment="беру в работу"
    )
    monkeypatch.setattr("personal_assistant.agents.jira.add_jira_comment", fake_comment)

    output = session.handle("напиши в первую")

    assert output == "commented"
    assert captured["issue_key"] == "PA-1"
    assert captured["text"] == "беру в работу"


def test_chat_session_analyzes_productivity_without_table_output(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_search(*args: Any, **kwargs: Any) -> list[JiraIssue]:
        captured.update(kwargs)
        return [
            JiraIssue(
                key="PA-1",
                summary="Ship report",
                status="Done",
                priority="High",
                assignee="Oleg",
                url="https://example.atlassian.net/browse/PA-1",
            ),
            JiraIssue(
                key="PA-2",
                summary="Review API changes",
                status="In Progress",
                priority=None,
                assignee="Oleg",
                url="https://example.atlassian.net/browse/PA-2",
            ),
        ]

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"
    )
    session = make_session(
        JiraCommand(action="analyze_productivity", limit=20),
        settings,
    )
    monkeypatch.setattr(
        "personal_assistant.agents.jira.search_jira_issues", fake_search
    )

    output = session.handle("проанализируй мою производительность за сегодня")

    assert captured == {
        "jql": "(assignee = currentUser()) AND (updated >= startOfDay()) ORDER BY updated DESC",
        "limit": 20,
    }
    assert session._last_display is None
    assert "Анализ производительности за сегодня" in output
    assert "Затронуто задач: 2" in output
    assert "Завершено: PA-1 (Done)" in output
    assert "Фокус на высоком приоритете: PA-1 (Done)" in output


def test_print_handled_streams_llm_answer_and_remembers_full_text(capsys) -> None:
    class StreamingAssistant:
        def handle_prompt(self, *args: Any, **kwargs: Any) -> AgentResponse:
            raise AssertionError("print_handled should use streaming path")

        async def handle_prompt_stream(
            self,
            text: str,
            *,
            context: str = "",
            confirm: ConfirmCallback | None = None,
            on_text_delta=None,
        ) -> AgentResponse:
            if on_text_delta:
                on_text_delta("при")
                on_text_delta("вет")
            return AgentResponse("привет")

        def context_summary(self) -> str:
            return ""

        def reset_context(self) -> None:
            return None

    session = ChatSession(
        StreamingAssistant(),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
    )

    session.print_handled("скажи привет")

    output = capsys.readouterr().out
    assert "привет" in output
    assert (
        session._context_summary()
        == "Recent conversation:\nUser: скажи привет\nAssistant: привет"
    )


def test_print_handled_prints_final_text_when_stream_has_no_chunks(capsys) -> None:
    class NonStreamingAssistant:
        async def handle_prompt_stream(
            self,
            text: str,
            *,
            context: str = "",
            confirm: ConfirmCallback | None = None,
            on_text_delta=None,
        ) -> AgentResponse:
            return AgentResponse("готово")

        def handle_prompt(self, *args: Any, **kwargs: Any) -> AgentResponse:
            return AgentResponse("unused")

        def context_summary(self) -> str:
            return ""

        def reset_context(self) -> None:
            return None

    session = ChatSession(
        NonStreamingAssistant(),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
    )

    session.print_handled("сделай")

    output = capsys.readouterr().out
    assert "готово" in output


def test_terminal_ui_prints_gitlab_mr_review_as_structured_panel() -> None:
    console = Console(record=True, width=120, color_system=None)
    ui = TerminalUI(console=console)
    result = MRReviewResult(
        summary="MR в целом выглядит удачно.",
        risk_assessment="Средний риск по доступу к данным.",
        comments=[
            {
                "severity": ReviewSeverity.IMPORTANT,
                "file_path": "src/apps/external_users/api/urls/internal.py",
                "line": 16,
                "message": "Проверьте фильтрацию по текущему external_user.",
                "reason": "List endpoint может случайно вернуть чужие загрузки.",
                "suggested_change": "Добавьте тест на изоляцию загрузок.",
            }
        ],
        recommendation=ReviewRecommendation.APPROVE_WITH_SUGGESTIONS,
    )

    ui.print_display(AgentDisplay(kind="gitlab_mr_review", payload=result))

    output = console.export_text()
    assert "GitLab MR Review" in output
    assert "Summary" in output
    assert "Recommendation" in output
    assert "approve_with_suggestions" in output
    assert "#1 important" in output
    assert "Where" in output
    assert "src/apps/external_users/api/urls/internal.py:16" in output
    assert "Suggested" in output


def test_print_handled_catches_http_request_errors(capsys) -> None:
    class TimeoutAssistant:
        async def handle_prompt_stream(
            self,
            text: str,
            *,
            context: str = "",
            confirm: ConfirmCallback | None = None,
            on_text_delta=None,
        ) -> AgentResponse:
            raise httpx.ConnectTimeout("_ssl.c:1015: handshake timed out")

        def handle_prompt(self, *args: Any, **kwargs: Any) -> AgentResponse:
            return AgentResponse("unused")

        def context_summary(self) -> str:
            return ""

        def reset_context(self) -> None:
            return None

    session = ChatSession(
        TimeoutAssistant(),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
    )

    session.print_handled("сделай ревью")

    output = capsys.readouterr().out
    assert "Ошибка:" in output
    assert "handshake timed out" in output


def test_jira_agent_streams_text_answer_with_deltas() -> None:
    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
    )
    captured: dict[str, Any] = {}

    class FakePlanner:
        def run_sync(self, prompt: str):
            captured["planner_prompt"] = prompt

            class Result:
                output = JiraCommand(action="answer")

            return Result()

    class FakeStreamResult:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def stream_text(self, *, delta: bool = False, debounce_by=0.1):
            captured["delta"] = delta
            yield "при"
            yield "вет"

    class FakeTextAgent:
        def run_stream(self, text: str, *, deps: Settings):
            captured["text"] = text
            captured["deps"] = deps
            return FakeStreamResult()

    agent = JiraAgent(settings)
    agent._planner = FakePlanner()  # type: ignore[assignment]
    agent._agent = FakeTextAgent()  # type: ignore[assignment]
    chunks: list[str] = []

    response = asyncio.run(
        agent.handle_prompt_stream("скажи привет", on_text_delta=chunks.append)
    )

    assert response.text == "привет"
    assert chunks == ["при", "вет"]
    assert captured["text"] == "скажи привет"
    assert captured["deps"] is settings
    assert captured["delta"] is True


def test_local_planner_recognizes_productivity_request() -> None:
    agent = AssistantAgent(
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token")
    )

    command = agent.plan_command("проанализируй мою производительность за сегодня")

    assert command.action == "analyze_productivity"
    assert command.limit == 20


def test_chat_session_passes_recent_conversation_to_planner() -> None:
    class CapturingAgent:
        def __init__(self) -> None:
            self.contexts: list[str] = []

        def plan_command(self, prompt: str, *, context: str = "") -> JiraCommand:
            self.contexts.append(context)
            if prompt == "давай":
                return JiraCommand(action="answer", message="готово")
            return JiraCommand(action="answer", message="Нужно уточнение")

        def handle_text(self, text: str) -> str:
            return "fallback"

        def handle_prompt(
            self,
            text: str,
            *,
            context: str = "",
            confirm: ConfirmCallback | None = None,
        ) -> AgentResponse:
            command = self.plan_command(text, context=context)
            return AgentResponse(command.message or self.handle_text(text))

        def context_summary(self) -> str:
            return ""

        def reset_context(self) -> None:
            return None

    agent = CapturingAgent()
    session = ChatSession(
        agent,
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
    )

    assert session.handle("создай задачу внутри CCO-1914") == "Нужно уточнение"
    assert session.handle("давай") == "готово"

    assert "User: создай задачу внутри CCO-1914" in agent.contexts[1]
    assert "Assistant: Нужно уточнение" in agent.contexts[1]


def test_chat_session_creates_child_issue(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create_issue(*args: Any, **kwargs: Any) -> JiraIssue:
        captured.update(kwargs)
        return JiraIssue(
            key="CCO-2000",
            summary=kwargs["summary"],
            status="To Do",
            priority=None,
            assignee=None,
            url="https://example.atlassian.net/browse/CCO-2000",
        )

    monkeypatch.setattr(
        "personal_assistant.agents.jira.create_jira_issue", fake_create_issue
    )

    session = make_session(
        JiraCommand(
            action="create_issue",
            parent_key="CCO-1914",
            summary="Добавить DocumentUploads в просмотре документов",
            description="Нужно добавить DocumentUploads в просмотре документов.",
            issue_type="Task",
        ),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
        confirm=lambda preview: True,
    )

    output = session.handle("создай задачу внутри истории 1914")

    assert (
        output
        == "CCO-2000: задача создана.\nhttps://example.atlassian.net/browse/CCO-2000"
    )
    assert captured == {
        "summary": "Добавить DocumentUploads в просмотре документов",
        "issue_type": "Sub-task",
        "description": "Нужно добавить DocumentUploads в просмотре документов.",
        "parent_key": "CCO-1914",
    }
    assert session._context_summary().startswith("Current issue: CCO-2000")


def test_chat_session_keeps_explicit_child_issue_type(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create_issue(*args: Any, **kwargs: Any) -> JiraIssue:
        captured.update(kwargs)
        return JiraIssue(
            key="CCO-2001",
            summary=kwargs["summary"],
            status="To Do",
            priority=None,
            assignee=None,
            url="https://example.atlassian.net/browse/CCO-2001",
        )

    monkeypatch.setattr(
        "personal_assistant.agents.jira.create_jira_issue", fake_create_issue
    )

    session = make_session(
        JiraCommand(
            action="create_issue",
            parent_key="CCO-1914",
            summary="Добавить DocumentUploads в просмотре документов",
            issue_type="Sub-task",
        ),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
        confirm=lambda preview: True,
    )

    session.handle("создай подзадачу внутри истории 1914")

    assert captured["issue_type"] == "Sub-task"
