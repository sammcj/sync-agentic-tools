"""Diff generation and display for agentic-sync."""

import difflib
from dataclasses import dataclass
from pathlib import Path

from .files import read_file_lines


@dataclass
class DiffStats:
    """Statistics about a diff."""

    additions: int
    deletions: int
    total_changes: int

    @property
    def change_summary(self) -> str:
        """Get human-readable change summary."""
        if self.additions == 0 and self.deletions == 0:
            return "no changes"
        return f"+{self.additions} -{self.deletions}"


def generate_unified_diff(
    file1: Path, file2: Path, context_lines: int = 3
) -> tuple[list[str], DiffStats]:
    """
    Generate unified diff between two files.

    Args:
        file1: First file path
        file2: Second file path
        context_lines: Number of context lines to show

    Returns:
        Tuple of (diff lines, diff stats)
    """
    lines1 = read_file_lines(file1)
    lines2 = read_file_lines(file2)

    # Generate unified diff
    diff = list(
        difflib.unified_diff(
            lines1,
            lines2,
            fromfile=str(file1),
            tofile=str(file2),
            lineterm="",
            n=context_lines,
        )
    )

    # Calculate stats
    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    stats = DiffStats(additions=additions, deletions=deletions, total_changes=additions + deletions)

    return diff, stats


def generate_diff_between_strings(
    text1: str, text2: str, name1: str = "original", name2: str = "modified"
) -> tuple[list[str], DiffStats]:
    """
    Generate unified diff between two strings.

    Args:
        text1: First text
        text2: Second text
        name1: Name for first version
        name2: Name for second version

    Returns:
        Tuple of (diff lines, diff stats)
    """
    lines1 = text1.splitlines(keepends=True)
    lines2 = text2.splitlines(keepends=True)

    diff = list(difflib.unified_diff(lines1, lines2, fromfile=name1, tofile=name2, lineterm=""))

    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    stats = DiffStats(additions=additions, deletions=deletions, total_changes=additions + deletions)

    return diff, stats


def count_diff_lines(file1: Path, file2: Path) -> DiffStats:
    """
    Count additions/deletions without generating full diff.

    Args:
        file1: First file path
        file2: Second file path

    Returns:
        DiffStats object
    """
    _, stats = generate_unified_diff(file1, file2)
    return stats


def count_diff_lines_from_strings(
    text1: str, text2: str, name1: str = "original", name2: str = "modified"
) -> DiffStats:
    """Count additions/deletions between two strings without generating full diff."""
    _, stats = generate_diff_between_strings(text1, text2, name1, name2)
    return stats
