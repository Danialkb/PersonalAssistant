import asyncio
from dataclasses import replace

import httpx
import pytest

from personal_assistant.agents.gitlab import (
    GitLabMRReviewAgent,
    MRReviewPromptBuilder,
    MRReviewResult,
    ReviewRecommendation,
    ReviewSeverity,
    format_mr_review_result,
    parse_gitlab_mr_reference,
)
from personal_assistant.agents.jira import JiraCommand
from personal_assistant.assistant import AssistantAgent
from personal_assistant.clients.gitlab import (
    GitlabMergeRequest,
    GitlabMergeRequestChange,
    GitlabProject,
    GitlabUser,
)
from personal_assistant.clients.jira import JiraTransition
from personal_assistant.services.gitlab_mr import GitLabMRReviewContext
from personal_assistant.services.jira_code_review import (
    JiraCodeReviewUpdateResult,
    JiraCodeReviewUpdater,
    extract_jira_issue_key,
)
from personal_assistant.settings import Settings


def make_context() -> GitLabMRReviewContext:
    return GitLabMRReviewContext(
        project=GitlabProject(
            id=42,
            name="Assistant",
            path_with_namespace="team/assistant",
            web_url="https://gitlab.example.com/team/assistant",
            default_branch="main",
        ),
        merge_request=GitlabMergeRequest(
            id=100,
            iid=7,
            title="Add GitLab MR review agent",
            state="opened",
            source_branch="feature/reviewer",
            target_branch="main",
            web_url="https://gitlab.example.com/team/assistant/-/merge_requests/7",
            author=GitlabUser(id=1, username="oleg", name="Oleg"),
        ),
        changes=(
            GitlabMergeRequestChange(
                old_path="src/personal_assistant/agents/gitlab.py",
                new_path="src/personal_assistant/agents/gitlab.py",
                diff="@@ -1,2 +1,4 @@\n+class GitLabMRReviewAgent:\n+    pass",
                new_file=True,
            ),
        ),
    )


def test_prompt_builder_keeps_review_focused_and_includes_diff() -> None:
    prompt = MRReviewPromptBuilder().build(make_context())

    assert "Prefer fewer, higher-quality comments" in prompt
    assert "Do not comment on style preferences" in prompt
    assert "Add GitLab MR review agent" in prompt
    assert "src/personal_assistant/agents/gitlab.py" in prompt
    assert "```diff" in prompt


def test_review_agent_loads_context_and_returns_structured_result() -> None:
    captured: dict[str, object] = {}

    class StubService:
        def load_review_context(
            self, project: int | str, merge_request_iid: int
        ) -> GitLabMRReviewContext:
            captured["project"] = project
            captured["merge_request_iid"] = merge_request_iid
            return make_context()

    class StubReviewer:
        def review(self, prompt: str) -> MRReviewResult:
            captured["prompt"] = prompt
            return MRReviewResult(
                summary="Adds a focused MR reviewer.",
                risk_assessment="Low risk; review is read-only.",
                comments=[],
                recommendation=ReviewRecommendation.APPROVE,
            )

    agent = GitLabMRReviewAgent(StubService(), reviewer=StubReviewer())  # type: ignore[arg-type]

    result = agent.review_merge_request("team/assistant", 7)

    assert captured["project"] == "team/assistant"
    assert captured["merge_request_iid"] == 7
    assert "Review this GitLab merge request" in str(captured["prompt"])
    assert result.recommendation == ReviewRecommendation.APPROVE


def test_parse_gitlab_mr_reference_from_url() -> None:
    reference = parse_gitlab_mr_reference(
        "проверь MR "
        "https://gitlab.company.com/cff/corporate-offline-cabinet/-/merge_requests/1680"
    )

    assert reference == ("cff/corporate-offline-cabinet", 1680)


