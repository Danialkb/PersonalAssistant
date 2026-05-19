import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from personal_assistant.settings import Settings
from personal_assistant.tools.jira import (
    find_jira_transition,
    format_jira_transitions,
    get_jira_transitions,
    resolve_jira_issue_key,
    transition_jira_issue,
)


JIRA_ISSUE_KEY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
ConfirmCallback = Callable[[str], bool]


@dataclass(frozen=True)
class JiraCodeReviewUpdateResult:
    issue_key: str | None
    message: str
    changed: bool = False


class JiraCodeReviewUpdater:
    def __init__(
        self, settings: Settings, *, target_transition: str = "Code Review"
    ) -> None:
        self._settings = settings
        self._target_transition = target_transition

    def update_from_texts(
        self, texts: list[str], *, confirm: ConfirmCallback
    ) -> JiraCodeReviewUpdateResult:
        issue_key = extract_jira_issue_key(*texts)
        if not issue_key:
            return JiraCodeReviewUpdateResult(
                issue_key=None,
                message="Jira: ключ задачи не найден, review выполнен без обновления Jira.",
            )
        return self.move_to_code_review(issue_key, confirm=confirm)

    def move_to_code_review(
        self, issue_key: str, *, confirm: ConfirmCallback
    ) -> JiraCodeReviewUpdateResult:
        resolved_issue_key = resolve_jira_issue_key(self._settings, issue_key)
        try:
            transitions = get_jira_transitions(
                self._settings, issue_key=resolved_issue_key
            )
            transition = find_jira_transition(transitions, self._target_transition)
            if not transition:
                return JiraCodeReviewUpdateResult(
                    issue_key=resolved_issue_key,
                    message=(
                        f"Jira: не могу изменить статус {resolved_issue_key} "
                        f"на {self._target_transition!r}: transition/status недоступен.\n"
                        f"{format_jira_transitions(transitions)}"
                    ),
                )

            preview = self._preview_transition(resolved_issue_key, transition)
            if not confirm(preview):
                return JiraCodeReviewUpdateResult(
                    issue_key=resolved_issue_key,
                    message="Jira: изменение отменено, review выполнен без обновления Jira.",
                )

            message = transition_jira_issue(
                self._settings,
                issue_key=resolved_issue_key,
                transition_name=transition.name,
            )
            return JiraCodeReviewUpdateResult(
                issue_key=resolved_issue_key,
                message=f"Jira: {message}",
                changed=True,
            )
        except Exception as exc:
            return JiraCodeReviewUpdateResult(
                issue_key=resolved_issue_key,
                message=(
                    f"Jira: не удалось перевести {resolved_issue_key} "
                    f"в {self._target_transition}: {exc}"
                ),
            )

    @staticmethod
    def _preview_transition(issue_key: str, transition: Any) -> str:
        if transition.target_status and transition.target_status != transition.name:
            return f"Изменить статус {issue_key}: {transition.name} -> {transition.target_status}"
        return f"Изменить статус {issue_key}: {transition.name}"


def extract_jira_issue_key(*texts: str) -> str | None:
    for text in texts:
        match = JIRA_ISSUE_KEY_PATTERN.search(text.upper())
        if match:
            return match.group(0)
    return None
