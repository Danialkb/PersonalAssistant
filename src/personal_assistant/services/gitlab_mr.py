from dataclasses import dataclass

from personal_assistant.clients.gitlab import (
    GitlabClient,
    GitlabMergeRequest,
    GitlabMergeRequestChange,
    GitlabProject,
)
from personal_assistant.settings import Settings


@dataclass(frozen=True)
class GitLabMRReviewContext:
    project: GitlabProject
    merge_request: GitlabMergeRequest
    changes: tuple[GitlabMergeRequestChange, ...]

    @property
    def changed_file_paths(self) -> tuple[str, ...]:
        return tuple(change.file_path for change in self.changes)


class GitLabMRService:
    def __init__(self, client: GitlabClient) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "GitLabMRService":
        return cls(GitlabClient(settings))

    def load_review_context(
        self, project: int | str, merge_request_iid: int
    ) -> GitLabMRReviewContext:
        return GitLabMRReviewContext(
            project=self._client.get_project(project),
            merge_request=self._client.get_merge_request(project, merge_request_iid),
            changes=tuple(
                self._client.get_merge_request_changes(project, merge_request_iid)
            ),
        )