def test_parse_gitlab_mr_reference_from_project_and_iid() -> None:
    reference = parse_gitlab_mr_reference(
        "проверь gitlab mr cff/corporate-offline-cabinet 1680"
    )

    assert reference == ("cff/corporate-offline-cabinet", 1680)


def test_extract_jira_issue_key_uses_prompt_before_mr_metadata() -> None:
    issue_key = extract_jira_issue_key(
        "сделай review для CCO-2209",
        "CCO-1111 Add feature",
        "feature/CCO-2222-review",
    )

    assert issue_key == "CCO-2209"


def test_extract_jira_issue_key_uses_title_then_source_branch() -> None:
    assert (
        extract_jira_issue_key(
            "сделай review", "CCO-1111 Add feature", "feature/CCO-2222-review"
        )
        == "CCO-1111"
    )
    assert (
        extract_jira_issue_key(
            "сделай review", "Add feature", "feature/CCO-2222-review"
        )
        == "CCO-2222"
    )


def test_jira_code_review_updater_requires_confirmation(monkeypatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr(
        "personal_assistant.services.jira_code_review.get_jira_transitions",
        lambda *args, **kwargs: [
            JiraTransition(id="21", name="Code Review", target_status="Code Review")
        ],
    )

    def fake_transition(*args, **kwargs):
        called["transition"] = kwargs
        return "CCO-2209: выполнен transition Code Review."

    monkeypatch.setattr(
        "personal_assistant.services.jira_code_review.transition_jira_issue",
        fake_transition,
    )

    updater = JiraCodeReviewUpdater(
        Settings(
            JIRA_BASE_URL="https://example.atlassian.net",
            JIRA_API_KEY="token",
            JIRA_PROJECT_KEY="CCO",
        )
    )

    result = updater.move_to_code_review("2209", confirm=lambda preview: False)

    assert result.issue_key == "CCO-2209"
    assert result.changed is False
    assert "изменение отменено" in result.message
    assert called == {}


def test_jira_code_review_updater_transitions_after_confirmation(monkeypatch) -> None:
    previews: list[str] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "personal_assistant.services.jira_code_review.get_jira_transitions",
        lambda *args, **kwargs: [
            JiraTransition(id="21", name="Review", target_status="Code Review")
        ],
    )

    def fake_transition(*args, **kwargs):
        captured.update(kwargs)
        return "CCO-2209: статус изменен на Code Review через transition Review."

    monkeypatch.setattr(
        "personal_assistant.services.jira_code_review.transition_jira_issue",
        fake_transition,
    )

    updater = JiraCodeReviewUpdater(
        Settings(
            JIRA_BASE_URL="https://example.atlassian.net",
            JIRA_API_KEY="token",
            JIRA_PROJECT_KEY="CCO",
        )
    )

    result = updater.move_to_code_review(
        "CCO-2209", confirm=lambda preview: previews.append(preview) or True
    )

    assert previews == ["Изменить статус CCO-2209: Review -> Code Review"]
    assert captured == {
        "issue_key": "CCO-2209",
        "transition_name": "Review",
    }
    assert result.changed is True
    assert result.message.startswith("Jira: CCO-2209: статус изменен")


def test_jira_code_review_updater_reports_unavailable_transition(monkeypatch) -> None:
    called: dict[str, object] = {}

    monkeypatch.setattr(
        "personal_assistant.services.jira_code_review.get_jira_transitions",
        lambda *args, **kwargs: [
            JiraTransition(id="31", name="Ready for Testing", target_status="Testing")
        ],
    )
    monkeypatch.setattr(
        "personal_assistant.services.jira_code_review.transition_jira_issue",
        lambda *args, **kwargs: called.update({"transition": kwargs}),
    )

    updater = JiraCodeReviewUpdater(
        Settings(
            JIRA_BASE_URL="https://example.atlassian.net",
            JIRA_API_KEY="token",
            JIRA_PROJECT_KEY="CCO",
        )
    )

    result = updater.move_to_code_review("CCO-2209", confirm=lambda preview: True)

    assert result.issue_key == "CCO-2209"
    assert result.changed is False
    assert "transition/status недоступен" in result.message
    assert "Ready for Testing" in result.message
    assert called == {}


