"""Utility functions for agentic-sync."""

import fnmatch
import socket
import uuid
from pathlib import Path


def matches_pattern(path: Path, pattern: str, base_path: Path) -> bool:
    """
    Check if path matches glob pattern.

    Args:
        path: Path to check
        pattern: Glob pattern (supports * and **)
        base_path: Base path for relative pattern matching

    Returns:
        True if path matches pattern
    """
    # Make path relative to base_path for matching
    try:
        relative_path = path.relative_to(base_path)
    except ValueError:
        return False

    relative_str = str(relative_path)

    # Handle ** recursive glob
    if "**" in pattern:
        # Convert ** to match any number of path segments
        pattern_parts = pattern.split("/")
        path_parts = relative_str.split("/")

        return _matches_recursive_pattern(path_parts, pattern_parts)
    else:
        # Simple fnmatch
        return fnmatch.fnmatch(relative_str, pattern)


def _matches_recursive_pattern(path_parts: list[str], pattern_parts: list[str]) -> bool:
    """
    Match path against pattern with ** support.

    Args:
        path_parts: Path components
        pattern_parts: Pattern components

    Returns:
        True if matches
    """
    # If no pattern parts left, check if path is also done
    if not pattern_parts:
        return not path_parts

    # If no path parts left but pattern remains
    if not path_parts:
        # Only matches if remaining pattern is all **
        return all(p == "**" for p in pattern_parts)

    current_pattern = pattern_parts[0]

    if current_pattern == "**":
        # ** can match zero or more path segments
        # Try matching with rest of pattern at each possible position
        for i in range(len(path_parts) + 1):
            if _matches_recursive_pattern(path_parts[i:], pattern_parts[1:]):
                return True
        return False
    else:
        # Must match current path part
        if fnmatch.fnmatch(path_parts[0], current_pattern):
            return _matches_recursive_pattern(path_parts[1:], pattern_parts[1:])
        return False


def matches_patterns(
    relative_path: str,
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> bool:
    """
    Check if a relative path matches include/exclude patterns.

    Args:
        relative_path: Path relative to base (as string)
        include_patterns: Patterns to include (empty = include all)
        exclude_patterns: Patterns to exclude

    Returns:
        True if path would be included after applying patterns
    """
    # If no include patterns, everything is potentially included
    included = not include_patterns

    # Check include patterns
    if include_patterns:
        for pattern in include_patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                included = True
                break
            # Handle ** patterns
            if "**" in pattern:
                pattern_parts = pattern.split("/")
                path_parts = relative_path.split("/")
                if _matches_recursive_pattern(path_parts, pattern_parts):
                    included = True
                    break

    # Check exclude patterns
    if included:
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(relative_path, pattern):
                return False
            # Handle ** patterns
            if "**" in pattern:
                pattern_parts = pattern.split("/")
                path_parts = relative_path.split("/")
                if _matches_recursive_pattern(path_parts, pattern_parts):
                    return False

    return included


def find_files(
    base_path: Path,
    include_patterns: list[str],
    exclude_patterns: list[str],
    follow_symlinks: bool = False,
    respect_gitignore: bool = True,
) -> set[Path]:
    """
    Find files matching include/exclude patterns.

    Args:
        base_path: Base directory to search
        include_patterns: Patterns to include (empty = include all)
        exclude_patterns: Patterns to exclude
        follow_symlinks: Whether to follow symbolic links
        respect_gitignore: Whether to respect .gitignore files

    Returns:
        Set of matching file paths
    """
    if not base_path.exists():
        return set()

    # Combine exclude patterns with gitignore patterns if requested
    combined_excludes = list(exclude_patterns)
    if respect_gitignore:
        from .gitignore import get_gitignore_excludes

        gitignore_patterns = get_gitignore_excludes(base_path)
        combined_excludes.extend(gitignore_patterns)

    candidates = set()

    # If no include patterns, include everything
    if not include_patterns:
        for item in base_path.rglob("*"):
            if item.is_file():
                if not follow_symlinks and item.is_symlink():
                    continue
                candidates.add(item)
    else:
        # Process each include pattern
        for pattern in include_patterns:
            # Handle ** patterns with rglob
            if "**" in pattern:
                # Extract the glob part after **
                pattern_path = Path(pattern)
                parts = pattern_path.parts

                if parts[0] == "**":
                    # **/*.ext or **/dir/**
                    search_pattern = "/".join(parts[1:]) if len(parts) > 1 else "*"
                    for item in base_path.rglob(search_pattern):
                        if item.is_file():
                            if not follow_symlinks and item.is_symlink():
                                continue
                            candidates.add(item)
                else:
                    # dir/**/*.ext
                    subdir = base_path / parts[0]
                    if subdir.exists():
                        search_pattern = "/".join(parts[2:]) if len(parts) > 2 else "*"
                        for item in subdir.rglob(search_pattern):
                            if item.is_file():
                                if not follow_symlinks and item.is_symlink():
                                    continue
                                candidates.add(item)
            else:
                # Simple glob
                for item in base_path.glob(pattern):
                    if item.is_file():
                        if not follow_symlinks and item.is_symlink():
                            continue
                        candidates.add(item)

    # Apply exclusions
    result = set()
    for candidate in candidates:
        excluded = False
        relative_path = candidate.relative_to(base_path)

        for exclude_pattern in combined_excludes:
            # Check if the file itself matches the pattern
            if matches_pattern(candidate, exclude_pattern, base_path):
                excluded = True
                break

            # Also check if any parent directory matches the pattern
            # This ensures that patterns like "**/.git" exclude all files within .git directories
            for parent in relative_path.parents:
                if parent != Path("."):  # Skip the root "." parent
                    parent_path = base_path / parent
                    if matches_pattern(parent_path, exclude_pattern, base_path):
                        excluded = True
                        break

            if excluded:
                break

        if not excluded:
            result.add(candidate)

    return result


def get_machine_id() -> str:
    """
    Generate a unique machine identifier.

    Returns:
        Machine ID string (hostname + UUID)
    """
    hostname = socket.gethostname()
    # Use MAC address based UUID for consistency across runs on same machine
    mac = uuid.getnode()
    machine_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{hostname}-{mac}")
    return f"{hostname}-{machine_uuid.hex[:8]}"


def format_size(size_bytes: int) -> str:
    """
    Format file size in human-readable format.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 KB", "2.3 MB")
    """
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"
