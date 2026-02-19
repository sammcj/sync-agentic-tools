"""
Propagation logic for cross-tool file copying with transformations.
"""

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from .config import Config, PropagationRule
from .ui import show_error, show_info


def apply_sed_transform(content: str, pattern: str) -> str:
    """
    Apply sed-style regex transformation.

    Supports patterns like: 's/old/new/g', 's/old/new/', 's|old|new|g'
    """
    # Parse sed pattern
    if not pattern.startswith("s"):
        raise ValueError(f"Invalid sed pattern: {pattern}")

    # Find delimiter (usually /)
    delimiter = pattern[1]
    parts = pattern.split(delimiter)

    if len(parts) < 3:
        raise ValueError(f"Invalid sed pattern: {pattern}")

    search = parts[1]
    replace = parts[2]
    flags = parts[3] if len(parts) > 3 else ""

    # Apply regex replacement
    if "g" in flags:
        # Global replacement
        return re.sub(search, replace, content)
    else:
        # Single replacement
        return re.sub(search, replace, content, count=1)


def apply_remove_xml_sections_transform(content: str, sections: list[str]) -> str:
    """
    Remove specific sections from markdown-style content.

    Sections are identified by XML-style tags like <SECTION_NAME>...</SECTION_NAME>
    """
    result = content

    for section in sections:
        # Match section with both self-closing and paired tags
        # Pattern: <SECTION_NAME>...</SECTION_NAME> or <SECTION_NAME/>
        pattern = rf"<{section}[^>]*>.*?</{section}>|<{section}\s*/>"
        result = re.sub(pattern, "", result, flags=re.DOTALL)

    return result


def apply_remove_markdown_sections_transform(content: str, sections: list[str]) -> str:
    """
    Remove sections identified by markdown headings.

    Each section name is matched against heading text (e.g. "Sub-agent Coordination"
    matches "#### Sub-agent Coordination"). Removes from the heading through to just
    before the next heading at the same or higher level, or end of file.

    Headings inside fenced code blocks are ignored.
    """
    result = content

    for section in sections:
        heading_pattern = re.compile(
            rf"^(#{{1,6}})\s+{re.escape(section)}\s*$",
            re.MULTILINE,
        )

        match = heading_pattern.search(result)
        if not match:
            continue

        heading_level = len(match.group(1))
        section_start = match.start()

        # Find the next heading at the same or higher level, skipping code blocks
        next_heading_re = re.compile(
            rf"^#{{1,{heading_level}}}\s+\S",
            re.MULTILINE,
        )
        in_code_block = False
        section_end = len(result)
        for line_match in re.finditer(r"^.*$", result[match.end():], re.MULTILINE):
            line = line_match.group()
            if line.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue
            if next_heading_re.match(line):
                section_end = match.end() + line_match.start()
                break

        # Remove the section, normalising surrounding blank lines
        before = result[:section_start].rstrip("\n")
        after = result[section_end:]
        if before:
            result = before + "\n\n" + after.lstrip("\n")
        else:
            result = after.lstrip("\n")

    return result


def apply_transform(content: str, transform: dict[str, Any]) -> str:
    """Apply a single transformation to content."""
    transform_type = transform.get("type")

    if transform_type == "sed":
        pattern = transform.get("pattern")
        if not pattern:
            raise ValueError("sed transform requires 'pattern' parameter")
        return apply_sed_transform(content, pattern)

    elif transform_type == "remove_xml_sections":
        sections = transform.get("sections")
        if not sections:
            raise ValueError("remove_xml_sections transform requires 'sections' parameter")
        return apply_remove_xml_sections_transform(content, sections)

    elif transform_type == "remove_markdown_sections":
        sections = transform.get("sections")
        if not sections:
            raise ValueError("remove_markdown_sections transform requires 'sections' parameter")
        return apply_remove_markdown_sections_transform(content, sections)

    else:
        raise ValueError(f"Unknown transform type: {transform_type}")


