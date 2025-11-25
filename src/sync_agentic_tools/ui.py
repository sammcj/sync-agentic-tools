"""UI components for agentic-sync using rich."""

from enum import Enum

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table

from .diff import DiffStats

console = Console()


class ChangeType(Enum):
    """Type of file change."""

    MODIFIED = "modified"
    NEW = "new"
    DELETED = "deleted"
    CONFLICT = "conflict"
    ORPHANED = "orphaned"


class FileChange:
    """Represents a file change for UI display."""

    def __init__(
        self,
        relative_path: str,
        change_type: ChangeType,
        diff_stats: DiffStats | None = None,
        warnings: list[str] | None = None,
    ):
        self.relative_path = relative_path
        self.change_type = change_type
        self.diff_stats = diff_stats
        self.warnings = warnings or []


def show_summary(
    changes: list[FileChange],
    tool_name: str,
    direction: str,
    source_path: str | None = None,
    target_path: str | None = None,
) -> None:
    """
    Display summary of changes.

    Args:
        changes: List of file changes
        tool_name: Name of tool being synced
        direction: Direction of sync (e.g., "source → target")
        source_path: Optional source directory path
        target_path: Optional target directory path
    """
    if not changes:
        console.print(f"[green]✓ No changes to sync for {tool_name}[/green]")
        return

    # Create table
    title = f"Changes for {tool_name} ({direction})"
    table = Table(title=title, show_header=True)
    table.add_column("File", style="cyan", no_wrap=False)
    table.add_column("Type", style="magenta")
    table.add_column("Changes", style="yellow", justify="right")

    # Add source/target paths as first row if provided
    if source_path and target_path:
        table.add_row(f"[bold dim]Source:[/bold dim] {source_path}", "", "")
        table.add_row(f"[bold dim]Target:[/bold dim] {target_path}", "", "")
        table.add_section()

    # Categorise changes
    modified = [c for c in changes if c.change_type == ChangeType.MODIFIED]
    new = [c for c in changes if c.change_type == ChangeType.NEW]
    deleted = [c for c in changes if c.change_type == ChangeType.DELETED]
    conflicts = [c for c in changes if c.change_type == ChangeType.CONFLICT]
    orphaned = [c for c in changes if c.change_type == ChangeType.ORPHANED]

    # Add modified files
    if modified:
        table.add_section()
        table.add_row("[bold]Modified Files[/bold]", "", "")
        for i, change in enumerate(modified, 1):
            stats_str = change.diff_stats.change_summary if change.diff_stats else "unknown"
            warning_marker = " ⚠" if change.warnings else ""
            table.add_row(f"[{i}] {change.relative_path}{warning_marker}", "modified", stats_str)

    # Add new files
    if new:
        table.add_section()
        table.add_row("[bold]New Files[/bold]", "", "")
        for i, change in enumerate(new, len(modified) + 1):
            warning_marker = " ⚠" if change.warnings else ""
            table.add_row(f"[{i}] {change.relative_path}{warning_marker}", "new", "(new)")

    # Add deleted files
    if deleted:
        table.add_section()
        table.add_row("[bold]Deleted Files[/bold]", "", "")
        for i, change in enumerate(deleted, len(modified) + len(new) + 1):
            table.add_row(f"[{i}] {change.relative_path}", "deleted", "(deleted)")

    # Add conflicts
    if conflicts:
        table.add_section()
        table.add_row("[bold red]Conflicts[/bold red]", "", "")
        for i, change in enumerate(conflicts, len(modified) + len(new) + len(deleted) + 1):
            table.add_row(f"[{i}] {change.relative_path}", "[red]conflict[/red]", "")

    # Add orphaned files
    if orphaned:
        table.add_section()
        table.add_row("[bold yellow]Orphaned Files[/bold yellow]", "", "")
        for i, change in enumerate(orphaned, len(modified) + len(new) + len(deleted) + len(conflicts) + 1):
            table.add_row(f"[{i}] {change.relative_path}", "[yellow]orphaned[/yellow]", "(not in source)")

    console.print(table)

    # Show warnings summary
    total_warnings = sum(len(c.warnings) for c in changes)
    if total_warnings > 0:
        console.print(f"\n[yellow]⚠ {total_warnings} warning(s) detected[/yellow]")