def test_review_agent_handles_cli_prompt_with_mr_url() -> None:
    captured: dict[str, object] = {}

    class StubService:
        def load_review_context(
            self, project: int | str, merge_request_iid: int
        ) -> GitLabMRReviewContext:
            captured["project"] = project
            captured["merge_request_iid"] = merge_request_iid
            return make_context()

    class StubReviewer:
        def review(self, prompt: str) -> MRReviewResult:
            return MRReviewResult(
                summary="MR is ready for review.",
                risk_assessment="Low risk.",
                comments=[],
                recommendation=ReviewRecommendation.APPROVE,
            )

    agent = GitLabMRReviewAgent(StubService(), reviewer=StubReviewer())  # type: ignore[arg-type]

    response = agent.handle_prompt(
        "проверь MR "
        "https://gitlab.company.com/cff/corporate-offline-cabinet/-/merge_requests/1680"
    )

    assert captured == {
        "project": "cff/corporate-offline-cabinet",
        "merge_request_iid": 1680,
    }
    assert "Summary: MR is ready for review." in response.text
    assert "Recommendation: approve" in response.text
    assert response.display is not None
    assert response.display.kind == "gitlab_mr_review"
    assert response.display.payload.recommendation == ReviewRecommendation.APPROVE


def test_review_agent_updates_jira_before_reviewing_mr() -> None:
    events: list[str] = []
    context = replace(
        make_context(),
        merge_request=replace(
            make_context().merge_request,
            title="CCO-2209 Add GitLab MR review agent",
            source_branch="feature/reviewer",
        ),
    )

    class StubService:
        def load_review_context(
            self, project: int | str, merge_request_iid: int
        ) -> GitLabMRReviewContext:
            return context

    class StubJiraUpdater:
        def update_from_texts(self, texts, *, confirm):
            events.append("jira")
            assert texts == [
                "проверь MR https://gitlab.company.com/team/assistant/-/merge_requests/7",
                "CCO-2209 Add GitLab MR review agent",
                "feature/reviewer",
            ]
            assert confirm("Изменить статус CCO-2209: Code Review") is True
            return JiraCodeReviewUpdateResult(
                issue_key="CCO-2209",
                message="Jira: CCO-2209: статус изменен на Code Review.",
                changed=True,
            )

    class StubReviewer:
        def review(self, prompt: str) -> MRReviewResult:
            events.append("review")
            return MRReviewResult(
                summary="MR is ready for review.",
                risk_assessment="Low risk.",
                comments=[],
                recommendation=ReviewRecommendation.APPROVE,
            )

    agent = GitLabMRReviewAgent(
        StubService(),  # type: ignore[arg-type]
        reviewer=StubReviewer(),
        jira_updater=StubJiraUpdater(),
    )

    response = agent.handle_prompt(
        "проверь MR https://gitlab.company.com/team/assistant/-/merge_requests/7",
        confirm=lambda preview: preview == "Изменить статус CCO-2209: Code Review",
    )

    assert events == ["jira", "review"]
    assert response.text.startswith("Jira: CCO-2209: статус изменен")
    assert "Summary: MR is ready for review." in response.text