def propagate_single_file(
    source_file: Path,
    target_base: Path,
    relative_path: Path,
    content: str,
    transforms: list[dict[str, Any]],
    dry_run: bool,
) -> None:
    """
    Propagate a single file with transformations.

    Args:
        source_file: Source file path
        target_base: Target base directory
        relative_path: Relative path for file
        content: File content
        transforms: List of transformations to apply
        dry_run: If True, don't actually write files
    """
    target_path = target_base / relative_path

    # Apply transformations
    transformed_content = content
    for transform in transforms:
        try:
            transformed_content = apply_transform(transformed_content, transform)
        except Exception as e:
            show_error(f"Failed to apply transform {transform.get('type')}: {e}")
            return

    # Check if target already has the same content
    needs_update = True
    if target_path.exists():
        try:
            with open(target_path, encoding="utf-8") as f:
                existing_content = f.read()
            if existing_content == transformed_content:
                needs_update = False
        except Exception:
            # If we can't read target, assume it needs update
            needs_update = True

    # Write to target only if changed
    if not needs_update:
        # Skip - already up to date
        pass
    elif dry_run:
        show_info(f"Would propagate: {source_file} → {target_path}")
    else:
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(transformed_content)
            show_info(f"Propagated: {source_file} → {target_path}")
        except Exception as e:
            show_error(f"Failed to write target file {target_path}: {e}")


def find_orphaned_files(
    source_path: Path,
    target_base: Path,
    exclude_patterns: list[str],
    propagated_files: set[Path],
) -> list[Path]:
    """
    Find files in target that don't exist in source (orphaned files).

    Args:
        source_path: Source directory
        target_base: Target directory
        exclude_patterns: Patterns to exclude from checking
        propagated_files: Set of files that were propagated

    Returns:
        List of orphaned file paths
    """
    if not target_base.exists():
        return []

    orphaned = []

    for target_file in target_base.rglob("*"):
        if not target_file.is_file():
            continue

        # Skip hidden files
        relative_path = target_file.relative_to(target_base)
        if any(part.startswith(".") for part in relative_path.parts):
            continue

        # Check if this file was propagated (should exist)
        if target_file not in propagated_files:
            # This file exists in target but wasn't propagated from source
            orphaned.append(target_file)

    return orphaned


