from personal_assistant.agents.gitlab import (
    GitLabMRReviewAgent,
    MRReviewPromptBuilder,
    MRReviewResult,
    ReviewRecommendation,
    ReviewSeverity,
    format_mr_review_result,
)
from personal_assistant.clients.gitlab import (
    GitlabMergeRequest,
    GitlabMergeRequestChange,
    GitlabProject,
    GitlabUser,
)
from personal_assistant.services.gitlab_mr import GitLabMRReviewContext


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