def test_review_agent_reviews_mr_when_jira_update_is_skipped() -> None:
    events: list[str] = []

    class StubService:
        def load_review_context(
            self, project: int | str, merge_request_iid: int
        ) -> GitLabMRReviewContext:
            return make_context()

    class StubJiraUpdater:
        def update_from_texts(self, texts, *, confirm):
            events.append("jira")
            return JiraCodeReviewUpdateResult(
                issue_key=None,
                message="Jira: ключ задачи не найден, review выполнен без обновления Jira.",
            )

    class StubReviewer:
        def review(self, prompt: str) -> MRReviewResult:
            events.append("review")
            return MRReviewResult(
                summary="Review still ran.",
                risk_assessment="Low risk.",
                comments=[],
                recommendation=ReviewRecommendation.APPROVE,
            )

    agent = GitLabMRReviewAgent(
        StubService(),  # type: ignore[arg-type]
        reviewer=StubReviewer(),
        jira_updater=StubJiraUpdater(),
    )

    response = agent.handle_prompt(
        "проверь MR https://gitlab.company.com/team/assistant/-/merge_requests/7"
    )

    assert events == ["jira", "review"]
    assert "Jira: ключ задачи не найден" in response.text
    assert "Summary: Review still ran." in response.text


def test_review_agent_asks_reviewer_to_answer_in_user_prompt_language() -> None:
    captured: dict[str, object] = {}

    class StubService:
        def load_review_context(
            self, project: int | str, merge_request_iid: int
        ) -> GitLabMRReviewContext:
            return make_context()

    class StubReviewer:
        def review(self, prompt: str) -> MRReviewResult:
            captured["prompt"] = prompt
            return MRReviewResult(
                summary="Серьезных проблем не найдено.",
                risk_assessment="Низкий риск.",
                comments=[],
                recommendation=ReviewRecommendation.APPROVE,
            )

    agent = GitLabMRReviewAgent(StubService(), reviewer=StubReviewer())  # type: ignore[arg-type]

    agent.handle_prompt(
        "проверь MR "
        "https://gitlab.company.com/cff/corporate-offline-cabinet/-/merge_requests/1680"
    )

    prompt = str(captured["prompt"])
    assert "User request:" in prompt
    assert "проверь MR https://gitlab.company.com" in prompt
    assert "same language as the user request" in prompt


def test_review_agent_stream_uses_async_reviewer_inside_event_loop() -> None:
    captured: dict[str, object] = {}

    class StubService:
        def load_review_context(
            self, project: int | str, merge_request_iid: int
        ) -> GitLabMRReviewContext:
            captured["project"] = project
            captured["merge_request_iid"] = merge_request_iid
            return make_context()

    class StubReviewer:
        def review(self, prompt: str) -> MRReviewResult:
            raise AssertionError("stream path should not call sync review")

        async def review_async(self, prompt: str) -> MRReviewResult:
            captured["prompt"] = prompt
            return MRReviewResult(
                summary="Async review works in CLI stream mode.",
                risk_assessment="Low risk.",
                comments=[],
                recommendation=ReviewRecommendation.APPROVE,
            )

    agent = GitLabMRReviewAgent(StubService(), reviewer=StubReviewer())  # type: ignore[arg-type]

    response = asyncio.run(
        agent.handle_prompt_stream(
            "проверь MR "
            "https://gitlab.company.com/cff/corporate-offline-cabinet/-/merge_requests/1679"
        )
    )

    assert captured["project"] == "cff/corporate-offline-cabinet"
    assert captured["merge_request_iid"] == 1679
    assert "Review this GitLab merge request" in str(captured["prompt"])
    assert "Summary: Async review works in CLI stream mode." in response.text
    assert "Recommendation: approve" in response.text
    assert response.display is not None
    assert response.display.kind == "gitlab_mr_review"


def test_review_agent_reports_gitlab_connect_timeout() -> None:
    class StubService:
        def load_review_context(
            self, project: int | str, merge_request_iid: int
        ) -> GitLabMRReviewContext:
            request = httpx.Request(
                "GET",
                "https://gitlab.example.com/api/v4/projects/team%2Fassistant",
            )
            raise httpx.ConnectTimeout("timed out", request=request)

    class StubReviewer:
        def review(self, prompt: str) -> MRReviewResult:
            raise AssertionError("review should not start without GitLab context")

    agent = GitLabMRReviewAgent(StubService(), reviewer=StubReviewer())  # type: ignore[arg-type]

    with pytest.raises(ValueError) as exc_info:
        agent.review_merge_request("team/assistant", 7)

    message = str(exc_info.value)
    assert "Не могу подключиться к GitLab (https://gitlab.example.com)" in message
    assert "VPN" in message


