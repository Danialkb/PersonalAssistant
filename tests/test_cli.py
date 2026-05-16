from typing import Any

from personal_assistant.agents.jira import JiraCommand
from personal_assistant.assistant import AssistantAgent
from personal_assistant.clients.jira import JiraIssue, JiraTransition
from personal_assistant.cli import ChatSession, main
from personal_assistant.settings import Settings


class StubAgent:
    def __init__(self, command: JiraCommand) -> None:
        self.command = command

    def plan_command(self, prompt: str, *, context: str = "") -> JiraCommand:
        return self.command

    def handle_text(self, text: str) -> str:
        return "fallback"


def stub_transitions(*args: Any, **kwargs: Any) -> list[JiraTransition]:
    return [
        JiraTransition(id="11", name="In Progress", target_status="In Progress"),
        JiraTransition(id="21", name="Code Review", target_status="Code Review"),
        JiraTransition(id="31", name="Ready for Testing", target_status="Ready for Testing"),
    ]


def test_chat_session_does_not_apply_write_without_confirmation(monkeypatch) -> None:
    called: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        called["transition"] = kwargs
        return "changed"

    monkeypatch.setattr("personal_assistant.cli.transition_jira_issue", fake_transition)
    monkeypatch.setattr("personal_assistant.cli.get_jira_transitions", stub_transitions)

    session = ChatSession(
        StubAgent(JiraCommand(action="transition", issue_key="PA-12", transition="In Progress")),
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

    monkeypatch.setattr("personal_assistant.cli.transition_jira_issue", fake_transition)
    monkeypatch.setattr("personal_assistant.cli.get_jira_transitions", stub_transitions)

    session = ChatSession(
        StubAgent(JiraCommand(action="transition", issue_key="PA-12", transition="In Progress")),
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

    monkeypatch.setattr("personal_assistant.cli.transition_jira_issue", fake_transition)
    monkeypatch.setattr("personal_assistant.cli.get_jira_transitions", stub_transitions)

    session = ChatSession(
        StubAgent(JiraCommand(action="transition", issue_key="2284", transition="Code Review")),
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
    session = ChatSession(
        StubAgent(JiraCommand(action="transition", issue_key="2321", transition="Ready for Testing")),
        settings,
        confirm=lambda preview: True,
    )
    monkeypatch.setattr("personal_assistant.cli.transition_jira_issue", fake_transition)
    monkeypatch.setattr("personal_assistant.cli.get_jira_transitions", stub_transitions)

    try:
        session.handle("закинь 2321 в ready for testing")
    except ValueError:
        pass

    assert session._context_summary() == "Current issue: CCO-2321"


def test_one_shot_prompt_uses_chat_executor(monkeypatch, capsys) -> None:
    class StubSettings:
        JIRA_PROJECT_KEY = "CCO"
        default_jira_jql = "assignee = currentUser() ORDER BY updated DESC"

    class StubAssistant:
        def __init__(self, settings: StubSettings) -> None:
            pass

        def plan_command(self, prompt: str, *, context: str = "") -> JiraCommand:
            return JiraCommand(action="transition", issue_key="2284", transition="Code Review")

        def handle_text(self, text: str) -> str:
            return "fallback"

    captured: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "CCO-2284: выполнен transition Code Review."

    monkeypatch.setattr("sys.argv", ["jira", "перенеси", "задачу", "2284", "в", "jira", "установи", "Code review"])
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr("personal_assistant.cli.get_settings", lambda: StubSettings())
    monkeypatch.setattr("personal_assistant.cli.AssistantAgent", StubAssistant)
    monkeypatch.setattr("personal_assistant.cli.transition_jira_issue", fake_transition)
    monkeypatch.setattr("personal_assistant.cli.get_jira_transitions", stub_transitions)

    main()

    output = capsys.readouterr().out
    assert "Изменить статус CCO-2284: Code Review" in output
    assert "CCO-2284: выполнен transition Code Review." in output
    assert captured["issue_key"] == "CCO-2284"


def test_chat_session_rejects_unknown_transition_before_confirmation(monkeypatch) -> None:
    called: dict[str, Any] = {}

    def fake_confirm(preview: str) -> bool:
        called["confirm"] = preview
        return True

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        called["transition"] = kwargs
        return "changed"

    monkeypatch.setattr("personal_assistant.cli.transition_jira_issue", fake_transition)
    monkeypatch.setattr(
        "personal_assistant.cli.get_jira_transitions",
        lambda *args, **kwargs: [
            JiraTransition(id="21", name="Code Review", target_status="Code Review"),
            JiraTransition(id="31", name="Ready for Testing", target_status="Ready for Testing"),
        ],
    )

    session = ChatSession(
        StubAgent(JiraCommand(action="transition", issue_key="2329", transition="QA Review")),
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


def test_chat_session_normalizes_partial_transition_to_existing_option(monkeypatch) -> None:
    previews: list[str] = []
    captured: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "CCO-2329: выполнен transition Code Review."

    monkeypatch.setattr("personal_assistant.cli.transition_jira_issue", fake_transition)
    monkeypatch.setattr(
        "personal_assistant.cli.get_jira_transitions",
        lambda *args, **kwargs: [
            JiraTransition(id="21", name="Code Review", target_status="Code Review"),
            JiraTransition(id="31", name="Ready for Testing", target_status="Ready for Testing"),
        ],
    )

    session = ChatSession(
        StubAgent(JiraCommand(action="transition", issue_key="2329", transition="Review")),
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

    class StubAssistant:
        def __init__(self, settings: StubSettings) -> None:
            pass

        def plan_command(self, prompt: str, *, context: str = "") -> JiraCommand:
            return JiraCommand(action="answer", message=f"ответ: {prompt}")

        def handle_text(self, text: str) -> str:
            return "fallback"

    inputs = iter(["/exit"])
    monkeypatch.setattr("sys.argv", ["assistant", "привет"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr("personal_assistant.cli.get_settings", lambda: StubSettings())
    monkeypatch.setattr("personal_assistant.cli.AssistantAgent", StubAssistant)

    main()

    output = capsys.readouterr().out
    assert "Jira chat. /exit чтобы выйти, /clear чтобы очистить контекст." in output
    assert "ответ: привет" in output


def test_chat_session_search_stores_context_and_resolves_first_issue(monkeypatch) -> None:
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

    settings = Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token")
    session = ChatSession(
        StubAgent(JiraCommand(action="search", limit=5)),
        settings,
        confirm=lambda preview: True,
    )
    monkeypatch.setattr("personal_assistant.cli.search_jira_issues", fake_search)

    output = session.handle("покажи задачи")

    assert "PA-1: First" in output

    session._agent = StubAgent(JiraCommand(action="comment", issue_key="первая", comment="беру в работу"))
    monkeypatch.setattr("personal_assistant.cli.add_jira_comment", fake_comment)

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

    settings = Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token")
    session = ChatSession(
        StubAgent(JiraCommand(action="analyze_productivity", limit=20)),
        settings,
    )
    monkeypatch.setattr("personal_assistant.cli.search_jira_issues", fake_search)

    output = session.handle("проанализируй мою производительность за сегодня")

    assert captured == {
        "jql": "(assignee = currentUser()) AND (updated >= startOfDay()) ORDER BY updated DESC",
        "limit": 20,
    }
    assert session._last_output_issues is None
    assert "Анализ производительности за сегодня" in output
    assert "Затронуто задач: 2" in output
    assert "Завершено: PA-1 (Done)" in output
    assert "Фокус на высоком приоритете: PA-1 (Done)" in output


def test_local_planner_recognizes_productivity_request() -> None:
    agent = AssistantAgent(Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"))

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

    monkeypatch.setattr("personal_assistant.cli.create_jira_issue", fake_create_issue)

    session = ChatSession(
        StubAgent(
            JiraCommand(
                action="create_issue",
                parent_key="CCO-1914",
                summary="Добавить DocumentUploads в просмотре документов",
                description="Нужно добавить DocumentUploads в просмотре документов.",
                issue_type="Task",
            )
        ),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
        confirm=lambda preview: True,
    )

    output = session.handle("создай задачу внутри истории 1914")

    assert output == "CCO-2000: задача создана.\nhttps://example.atlassian.net/browse/CCO-2000"
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

    monkeypatch.setattr("personal_assistant.cli.create_jira_issue", fake_create_issue)

    session = ChatSession(
        StubAgent(
            JiraCommand(
                action="create_issue",
                parent_key="CCO-1914",
                summary="Добавить DocumentUploads в просмотре документов",
                issue_type="Sub-task",
            )
        ),
        Settings(JIRA_BASE_URL="https://example.atlassian.net", JIRA_API_KEY="token"),
        confirm=lambda preview: True,
    )

    session.handle("создай подзадачу внутри истории 1914")

    assert captured["issue_type"] == "Sub-task"
