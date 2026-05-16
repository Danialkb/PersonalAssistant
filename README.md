# PersonalAssistant

Text-first CLI assistant with Jira integration.

## Extension points

The code is split so new integrations can be added without changing Jira internals:

- `src/personal_assistant/clients/base.py` contains `HttpApiClient`, the shared HTTP transport base for API clients.
- `src/personal_assistant/clients/jira.py` contains Jira-specific endpoints and payload parsing.
- `src/personal_assistant/clients/gitlab.py` contains GitLab-specific endpoints and payload parsing.
- `src/personal_assistant/tools/jira.py` contains Jira tool functions used by agents and the CLI executor.
- `src/personal_assistant/assistant.py` contains `AssistantAgent`, a small orchestrator that delegates to domain agents.
- `src/personal_assistant/agents/jira.py` contains Jira prompts, tools, command schema, and local fallback planning.

GitLab client settings are read from `GITLAB_BASE_URL` and `GITLAB_TOKEN`. `GITLAB_BASE_URL` may point either to
the GitLab host root or directly to `/api/v4`.

The GitLab MR reviewer is built around `GitLabMRReviewAgent`. It loads MR metadata and diffs through
`GitLabMRService`, builds a focused review prompt through `MRReviewPromptBuilder`, and returns a structured
`MRReviewResult` without posting comments to GitLab.

Example:

```python
from personal_assistant.agents.gitlab import GitLabMRReviewAgent, format_mr_review_result
from personal_assistant.assistant import AssistantAgent
from personal_assistant.settings import get_settings

settings = get_settings()
model = AssistantAgent._build_model(settings)
agent = GitLabMRReviewAgent.from_settings(settings, model=model)
result = agent.review_merge_request("team/project", 12)
print(format_mr_review_result(result))
```