def show_diff(file_path: str, diff_lines: list[str], file1_info: str, file2_info: str) -> None:
    """
    Display a diff with syntax highlighting.

    Args:
        file_path: Relative path to file
        diff_lines: Lines of unified diff
        file1_info: Info about first file (e.g., timestamp)
        file2_info: Info about second file
    """
    diff_text = "\n".join(diff_lines)

    # Create syntax-highlighted diff
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)

    panel = Panel(
        syntax,
        title=f"[bold]Diff: {file_path}[/bold]",
        subtitle=f"{file1_info} ↔ {file2_info}",
        border_style="cyan",
    )

    console.print(panel)


def show_commands() -> None:
    """Display available commands."""
    commands_table = Table(show_header=False, box=None, padding=(0, 2))
    commands_table.add_column("Command", style="bold green")
    commands_table.add_column("Description")

    commands_table.add_row("[1-N]", "View diff for file number")
    commands_table.add_row("[a]", "View all diffs")
    commands_table.add_row("[y]", "Proceed with sync")
    commands_table.add_row("[n]", "Cancel")
    commands_table.add_row("[q]", "Quit")

    console.print("\n")
    console.print(commands_table)


def prompt_user_choice(prompt_text: str, choices: list[str]) -> str:
    """
    Prompt user to select from choices.

    Args:
        prompt_text: Prompt text to display
        choices: List of valid choices

    Returns:
        User's choice (lowercase)
    """
    while True:
        response = Prompt.ask(prompt_text, choices=choices).lower()
        if response in choices:
            return response


def confirm_action(message: str, default: bool = False) -> bool:
    """
    Ask user for confirmation.

    Args:
        message: Confirmation message
        default: Default value

    Returns:
        True if user confirms
    """
    return Confirm.ask(message, default=default)


def show_error(message: str) -> None:
    """
    Display an error message.

    Args:
        message: Error message
    """
    console.print(f"[bold red]ERROR:[/bold red] {message}")


def show_warning(message: str) -> None:
    """
    Display a warning message.

    Args:
        message: Warning message
    """
    console.print(f"[bold yellow]WARNING:[/bold yellow] {message}")


def show_success(message: str) -> None:
    """
    Display a success message.

    Args:
        message: Success message
    """
    console.print(f"[bold green]✓[/bold green] {message}")


def show_info(message: str) -> None:
    """
    Display an info message.

    Args:
        message: Info message
    """
    console.print(f"[blue]ℹ[/blue] {message}")


def show_conflict_resolution_prompt(file_path: str, source_info: str, target_info: str) -> str:
    """
    Show conflict resolution prompt.

    Args:
        file_path: Path to conflicting file
        source_info: Info about source version
        target_info: Info about target version

    Returns:
        User choice: "keep_source", "use_target", "diff", "skip"
    """
    console.print(f"\n[bold red]CONFLICT:[/bold red] {file_path}")
    console.print(f"  Source: {source_info}")
    console.print(f"  Target: {target_info}")
    console.print()

    choices = ["k", "u", "d", "s", "a"]
    choice = prompt_user_choice(
        "[K]eep source / [U]se target / [D]iff / [S]kip / [A]uto (newer wins)",
        choices,
    )

    mapping = {
        "k": "keep_source",
        "u": "use_target",
        "d": "diff",
        "s": "skip",
        "a": "auto",
    }

    return mapping[choice]