def test_assistant_routes_gitlab_mr_url_to_gitlab_agent() -> None:
    class StubJiraAgent:
        name = "jira"

        def handle_text(self, text: str) -> str:
            return "jira text"

        def plan_command(self, text: str, *, context: str = "") -> JiraCommand:
            return JiraCommand(action="answer", message="jira plan")

        def handle_prompt(self, text: str, *, context: str = "", confirm=None):
            return self.plan_command(text, context=context)

        def context_summary(self) -> str:
            return ""

        def reset_context(self) -> None:
            return None

    class StubGitLabAgent:
        name = "gitlab"

        def handle_text(self, text: str) -> str:
            return "gitlab text"

        def plan_command(self, text: str, *, context: str = "") -> MRReviewResult:
            return MRReviewResult(
                summary="gitlab",
                risk_assessment="low",
                comments=[],
                recommendation=ReviewRecommendation.APPROVE,
            )

        def handle_prompt(self, text: str, *, context: str = "", confirm=None):
            return self.plan_command(text, context=context)

        def context_summary(self) -> str:
            return ""

        def reset_context(self) -> None:
            return None

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
    )
    assistant = AssistantAgent(
        settings,
        agents=[StubJiraAgent(), StubGitLabAgent()],  # type: ignore[list-item]
    )

    command = assistant.plan_command(
        "проверь MR https://gitlab.company.com/cff/app/-/merge_requests/1680"
    )

    assert isinstance(command, MRReviewResult)
    assert command.summary == "gitlab"


def test_assistant_does_not_route_gitlab_mr_url_to_jira_when_gitlab_is_missing() -> (
    None
):
    class StubJiraAgent:
        name = "jira"

        def handle_text(self, text: str) -> str:
            return "jira text"

        def plan_command(self, text: str, *, context: str = "") -> JiraCommand:
            return JiraCommand(action="answer", message="jira plan")

        def handle_prompt(self, text: str, *, context: str = "", confirm=None):
            raise AssertionError("GitLab MR URL should not be routed to Jira")

        def context_summary(self) -> str:
            return ""

        def reset_context(self) -> None:
            return None

    settings = Settings(
        JIRA_BASE_URL="https://example.atlassian.net",
        JIRA_API_KEY="token",
    )
    assistant = AssistantAgent(
        settings,
        agents=[StubJiraAgent()],  # type: ignore[list-item]
    )

    response = assistant.handle_prompt(
        "проверь MR https://gitlab.company.com/cff/app/-/merge_requests/1680"
    )

    assert "GitLab не настроен" in response.text
    assert "GITLAB_BASE_URL" in response.text
    assert "GITLAB_TOKEN" in response.text


def test_format_mr_review_result_is_concise() -> None:
    result = MRReviewResult(
        summary="MR mostly fits the existing structure.",
        risk_assessment="Medium risk around error handling.",
        comments=[
            {
                "severity": ReviewSeverity.IMPORTANT,
                "file_path": "src/client.py",
                "line": 12,
                "message": "Handle 404 separately before treating the response as retryable.",
                "reason": "A missing MR is a user error, not an infrastructure failure.",
                "suggested_change": "Map 404 to a clear ValueError with project and MR IID.",
            }
        ],
        recommendation=ReviewRecommendation.APPROVE_WITH_SUGGESTIONS,
    )

    output = format_mr_review_result(result)

    assert "Recommendation: approve_with_suggestions" in output
    assert "[important] src/client.py:12" in output
    assert "Handle 404 separately" in output
