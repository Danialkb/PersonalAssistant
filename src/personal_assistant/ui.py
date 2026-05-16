import builtins
from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
                "[bold cyan]Jira chat.[/bold cyan] /exit чтобы выйти, /clear чтобы очистить контекст.",
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

    def confirm(self, preview: str) -> bool:
        self.console.print(
            Panel(
                Text(preview),
                title="[bold yellow]Confirm Jira Change[/bold yellow]",
                border_style="yellow",
            )
        )
        input_func = self._input or builtins.input
        answer = input_func("Apply? [y/N] ").strip().lower()
        return answer in {"y", "yes", "д", "да"}
