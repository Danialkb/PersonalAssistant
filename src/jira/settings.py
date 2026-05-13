from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    JIRA_BASE_URL: str = Field(
        ...,
        validation_alias=AliasChoices("JIRA_BASE_URL", "JIRA_URL", "JIRA_HOST"),
        description="Atlassian site URL, for example https://company.atlassian.net",
    )
    JIRA_EMAIL: str | None = Field(
        default=None,
        validation_alias=AliasChoices("JIRA_EMAIL", "JIRA_USER", "JIRA_USERNAME"),
        description="Jira account email for Cloud API token auth",
    )
    JIRA_API_KEY: str = Field(
        ...,
        validation_alias=AliasChoices("JIRA_API_KEY", "JIRA_API_TOKEN", "JIRA_TOKEN"),
        description="Jira API token or personal access token",
    )
    JIRA_AUTH_MODE: str = Field(
        default="auto",
        validation_alias=AliasChoices("JIRA_AUTH_MODE"),
        description="auto, basic, or bearer",
    )
    JIRA_ASSIGNEE: str | None = Field(
        default=None,
        validation_alias=AliasChoices("JIRA_ASSIGNEE", "JIRA_ACCOUNT_ID", "JIRA_ASSIGNEE_ACCOUNT_ID"),
        description="Jira assignee value for the default task filter. Defaults to currentUser().",
    )
    JIRA_DEFAULT_JQL: str | None = Field(
        default=None,
        validation_alias=AliasChoices("JIRA_DEFAULT_JQL"),
        description="Full default Jira JQL. Overrides JIRA_ASSIGNEE when provided.",
    )
    JIRA_PROJECT_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("JIRA_PROJECT_KEY", "JIRA_PROJECT"),
        description="Default Jira project key used to resolve bare issue numbers, for example CCO.",
    )

    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4.1-mini"

    @property
    def default_jira_jql(self) -> str:
        if self.JIRA_DEFAULT_JQL:
            return self.JIRA_DEFAULT_JQL

        assignee = self.JIRA_ASSIGNEE.strip() if self.JIRA_ASSIGNEE else "currentUser()"
        if assignee.endswith("()"):
            assignee_value = assignee
        else:
            assignee_value = f'"{assignee.replace(chr(34), chr(92) + chr(34))}"'
        return f"assignee = {assignee_value} ORDER BY updated DESC"


@lru_cache
def get_settings() -> Settings:
    return Settings()