def propagate_file(
    config: Config,
    rule: PropagationRule,
    dry_run: bool = False,
) -> None:
    """
    Propagate a file or directory from source to targets with transformations.

    Args:
        config: Configuration object
        rule: Propagation rule to apply
        dry_run: If True, don't actually write files
    """
    # Determine source path
    if rule.source_path:
        # Absolute path provided
        source_path = Path(rule.source_path).expanduser()
    elif rule.source_tool and rule.source_file:
        # Tool-relative path
        if rule.source_tool not in config.tools:
            raise ValueError(f"Source tool not found: {rule.source_tool}")

        source_tool = config.tools[rule.source_tool]
        # Tool-based propagation always uses target directories
        source_path = source_tool.target / rule.source_file
    else:
        raise ValueError(
            "Propagation rule must specify either source_path or (source_tool + source_file)"
        )

    if not source_path.exists():
        show_info(f"Skipping propagation: source does not exist: {source_path}")
        return

    # Check if source is a directory
    if source_path.is_dir():
        # Track propagated files per target for orphan detection
        target_propagated_files: dict[Path, set[Path]] = {}

        # Recursively propagate all files in directory
        for source_file in source_path.rglob("*"):
            if source_file.is_file():
                # Calculate relative path from source directory
                relative_path = source_file.relative_to(source_path)
                relative_path_str = str(relative_path)

                # Skip hidden files and files in hidden directories
                if any(part.startswith(".") for part in relative_path.parts):
                    continue

                # Check exclude patterns
                excluded = False
                for pattern in rule.exclude:
                    if fnmatch(relative_path_str, pattern) or fnmatch(source_file.name, pattern):
                        excluded = True
                        break

                if excluded:
                    continue

                # Read file content
                try:
                    with open(source_file, encoding="utf-8") as f:
                        content = f.read()
                except Exception as e:
                    show_error(f"Failed to read source file {source_file}: {e}")
                    continue

                # Propagate to each target
                for target in rule.targets:
                    # Determine target base path
                    if target.dest_path:
                        target_base = Path(target.dest_path).expanduser()
                    elif target.tool and target.target_file:
                        if target.tool not in config.tools:
                            show_error(f"Target tool not found: {target.tool}")
                            continue
                        target_tool = config.tools[target.tool]
                        target_base = target_tool.target / target.target_file
                    else:
                        show_error("Target must specify either dest_path or (tool + target_file)")
                        continue

                    # Track this target's propagated files
                    if target_base not in target_propagated_files:
                        target_propagated_files[target_base] = set()

                    target_file_path = target_base / relative_path
                    target_propagated_files[target_base].add(target_file_path)

                    propagate_single_file(
                        source_file, target_base, relative_path, content, target.transforms, dry_run
                    )

        # Check for orphaned files in each target
        if not dry_run:
            for target_base, propagated_files in target_propagated_files.items():
                orphaned = find_orphaned_files(source_path, target_base, rule.exclude, propagated_files)
                if orphaned:
                    from .ui import show_orphaned_file_action_prompt, show_orphaned_files_prompt

                    show_info("These files exist in target but not in source:")
                    for orphan in orphaned:
                        relative = orphan.relative_to(target_base)
                        show_info(f"  - {relative}")

                    action = show_orphaned_files_prompt(len(orphaned))

                    if action == "delete_all":
                        for orphan in orphaned:
                            try:
                                orphan.unlink()
                                relative = orphan.relative_to(target_base)
                                show_info(f"Deleted: {relative}")
                            except Exception as e:
                                show_error(f"Failed to delete {orphan}: {e}")

                    elif action == "sync_back_all":
                        for orphan in orphaned:
                            try:
                                # Calculate relative path and determine source destination
                                relative = orphan.relative_to(target_base)
                                source_dest = source_path / relative

                                # Create parent directories if needed
                                source_dest.parent.mkdir(parents=True, exist_ok=True)

                                # Copy file back to source
                                import shutil

                                shutil.copy2(orphan, source_dest)
                                show_info(f"Synced back: {relative}")
                            except Exception as e:
                                show_error(f"Failed to sync back {orphan}: {e}")

                    elif action == "select":
                        # Process each file individually
                        import os
                        import subprocess

                        for orphan in orphaned:
                            relative = orphan.relative_to(target_base)

                            # Keep prompting until user makes a decision (not "view")
                            while True:
                                file_action = show_orphaned_file_action_prompt(str(relative))

                                if file_action == "view":
                                    # Open file in editor
                                    editor = os.environ.get("EDITOR", "vi")
                                    try:
                                        subprocess.run([editor, str(orphan)], check=True)
                                        # After viewing, prompt again
                                        continue
                                    except Exception as e:
                                        show_error(f"Failed to open editor: {e}")
                                        # Continue to re-prompt
                                        continue

                                elif file_action == "delete":
                                    try:
                                        orphan.unlink()
                                        show_info(f"Deleted: {relative}")
                                    except Exception as e:
                                        show_error(f"Failed to delete {orphan}: {e}")
                                    break

                                elif file_action == "sync_back":
                                    try:
                                        source_dest = source_path / relative
                                        source_dest.parent.mkdir(parents=True, exist_ok=True)

                                        import shutil

                                        shutil.copy2(orphan, source_dest)
                                        show_info(f"Synced back: {relative}")
                                    except Exception as e:
                                        show_error(f"Failed to sync back {orphan}: {e}")
                                    break

                                else:  # skip
                                    break
                    # else: skip all
        return

    # Single file propagation
    try:
        with open(source_path, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        show_error(f"Failed to read source file {source_path}: {e}")
        return

    # Propagate to each target
    for target in rule.targets:
        # Determine target path (single file)
        if target.dest_path:
            target_path = Path(target.dest_path).expanduser()
        elif target.tool and target.target_file:
            if target.tool not in config.tools:
                show_error(f"Target tool not found: {target.tool}")
                continue
            target_tool = config.tools[target.tool]
            target_path = target_tool.target / target.target_file
        else:
            show_error("Target must specify either dest_path or (tool + target_file)")
            continue

        # Use target filename (not source filename)
        relative_path = Path(target_path.name)
        propagate_single_file(source_path, target_path.parent, relative_path, content, target.transforms, dry_run)


def run_propagation(config: Config, dry_run: bool = False) -> None:
    """
    Run all propagation rules in configuration.

    Args:
        config: Configuration object
        dry_run: If True, don't actually write files
    """
    if not config.propagate:
        return

    show_info("Running propagation rules...")

    for rule in config.propagate:
        try:
            propagate_file(config, rule, dry_run)
        except Exception as e:
            source_display = rule.source_path or f"{rule.source_tool}/{rule.source_file}"
            show_error(f"Propagation failed for {source_display}: {e}")
