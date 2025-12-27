"""Gitignore file parsing for agentic-sync."""

from pathlib import Path


def parse_gitignore(gitignore_path: Path, add_global_prefix: bool = True) -> list[str]:
    """
    Parse a .gitignore file and return a list of exclude patterns.

    Args:
        gitignore_path: Path to .gitignore file
        add_global_prefix: If True, add **/ prefix for global matching (root gitignore).
                          If False, return patterns as-is for directory-scoped matching.

    Returns:
        List of glob patterns to exclude
    """
    if not gitignore_path.exists():
        return []

    patterns = []

    try:
        with open(gitignore_path, encoding="utf-8") as f:
            for line in f:
                # Strip whitespace
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                # Skip negation patterns (we don't support un-ignoring for now)
                if line.startswith("!"):
                    continue

                # Convert gitignore pattern to glob pattern
                pattern = _gitignore_to_glob(line, add_global_prefix)
                if pattern:
                    patterns.append(pattern)

    except (OSError, UnicodeDecodeError):
        # Ignore files we can't read
        pass

    return patterns


def _gitignore_to_glob(pattern: str, add_global_prefix: bool = True) -> str:
    """
    Convert a gitignore pattern to a glob pattern.

    Args:
        pattern: Gitignore pattern
        add_global_prefix: If True, add **/ prefix for patterns that should match
                          at any directory level. If False, return patterns without
                          the global prefix (for directory-scoped matching).

    Returns:
        Glob pattern compatible with our pattern matching
    """
    # Track if pattern was root-relative (started with /)
    is_root_relative = pattern.startswith("/")

    # Remove leading slash if present
    if is_root_relative:
        pattern = pattern.lstrip("/")

    # Handle trailing slash (directory only)
    # For simplicity, we'll just remove it - our glob matches files, not dirs
    is_directory = pattern.endswith("/")
    if is_directory:
        pattern = pattern.rstrip("/")
        # Match everything inside the directory
        if is_root_relative or not add_global_prefix:
            return f"{pattern}/**"
        else:
            return f"**/{pattern}/**"

    # If root-relative, don't add ** prefix
    if is_root_relative:
        return pattern

    # If not adding global prefix, return as-is (will be prefixed with directory later)
    if not add_global_prefix:
        return pattern

    # Handle patterns with directory separators
    if "/" in pattern:
        # Pattern is relative to repository root or specific directory
        # Match it anywhere in the tree
        return f"**/{pattern}"

    # Simple pattern - match anywhere
    # e.g., "*.log" should match at any level
    return f"**/{pattern}"


def collect_gitignore_patterns(base_path: Path, respect_nested: bool = True) -> list[str]:
    """
    Collect gitignore patterns from .gitignore files in directory tree.

    Args:
        base_path: Base directory to search
        respect_nested: If True, also read .gitignore files in subdirectories

    Returns:
        List of all exclude patterns from gitignore files
    """
    patterns = []

    # Read root .gitignore - these patterns apply globally (with **/ prefix)
    root_gitignore = base_path / ".gitignore"
    if root_gitignore.exists():
        patterns.extend(parse_gitignore(root_gitignore, add_global_prefix=True))

    # Read nested .gitignore files if requested
    if respect_nested and base_path.is_dir():
        for gitignore_path in base_path.rglob(".gitignore"):
            # Skip the root one we already processed
            if gitignore_path == root_gitignore:
                continue

            # Parse patterns WITHOUT global prefix - we'll scope them to the directory
            nested_patterns = parse_gitignore(gitignore_path, add_global_prefix=False)

            # Make patterns relative to the base_path
            # (gitignore patterns are relative to their containing directory)
            gitignore_dir = gitignore_path.parent
            try:
                rel_dir = gitignore_dir.relative_to(base_path)
                # Prefix patterns with the relative directory to scope them
                for pattern in nested_patterns:
                    # For patterns that contain **, they're meant to match recursively
                    # within the subdirectory, so prefix with the directory
                    if "**" in pattern:
                        patterns.append(f"{rel_dir}/{pattern}")
                    else:
                        # Simple patterns like "settings.json" or "*.log" should
                        # match recursively within the subdirectory
                        patterns.append(f"{rel_dir}/**/{pattern}")
            except ValueError:
                # gitignore is not under base_path, skip it
                continue

    return patterns


def get_gitignore_excludes(base_path: Path) -> list[str]:
    """
    Get list of exclude patterns from .gitignore files in a directory.

    This is the main entry point for gitignore support.

    Args:
        base_path: Base directory to search

    Returns:
        List of glob patterns to exclude
    """
    return collect_gitignore_patterns(base_path, respect_nested=True)
