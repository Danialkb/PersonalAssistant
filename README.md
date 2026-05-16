# PersonalAssistant

Text-first CLI assistant with Jira integration.

## Extension points

The code is split so new integrations can be added without changing Jira internals:

- `src/personal_assistant/clients/base.py` contains `HttpApiClient`, the shared HTTP transport base for API clients.
- `src/personal_assistant/clients/jira.py` contains Jira-specific endpoints and payload parsing.
- `src/personal_assistant/tools/jira.py` contains Jira tool functions used by agents and the CLI executor.
- `src/personal_assistant/assistant.py` contains `AssistantAgent`, a small orchestrator that delegates to domain agents.
- `src/personal_assistant/agents/jira.py` contains Jira prompts, tools, command schema, and local fallback planning.

To add GitLab later, create a `GitlabClient(HttpApiClient)` with GitLab auth/endpoint parsing, then create a
`GitlabAgent` with `name = "gitlab"` and the same `handle_text` / `plan_command` interface used by `JiraAgent`.
Register it through `AssistantAgent(settings, agents=[JiraAgent(...), GitlabAgent(...)], default_agent="gitlab")`
or add it to `_build_default_agents`.
