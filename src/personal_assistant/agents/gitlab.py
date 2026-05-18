import asyncio
import re
from enum import StrEnum
from typing import Any, Literal, Protocol
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from personal_assistant.agents.base import (
    AgentDisplay,
    AgentResponse,
    ConfirmCallback,
    TextStreamCallback,
)
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


class GitLabMRCommand(BaseModel):
    action: Literal["answer", "review"] = "answer"
    message: str = ""
    project: str | None = None
    merge_request_iid: int | None = Field(default=None, ge=1)

    @property
    def can_review(self) -> bool:
        return bool(self.project and self.merge_request_iid)


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

    async def review_async(self, prompt: str) -> MRReviewResult:
        return (await self._agent.run(prompt)).output


class MRReviewPromptBuilder:
    def __init__(
        self, *, max_diff_chars: int = 60000, max_file_diff_chars: int = 12000
    ) -> None:
        self._max_diff_chars = max_diff_chars
        self._max_file_diff_chars = max_file_diff_chars

    def build(self, context: GitLabMRReviewContext, *, user_prompt: str = "") -> str:
        mr = context.merge_request
        author = mr.author.username if mr.author else "unknown"
        lines = [
            "Review this GitLab merge request.",
            "",
            "User request:",
            user_prompt or "[not provided]",
            "",
            "Review policy:",
            "- Focus on meaningful engineering feedback.",
            "- Prefer fewer, higher-quality comments over many minor comments.",
            "- Do not comment on style preferences unless they hide real maintainability risk.",
            "- Include tests feedback only for meaningful behavior or risk.",
            "- Write summary, risk assessment, comments, reasons, and suggested changes in the same language as the user request.",
            "- Keep enum values unchanged.",
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
        self, project: int | str, merge_request_iid: int, *, user_prompt: str = ""
    ) -> MRReviewResult:
        if self._reviewer is None:
            raise ValueError(
                "OPENAI_API_KEY is required to review GitLab merge requests"
            )
        context = self._load_review_context(project, merge_request_iid)
        prompt = self._prompt_builder.build(context, user_prompt=user_prompt)
        return self._reviewer.review(prompt)

    async def review_merge_request_async(
        self, project: int | str, merge_request_iid: int, *, user_prompt: str = ""
    ) -> MRReviewResult:
        if self._reviewer is None:
            raise ValueError(
                "OPENAI_API_KEY is required to review GitLab merge requests"
            )
        context = await asyncio.to_thread(
            self._load_review_context, project, merge_request_iid
        )
        prompt = self._prompt_builder.build(context, user_prompt=user_prompt)
        review_async = getattr(self._reviewer, "review_async", None)
        if review_async is not None:
            return await review_async(prompt)
        return await asyncio.to_thread(self._reviewer.review, prompt)

    def _load_review_context(
        self, project: int | str, merge_request_iid: int
    ) -> GitLabMRReviewContext:
        try:
            return self._service.load_review_context(project, merge_request_iid)
        except httpx.ConnectTimeout as exc:
            host = _request_host(exc)
            raise ValueError(
                f"Не могу подключиться к GitLab{host}: соединение истекло по timeout. "
                "Проверь VPN, доступность GITLAB_BASE_URL из терминала и корпоративную сеть."
            ) from exc
        except httpx.TimeoutException as exc:
            host = _request_host(exc)
            raise ValueError(
                f"GitLab{host} не ответил вовремя при загрузке MR context. "
                "Попробуй повторить позже или увеличить timeout клиента, если GitLab отвечает медленно."
            ) from exc
        except httpx.RequestError as exc:
            host = _request_host(exc)
            raise ValueError(f"Не удалось загрузить GitLab MR context{host}: {exc}") from exc

    def handle_text(self, text: str) -> str:
        return self.handle_prompt(text).text

    def plan_command(self, text: str, *, context: str = "") -> GitLabMRCommand:
        reference = parse_gitlab_mr_reference(text)
        if reference:
            project, merge_request_iid = reference
            return GitLabMRCommand(
                action="review",
                project=project,
                merge_request_iid=merge_request_iid,
            )
        return GitLabMRCommand(
            message=(
                "Укажи GitLab MR ссылкой вида "
                "https://gitlab.company.com/group/project/-/merge_requests/123 "
                "или текстом: gitlab mr group/project 123."
            )
        )

    def handle_prompt(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
    ) -> AgentResponse:
        command = self.plan_command(text, context=context)
        if command.action != "review" or not command.can_review:
            return AgentResponse(command.message)
        result = self.review_merge_request(
            command.project or "", command.merge_request_iid or 0, user_prompt=text
        )
        return _review_response(result)

    async def handle_prompt_stream(
        self,
        text: str,
        *,
        context: str = "",
        confirm: ConfirmCallback | None = None,
        on_text_delta: TextStreamCallback | None = None,
    ) -> AgentResponse:
        command = self.plan_command(text, context=context)
        if command.action != "review" or not command.can_review:
            return AgentResponse(command.message)
        result = await self.review_merge_request_async(
            command.project or "", command.merge_request_iid or 0, user_prompt=text
        )
        return _review_response(result)

    def context_summary(self) -> str:
        return ""

    def reset_context(self) -> None:
        return None


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


def _review_response(result: MRReviewResult) -> AgentResponse:
    return AgentResponse(
        format_mr_review_result(result),
        display=AgentDisplay(kind="gitlab_mr_review", payload=result),
    )


def parse_gitlab_mr_reference(text: str) -> tuple[str, int] | None:
    url_reference = _parse_gitlab_mr_url(text)
    if url_reference:
        return url_reference
    return _parse_gitlab_mr_text(text)


def _parse_gitlab_mr_url(text: str) -> tuple[str, int] | None:
    for raw_url in re.findall(r"https?://\S+", text):
        parsed = urlparse(raw_url.rstrip(".,;)"))
        parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        for index, part in enumerate(parts):
            if (
                part == "-"
                and index + 2 < len(parts)
                and parts[index + 1] == "merge_requests"
                and parts[index + 2].isdigit()
            ):
                project = "/".join(parts[:index])
                if project:
                    return project, int(parts[index + 2])
    return None


def _parse_gitlab_mr_text(text: str) -> tuple[str, int] | None:
    match = re.search(
        r"\b(?:gitlab\s+)?(?:mr|merge\s+request)\s+"
        r"(?P<project>[A-Za-z0-9_.~/%-]+)\s+!?(?P<iid>\d+)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return unquote(match.group("project")), int(match.group("iid"))


def _request_host(exc: httpx.RequestError) -> str:
    request = exc.request
    if request is None:
        return ""
    url = request.url
    return f" ({url.scheme}://{url.host})"
