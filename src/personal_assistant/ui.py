import builtins
from collections.abc import Callable
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from personal_assistant.agents.base import AgentDisplay
from personal_assistant.clients.jira import JiraIssue


class TerminalUI:
    def __init__(
        self,
        *,
        console: Console | None = None,
        input_func: Callable[[str], str] | None = None,
    ) -> None:
        self.console = console or Console()
        self._input = input_func

    def print_banner(self) -> None:
        self.console.print(
            Panel.fit(
                "[bold cyan]Assistant chat.[/bold cyan] /exit чтобы выйти, /clear чтобы очистить контекст.",
                title="[bold]Personal Assistant[/bold]",
                border_style="cyan",
            )
        )

    def print_error(self, message: str) -> None:
        self.console.print(f"[bold red]Ошибка:[/bold red] {message}")

    def print_success(self, message: str) -> None:
        self.console.print(f"[green]{message}[/green]")

    def print_assistant(self, message: str) -> None:
        self.console.print(Text(message))

    def print_assistant_delta(self, delta: str) -> None:
        self.console.print(Text(delta), end="")
        self.console.file.flush()

    def finish_assistant_stream(self) -> None:
        self.console.print()

    def print_display(self, display: AgentDisplay) -> None:
        if display.kind == "jira_issues":
            self.print_issues_table(display.payload)
            return
        if display.kind == "gitlab_mr_review":
            self.print_gitlab_mr_review(display.payload)
            return
        self.print_assistant(str(display.payload))

    def print_issues_table(self, issues: list[JiraIssue]) -> None:
        if not issues:
            self.console.print(
                "[yellow]Jira не вернула задач по текущему запросу.[/yellow]"
            )
            return

        table = Table(title="Задачи из Jira", title_style="bold cyan", show_lines=False)
        table.add_column("Key", style="bold magenta", no_wrap=True)
        table.add_column("Summary", style="white")
        table.add_column("Status", style="cyan", no_wrap=True)
        table.add_column("Priority", style="yellow", no_wrap=True)
        table.add_column("Assignee", style="green")

        for issue in issues:
            table.add_row(
                issue.key,
                issue.summary,
                issue.status,
                issue.priority or "-",
                issue.assignee or "-",
            )

        self.console.print(table)

    def print_gitlab_mr_review(self, result: Any) -> None:
        self.console.print(_build_gitlab_mr_review_renderable(result))

    def confirm(self, preview: str) -> bool:
        self.console.print(
            Panel(
                Text(preview),
                title="[bold yellow]Confirm Change[/bold yellow]",
                border_style="yellow",
            )
        )
        input_func = self._input or builtins.input
        answer = input_func("Apply? [y/N] ").strip().lower()
        return answer in {"y", "yes", "д", "да"}


def _build_gitlab_mr_review_renderable(result: Any) -> Group:
    recommendation = str(result.recommendation.value)
    recommendation_style = _recommendation_style(recommendation)

    overview = Table.grid(padding=(0, 2))
    overview.expand = True
    overview.add_column(style="bold cyan", no_wrap=True)
    overview.add_column(style="white")
    overview.add_row("Summary", str(result.summary))
    overview.add_row("Risk", str(result.risk_assessment))
    overview.add_row("Recommendation", Text(recommendation, style=recommendation_style))

    parts: list[Any] = [
        Panel(
            overview,
            title="[bold cyan]GitLab MR Review[/bold cyan]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    ]

    comments = list(result.comments)
    if not comments:
        parts.append(
            Panel(
                Text("No major issues.", style="green"),
                title="[bold green]Comments[/bold green]",
                border_style="green",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )
        return Group(*parts)

    for index, comment in enumerate(comments, start=1):
        parts.append(_build_gitlab_mr_review_comment(index, comment))
    return Group(*parts)


def _build_gitlab_mr_review_comment(index: int, comment: Any) -> Panel:
    severity = str(comment.severity.value)
    severity_style = _severity_style(severity)
    location = str(comment.file_path)
    if comment.line:
        location = f"{location}:{comment.line}"

    body = Table.grid(padding=(0, 1))
    body.expand = True
    body.add_column(style="bold cyan", no_wrap=True)
    body.add_column(style="white")
    body.add_row("Where", Text(location, style="magenta"))
    body.add_row("Issue", str(comment.message))
    if comment.reason:
        body.add_row("Reason", str(comment.reason))
    if comment.suggested_change:
        body.add_row("Suggested", str(comment.suggested_change))

    title = Text.assemble(
        (f"#{index} ", "bold white"),
        (severity, f"bold {severity_style}"),
    )
    return Panel(
        body,
        title=title,
        border_style=severity_style,
        box=box.ROUNDED,
        padding=(1, 2),
    )


def _severity_style(severity: str) -> str:
    return {
        "blocking": "red",
        "important": "yellow",
        "suggestion": "blue",
        "praise": "green",
    }.get(severity, "white")


def _recommendation_style(recommendation: str) -> str:
    return {
        "approve": "green",
        "approve_with_suggestions": "yellow",
        "request_changes": "red",
    }.get(recommendation, "white")
