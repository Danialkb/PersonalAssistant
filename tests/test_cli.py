from typing import Any

from jira.agent import JiraCommand
from jira.cli import ChatSession, main
from jira.jira_client import JiraIssue
from jira.settings import Settings


class StubAgent:
    def __init__(self, command: JiraCommand) -> None:
        self.command = command

    def plan_command(self, prompt: str, *, context: str = "") -> JiraCommand:
        return self.command

    def handle_text(self, text: str) -> str:
        return "fallback"


def test_chat_session_does_not_apply_write_without_confirmation(monkeypatch) -> None:
    called: dict[str, Any] = {}

    def fake_transition(*args: Any, **kwargs: Any) -> str:
        called["transition"] = kwargs
        return "changed"

    monkeypatch.setattr("jira.cli.transition_jira_issue", fake_transition)

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

    monkeypatch.setattr("jira.cli.transition_jira_issue", fake_transition)

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

    monkeypatch.setattr("jira.cli.transition_jira_issue", fake_transition)

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
    monkeypatch.setattr("jira.cli.get_settings", lambda: StubSettings())
    monkeypatch.setattr("jira.cli.AssistantAgent", StubAssistant)
    monkeypatch.setattr("jira.cli.transition_jira_issue", fake_transition)

    main()

    output = capsys.readouterr().out
    assert "Изменить статус CCO-2284: Code Review" in output
    assert "CCO-2284: выполнен transition Code Review." in output
    assert captured["issue_key"] == "CCO-2284"


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
    monkeypatch.setattr("jira.cli.get_settings", lambda: StubSettings())
    monkeypatch.setattr("jira.cli.AssistantAgent", StubAssistant)

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
    monkeypatch.setattr("jira.cli.search_jira_issues", fake_search)

    output = session.handle("покажи задачи")

    assert "PA-1: First" in output

    session._agent = StubAgent(JiraCommand(action="comment", issue_key="первая", comment="беру в работу"))
    monkeypatch.setattr("jira.cli.add_jira_comment", fake_comment)

    output = session.handle("напиши в первую")

    assert output == "commented"
    assert captured["issue_key"] == "PA-1"
    assert captured["text"] == "беру в работу"