def show_deletion_prompt(file_path: str, source: str, dest: str) -> str:
    """
    Show deletion confirmation prompt.

    Args:
        file_path: Path to deleted file
        source: Source location name
        dest: Destination location name

    Returns:
        User choice: "delete", "skip", "sync_back"
    """
    console.print(f"\n[bold yellow]DELETION from {source}:[/bold yellow] {file_path}")
    console.print(f"  File no longer exists in {source}")
    console.print(f"  Still exists in {dest}")
    console.print()

    choices = ["d", "s", "v", "k"]
    choice = prompt_user_choice(
        f"[D]elete from {dest} / [S]ync back to {source} / [V]iew / S[k]ip",
        choices,
    )

    mapping = {"d": "delete", "s": "sync_back", "v": "view", "k": "skip"}

    return mapping[choice]


def show_rename_prompt(old_path: str, new_path: str, dest: str) -> str:
    """
    Show rename confirmation prompt.

    Args:
        old_path: Old file path
        new_path: New file path
        dest: Destination location name

    Returns:
        User choice: "rename", "separate"
    """
    console.print("\n[bold cyan]RENAME DETECTED:[/bold cyan]")
    console.print(f"  {old_path} → {new_path}")
    console.print(f"  Rename in {dest}?")
    console.print()

    choices = ["y", "n"]
    choice = prompt_user_choice("[Y]es, rename / [N]o, treat as separate", choices)

    mapping = {"y": "rename", "n": "separate"}

    return mapping[choice]


def show_reverse_sync_prompt(file_path: str, source_info: str, target_info: str) -> str:
    """
    Show reverse sync suggestion prompt when target is newer.

    Args:
        file_path: Path to file
        source_info: Info about source version (timestamp)
        target_info: Info about target version (timestamp)

    Returns:
        User choice: "pull", "push_anyway", "diff", "skip"
    """
    console.print(f"\n[bold yellow]TARGET NEWER:[/bold yellow] {file_path}")
    console.print(f"  Source: {source_info}")
    console.print(f"  Target: {target_info} (newer)")
    console.print("\n[yellow]The target file is newer than the source.[/yellow]")
    console.print("[yellow]Consider pulling from target instead of pushing.[/yellow]")
    console.print()

    choices = ["p", "w", "d", "s"]
    choice = prompt_user_choice(
        "[P]ull from target / Push any[W]ay / [D]iff / [S]kip",
        choices,
    )

    mapping = {
        "p": "pull",
        "w": "push_anyway",
        "d": "diff",
        "s": "skip",
    }

    return mapping[choice]


def show_orphaned_files_prompt(orphan_count: int) -> str:
    """
    Show prompt for handling orphaned files.

    Args:
        orphan_count: Number of orphaned files found

    Returns:
        User choice: "delete_all", "sync_back_all", "select", "skip"
    """
    console.print(f"\n[bold yellow]Found {orphan_count} orphaned file(s)[/bold yellow]")
    console.print("[yellow]These files exist in target but not in source.[/yellow]")
    console.print()

    choices = ["d", "s", "i", "k"]
    choice = prompt_user_choice(
        "[D]elete all / [S]ync all back / Select \\[i]ndividually / S\\[k]ip",
        choices,
    )

    mapping = {
        "d": "delete_all",
        "s": "sync_back_all",
        "i": "select",
        "k": "skip",
    }

    return mapping[choice]


def show_orphaned_file_action_prompt(file_path: str) -> str:
    """
    Show prompt for handling a single orphaned file.

    Args:
        file_path: Relative path to the orphaned file

    Returns:
        User choice: "delete", "sync_back", "skip", "view"
    """
    console.print(f"\n[yellow]{file_path}[/yellow]")

    choices = ["d", "s", "v", "k"]
    choice = prompt_user_choice(
        "[D]elete / [S]ync back / \\[V]iew in editor / S\\[k]ip",
        choices,
    )

    mapping = {
        "d": "delete",
        "s": "sync_back",
        "v": "view",
        "k": "skip",
    }

    return mapping[choice]
