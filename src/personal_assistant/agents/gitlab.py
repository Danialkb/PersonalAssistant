from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from personal_assistant.services.gitlab_mr import GitLabMRReviewContext, GitLabMRService
from personal_assistant.settings import Settings


class ReviewSeverity(StrEnum):
    BLOCKING = "blocking"
    IMPORTANT = "important"
    SUGGESTION = "suggestion"
    PRAISE = "praise"


class ReviewRecommendation(StrEnum):
    APPROVE = "approve"
    APPROVE_WITH_SUGGESTIONS = "approve_with_suggestions"
    REQUEST_CHANGES = "request_changes"


class MRReviewComment(BaseModel):
    severity: ReviewSeverity
    file_path: str
    line: int | None = Field(default=None, ge=1)
    message: str
    reason: str
    suggested_change: str = ""


class MRReviewResult(BaseModel):
    summary: str
    risk_assessment: str
    comments: list[MRReviewComment] = Field(default_factory=list)
    recommendation: ReviewRecommendation


class MRReviewer(Protocol):
    def review(self, prompt: str) -> MRReviewResult: ...


REVIEWER_INSTRUCTIONS = (
    "You are a GitLab Merge Request reviewer. Return only structured output. "
    "Review the MR as a whole before commenting. Be concise, practical, and respectful. "
    "Do not nitpick. Do not ask for changes only because a different style is possible. "
    "Only include actionable comments that are genuinely useful. "
    "Prioritize architecture, correctness, security, meaningful edge cases, and important tests. "
    "Use blocking only for bugs, security issues, broken architecture, incorrect business logic, "
    "serious maintainability problems, missing critical tests, or regressions. "
    "If the MR is good, say there are no major issues and recommend approve. "
    "Use praise sparingly for decisions that are actually worth calling out."
)


class PydanticAIMRReviewer:
    def __init__(self, model: Any) -> None:
        self._agent = Agent(
            model,
            output_type=MRReviewResult,
            instructions=REVIEWER_INSTRUCTIONS,
        )

    def review(self, prompt: str) -> MRReviewResult:
        return self._agent.run_sync(prompt).output


class MRReviewPromptBuilder:
    def __init__(
        self, *, max_diff_chars: int = 60000, max_file_diff_chars: int = 12000
    ) -> None:
        self._max_diff_chars = max_diff_chars
        self._max_file_diff_chars = max_file_diff_chars

    def build(self, context: GitLabMRReviewContext) -> str:
        mr = context.merge_request
        author = mr.author.username if mr.author else "unknown"
        lines = [
            "Review this GitLab merge request.",
            "",
            "Review policy:",
            "- Focus on meaningful engineering feedback.",
            "- Prefer fewer, higher-quality comments over many minor comments.",
            "- Do not comment on style preferences unless they hide real maintainability risk.",
            "- Include tests feedback only for meaningful behavior or risk.",
            "- Return the requested structured result.",
            "",
            "Project:",
            f"- id: {context.project.id}",
            f"- path: {context.project.path_with_namespace}",
            f"- default_branch: {context.project.default_branch or 'unknown'}",
            "",
            "Merge request:",
            f"- iid: {mr.iid}",
            f"- title: {mr.title}",
            f"- state: {mr.state}",
            f"- source_branch: {mr.source_branch}",
            f"- target_branch: {mr.target_branch}",
            f"- author: {author}",
            f"- draft: {mr.draft}",
            f"- url: {mr.web_url}",
            "",
            "Changed files:",
        ]
        lines.extend(f"- {path}" for path in context.changed_file_paths)
        lines.extend(["", "Diffs:"])
        lines.extend(self._format_diffs(context))
        return "\n".join(lines)

    def _format_diffs(self, context: GitLabMRReviewContext) -> list[str]:
        remaining = self._max_diff_chars
        output: list[str] = []
        for change in context.changes:
            if remaining <= 0:
                output.append(
                    "\n[Diff content truncated: MR is too large for one review prompt.]"
                )
                break

            header = self._change_header(
                change.file_path, change.old_path, change.new_path
            )
            diff = change.diff[
                : min(len(change.diff), self._max_file_diff_chars, remaining)
            ]
            if len(diff) < len(change.diff):
                diff = f"{diff}\n[File diff truncated.]"
            block = f"\n--- {header}\n```diff\n{diff}\n```"
            output.append(block)
            remaining -= len(diff)
        return output

    @staticmethod
    def _change_header(file_path: str, old_path: str, new_path: str) -> str:
        if old_path and new_path and old_path != new_path:
            return f"{old_path} -> {new_path}"
        return file_path


class GitLabMRReviewAgent:
    name = "gitlab"

    def __init__(
        self,
        service: GitLabMRService,
        *,
        prompt_builder: MRReviewPromptBuilder | None = None,
        reviewer: MRReviewer | None = None,
        model: Any | None = None,
    ) -> None:
        self._service = service
        self._prompt_builder = prompt_builder or MRReviewPromptBuilder()
        self._reviewer = reviewer or (
            PydanticAIMRReviewer(model) if model is not None else None
        )

    @classmethod
    def from_settings(
        cls, settings: Settings, *, model: Any | None = None
    ) -> "GitLabMRReviewAgent":
        return cls(GitLabMRService.from_settings(settings), model=model)

    def review_merge_request(
        self, project: int | str, merge_request_iid: int
    ) -> MRReviewResult:
        if self._reviewer is None:
            raise ValueError(
                "OPENAI_API_KEY is required to review GitLab merge requests"
            )
        context = self._service.load_review_context(project, merge_request_iid)
        prompt = self._prompt_builder.build(context)
        return self._reviewer.review(prompt)

    def handle_text(self, text: str) -> str:
        return (
            "GitLab MR reviewer expects a project and merge request IID. "
            "Use GitLabMRReviewAgent.review_merge_request(project, merge_request_iid)."
        )

    def plan_command(self, text: str, *, context: str = "") -> MRReviewResult:
        return MRReviewResult(
            summary="GitLab MR review needs explicit project and merge request IID.",
            risk_assessment="Not reviewed.",
            comments=[],
            recommendation=ReviewRecommendation.APPROVE_WITH_SUGGESTIONS,
        )


def format_mr_review_result(result: MRReviewResult) -> str:
    lines = [
        f"Summary: {result.summary}",
        f"Risk: {result.risk_assessment}",
        f"Recommendation: {result.recommendation.value}",
    ]
    if not result.comments:
        lines.append("Comments: no major issues.")
        return "\n".join(lines)

    lines.append("Comments:")
    for comment in result.comments:
        location = comment.file_path
        if comment.line:
            location = f"{location}:{comment.line}"
        lines.append(f"- [{comment.severity.value}] {location}: {comment.message}")
        if comment.reason:
            lines.append(f"  Reason: {comment.reason}")
        if comment.suggested_change:
            lines.append(f"  Suggested change: {comment.suggested_change}")
    return "\n".join(lines)
