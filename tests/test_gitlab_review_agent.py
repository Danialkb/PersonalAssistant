import asyncio

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
from personal_assistant.services.gitlab_mr import GitLabMRReviewContext
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


def test_assistant_does_not_route_gitlab_mr_url_to_jira_when_gitlab_is_missing() -> None:
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
