"""Core sync logic for agentic-sync."""

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .backup import BackupManager
from .config import Config, ToolConfig
from .diff import count_diff_lines, count_diff_lines_from_strings, generate_diff_between_strings, generate_unified_diff
from .files import FileMetadata, files_are_identical, safe_copy_file
from .special_files import extract_json_keys, process_special_file
from .state import StateManager, SyncState
from .ui import (
    ChangeType,
    FileChange,
    show_error,
    show_info,
    show_success,
    show_summary,
    show_warning,
)
from .utils import find_files


class SyncDirection(Enum):
    """Direction of sync operation."""

    PUSH = "push"  # source → target
    PULL = "pull"  # target → source
    SYNC = "sync"  # bidirectional


@dataclass
class SyncPlan:
    """Plan for sync operations."""

    tool: ToolConfig
    direction: SyncDirection
    files_to_copy: list[tuple[Path, Path]]  # (source, dest)
    files_to_delete: list[tuple[Path, str]]  # (path, location)
    conflicts: list[tuple[Path, Path]]  # (source, target)
    reverse_suggestions: list[tuple[Path, Path]]  # (source, target) where target is newer
    orphaned_files: list[Path]  # Files in target with no source and no state
    confirmed_deletions: set[Path]  # Files already confirmed for deletion (skip re-prompting)


class SyncEngine:
    """Main sync engine."""

    def __init__(self, config: Config, dry_run: bool = False):
        """
        Initialise sync engine.

        Args:
            config: Configuration object
            dry_run: If True, don't make actual changes
        """
        self.config = config
        self.dry_run = dry_run
        self.backup_manager = BackupManager()

    def _get_special_handling_keys(self, tool: ToolConfig, filename: str) -> list[str] | None:
        """Get the special handling keys for a file, if any."""
        if filename in tool.special_handling:
            handling = tool.special_handling[filename]
            if handling.include_keys:
                return handling.include_keys
        return None

    def _extract_special_handling_content(
        self, tool: ToolConfig, filepath: Path
    ) -> str | None:
        """Extract filtered content for a file with special_handling.

        Returns the JSON string containing only the included keys, or None
        if the file has no special handling configured.
        """
        filename = filepath.name
        if filename not in tool.special_handling:
            return None
        handling = tool.special_handling[filename]
        if not handling.include_keys:
            return None
        try:
            return extract_json_keys(filepath, handling.include_keys, handling.exclude_patterns)
        except Exception:
            return None

    def _files_are_identical_with_special_handling(
        self, tool: ToolConfig, source_path: Path, target_path: Path
    ) -> bool:
        """
        Check if files are identical, accounting for special file handling.

        For files with special handling (e.g., JSON key extraction),
        compare the extracted versions rather than raw files.
        """
        filename = source_path.name

        # Check if this file has special handling
        if filename in tool.special_handling:
            handling = tool.special_handling[filename]

            # Extract the relevant parts from both files and compare the
            # parsed dicts so key ordering differences are ignored.
            try:
                source_extracted = extract_json_keys(
                    source_path, handling.include_keys, handling.exclude_patterns
                )
                target_extracted = extract_json_keys(
                    target_path, handling.include_keys, handling.exclude_patterns
                )

                return json.loads(source_extracted) == json.loads(target_extracted)
            except Exception:
                # If extraction fails, fall back to normal comparison
                return files_are_identical(source_path, target_path)
        else:
            # Normal file comparison
            return files_are_identical(source_path, target_path)

    def sync_tool(
        self,
        tool_name: str,
        direction: SyncDirection = SyncDirection.PUSH,
        auto_resolve: bool = False,
    ) -> bool:
        """
        Sync a single tool.

        Args:
            tool_name: Name of tool to sync
            direction: Direction of sync
            auto_resolve: Auto-resolve conflicts using timestamps

        Returns:
            True if sync succeeded
        """
        if tool_name not in self.config.tools:
            show_error(f"Tool '{tool_name}' not found in configuration")
            return False

        tool = self.config.tools[tool_name]

        if not tool.enabled:
            show_warning(f"Tool '{tool_name}' is disabled in configuration")
            return False

        show_info(f"Syncing {tool_name} ({direction.value})...")

        # Load state from parent directory (one level up from tool target)
        # This allows all tools to share the same state directory
        state_root = tool.target.parent
        state_manager = StateManager(state_root)
        state = state_manager.load_state()

        # Create sync plan
        plan = self._create_sync_plan(tool, direction, state)

        if (
            not plan.files_to_copy
            and not plan.files_to_delete
            and not plan.conflicts
            and not plan.reverse_suggestions
            and not plan.orphaned_files
        ):
            show_success(f"No changes to sync for {tool_name}")
            return True

        # Show summary
        changes = self._plan_to_changes(plan)
        direction_str = self._direction_str(direction)
        show_summary(changes, tool_name, direction_str, str(tool.source), str(tool.target))

        # In dry-run mode, stop here
        if self.dry_run:
            show_info("Dry run mode - no changes made")
            return True

        # Execute sync
        return self._execute_sync(tool, plan, state, state_manager, auto_resolve)

    def _get_propagation_managed_paths(self, tool: ToolConfig) -> list[str]:
        """
        Get list of paths that are managed by propagation for this tool.

        These paths should be excluded from sync to prevent conflicts.

        Returns:
            List of relative path patterns to exclude
        """
        excluded_paths = []

        for rule in self.config.propagate:
            for target in rule.targets:
                # Check if this target points to the current tool
                if target.dest_path:
                    # Absolute path - check if it's within tool's source or target
                    dest_path = Path(target.dest_path).expanduser()

                    # Check if dest is in this tool's source directory
                    try:
                        rel_path = str(dest_path.relative_to(tool.source))
                        excluded_paths.append(rel_path)
                        continue
                    except ValueError:
                        pass

                    # Check if dest is in this tool's target directory
                    try:
                        rel_path = str(dest_path.relative_to(tool.target))
                        excluded_paths.append(rel_path)
                    except ValueError:
                        pass

                elif target.tool == tool.name:
                    # Tool-relative target pointing to this tool
                    if target.target_file:
                        excluded_paths.append(target.target_file)

        if excluded_paths:
            show_info(
                f"Auto-excluding propagation-managed files: {', '.join(excluded_paths)}"
            )

        return excluded_paths

    def _create_sync_plan(
        self, tool: ToolConfig, direction: SyncDirection, state: SyncState
    ) -> SyncPlan:
        """Create a sync plan by comparing source and target."""
        plan = SyncPlan(
            tool=tool,
            direction=direction,
            files_to_copy=[],
            files_to_delete=[],
            conflicts=[],
            reverse_suggestions=[],
            orphaned_files=[],
            confirmed_deletions=set(),
        )

        # Build list of propagation-managed paths to exclude
        propagation_exclude = self._get_propagation_managed_paths(tool)

        # Find files in source and target
        source_files = find_files(
            tool.source,
            tool.include,
            list(tool.exclude) + propagation_exclude,
            self.config.settings.follow_symlinks,
            self.config.settings.respect_gitignore,
        )

        # If not following symlinks, find which source paths are symlinks
        # and exclude their target equivalents from scanning
        target_exclude = list(tool.exclude) + propagation_exclude
        if not self.config.settings.follow_symlinks:
            # Find all symlinks in source that match include patterns
            from .utils import matches_patterns

            symlink_paths = []
            for pattern in tool.include:
                # Handle glob patterns
                if "**" in pattern:
                    # Recursive search
                    base_parts = pattern.split("**")[0].strip("/").split("/")
                    if base_parts and base_parts[0]:
                        check_dir = tool.source / base_parts[0]
                    else:
                        check_dir = tool.source

                    if check_dir.exists() and check_dir.is_dir():
                        for item in check_dir.iterdir():
                            if item.is_symlink():
                                rel_path = str(item.relative_to(tool.source))
                                if matches_patterns(rel_path, tool.include, tool.exclude):
                                    symlink_paths.append(f"{rel_path}/**")

            if symlink_paths:
                target_exclude.extend(symlink_paths)
                show_info(
                    f"Excluding symlinked paths from target scan: {', '.join(symlink_paths)}"
                )

        target_files = find_files(
            tool.target,
            tool.include,
            target_exclude,  # Use extended exclude list
            self.config.settings.follow_symlinks,
            self.config.settings.respect_gitignore,
        )

        # Debug: Show what was found
        show_info(f"Source: {tool.source} ({len(source_files)} files)")
        show_info(f"Target: {tool.target} ({len(target_files)} files)")

        # Build path mappings
        source_by_relpath = {str(f.relative_to(tool.source)): f for f in source_files}
        target_by_relpath = {str(f.relative_to(tool.target)): f for f in target_files}

        all_relpaths = set(source_by_relpath.keys()) | set(target_by_relpath.keys())

        for relpath in all_relpaths:
            source_path = source_by_relpath.get(relpath)
            target_path = target_by_relpath.get(relpath)
            state_path = f"{tool.name}/{relpath}"

            # Get state for this file
            file_state = state.get_file_state(state_path)

            if direction == SyncDirection.PUSH:
                self._plan_push(plan, source_path, target_path, file_state, relpath)
            elif direction == SyncDirection.PULL:
                self._plan_pull(plan, source_path, target_path, file_state, relpath)
            else:  # SYNC (bidirectional)
                self._plan_bidirectional(plan, source_path, target_path, file_state, relpath)

        return plan

    def _plan_push(
        self,
        plan: SyncPlan,
        source_path: Path | None,
        target_path: Path | None,
        file_state,
        relpath: str,
    ):
        """Plan push operation (source → target)."""
        if source_path and not target_path:
            # New file in source
            plan.files_to_copy.append((source_path, plan.tool.target / relpath))
        elif source_path and target_path:
            # File exists in both
            if not self._files_are_identical_with_special_handling(
                plan.tool, source_path, target_path
            ):
                # For files with special_handling (partial sync), mtime
                # reflects the entire file including sections we don't sync.
                # The target can appear "newer" due to edits in unsynced
                # sections, so skip the mtime check -- source is authoritative
                # for its configured keys.
                has_special = source_path.name in plan.tool.special_handling
                if has_special:
                    plan.files_to_copy.append((source_path, target_path))
                else:
                    # Different content - check if target is newer
                    source_mtime = source_path.stat().st_mtime
                    target_mtime = target_path.stat().st_mtime

                    # If target is newer, suggest reverse sync instead of pushing
                    if target_mtime > source_mtime:
                        plan.reverse_suggestions.append((source_path, target_path))
                    else:
                        # Push source to target
                        plan.files_to_copy.append((source_path, target_path))
        elif not source_path and target_path:
            # File deleted from source or orphaned in target
            if file_state:  # Was previously synced - deletion candidate
                plan.files_to_delete.append((target_path, "target"))
            else:  # Never synced - orphaned file
                plan.orphaned_files.append(target_path)

    def _plan_pull(
        self,
        plan: SyncPlan,
        source_path: Path | None,
        target_path: Path | None,
        file_state,
        relpath: str,
    ):
        """Plan pull operation (target → source)."""
        if target_path and not source_path:
            # New file in target
            plan.files_to_copy.append((target_path, plan.tool.source / relpath))
        elif source_path and target_path:
            # File exists in both
            if not self._files_are_identical_with_special_handling(
                plan.tool, source_path, target_path
            ):
                # Different content - pull target to source
                plan.files_to_copy.append((target_path, source_path))
        elif source_path and not target_path:
            # File deleted from target
            if file_state:  # Was previously synced
                plan.files_to_delete.append((source_path, "source"))

    def _plan_bidirectional(
        self,
        plan: SyncPlan,
        source_path: Path | None,
        target_path: Path | None,
        file_state,
        relpath: str,
    ):
        """Plan bidirectional sync with three-way merge."""
        # Three-way merge logic
        if source_path and not target_path:
            if file_state:
                # Was in target, now deleted - delete from source?
                plan.files_to_delete.append((source_path, "source"))
            else:
                # New in source - add to target
                plan.files_to_copy.append((source_path, plan.tool.target / relpath))

        elif not source_path and target_path:
            if file_state:
                # Was in source, now deleted - delete from target?
                plan.files_to_delete.append((target_path, "target"))
            else:
                # New in target - add to source
                plan.files_to_copy.append((target_path, plan.tool.source / relpath))

        elif source_path and target_path:
            # File exists in both
            if not self._files_are_identical_with_special_handling(
                plan.tool, source_path, target_path
            ):
                # Check if either changed since last sync
                if file_state:
                    # Has state - can detect conflicts
                    source_changed = (
                        FileMetadata.from_file(source_path, plan.tool.source).checksum
                        != file_state.checksum
                    )
                    target_changed = (
                        FileMetadata.from_file(target_path, plan.tool.target).checksum
                        != file_state.checksum
                    )

                    if source_changed and not target_changed:
                        # Only source changed - push
                        plan.files_to_copy.append((source_path, target_path))
                    elif target_changed and not source_changed:
                        # Only target changed - pull
                        plan.files_to_copy.append((target_path, source_path))
                    elif source_changed and target_changed:
                        # Both changed - conflict!
                        plan.conflicts.append((source_path, target_path))
                else:
                    # No state - mark as conflict to be safe
                    plan.conflicts.append((source_path, target_path))

    def _execute_sync(
        self,
        tool: ToolConfig,
        plan: SyncPlan,
        state: SyncState,
        state_manager: StateManager,
        auto_resolve: bool,
    ) -> bool:
        """Execute the sync plan."""
        from .ui import (
            confirm_action,
            show_conflict_resolution_prompt,
            show_deletion_prompt,
            show_reverse_sync_prompt,
        )

        try:
            # Handle reverse suggestions first (when target is newer during push)
            if plan.reverse_suggestions:
                show_warning(
                    f"Found {len(plan.reverse_suggestions)} file(s) where target is newer than source"
                )

                for source_path, target_path in plan.reverse_suggestions:
                    relpath = str(source_path.relative_to(tool.source))
                    special_keys = self._get_special_handling_keys(tool, source_path.name)

                    from datetime import datetime

                    source_mtime = source_path.stat().st_mtime
                    target_mtime = target_path.stat().st_mtime

                    source_info = f"modified {datetime.fromtimestamp(source_mtime).strftime('%Y-%m-%d %H:%M:%S')}"
                    target_info = f"modified {datetime.fromtimestamp(target_mtime).strftime('%Y-%m-%d %H:%M:%S')}"

                    choice = show_reverse_sync_prompt(relpath, source_info, target_info, special_keys)

                    if choice == "diff":
                        # For special_handling files, diff only extracted keys
                        # to avoid exposing unsynced content (e.g. secrets).
                        src_ext = self._extract_special_handling_content(tool, source_path)
                        tgt_ext = self._extract_special_handling_content(tool, target_path)
                        if src_ext is not None and tgt_ext is not None:
                            diff_lines, _ = generate_diff_between_strings(
                                src_ext, tgt_ext, str(source_path), str(target_path)
                            )
                        else:
                            diff_lines, _ = generate_unified_diff(source_path, target_path)
                        from .ui import show_diff

                        show_diff(relpath, diff_lines, "source", "target")
                        choice = show_reverse_sync_prompt(relpath, source_info, target_info, special_keys)

                    if choice == "pull":
                        # Pull from target to source
                        plan.files_to_copy.append((target_path, source_path))
                        show_info(f"Will pull {relpath} from target to source")
                    elif choice == "push_anyway":
                        # Push source to target despite being older
                        plan.files_to_copy.append((source_path, target_path))
                        show_info(f"Will push {relpath} from source to target (overriding newer target)")
                    # else: skip

                # Clear reverse suggestions as they're now resolved
                plan.reverse_suggestions.clear()

            # Handle conflicts
            if plan.conflicts:
                show_warning(f"Found {len(plan.conflicts)} conflict(s) - need resolution")

                for source_path, target_path in plan.conflicts:
                    relpath = str(source_path.relative_to(tool.source))
                    special_keys = self._get_special_handling_keys(tool, source_path.name)

                    source_info = f"modified {source_path.stat().st_mtime}"
                    target_info = f"modified {target_path.stat().st_mtime}"

                    if auto_resolve:
                        # Auto-resolve using timestamps
                        if source_path.stat().st_mtime > target_path.stat().st_mtime:
                            choice = "keep_source"
                            show_info(f"Auto: Keeping source (newer) for {relpath}")
                        else:
                            choice = "use_target"
                            show_info(f"Auto: Using target (newer) for {relpath}")
                    else:
                        choice = show_conflict_resolution_prompt(relpath, source_info, target_info, special_keys)

                    if choice == "diff":
                        # For special_handling files, diff only extracted keys
                        src_ext = self._extract_special_handling_content(tool, source_path)
                        tgt_ext = self._extract_special_handling_content(tool, target_path)
                        if src_ext is not None and tgt_ext is not None:
                            diff_lines, _ = generate_diff_between_strings(
                                src_ext, tgt_ext, str(source_path), str(target_path)
                            )
                        else:
                            diff_lines, _ = generate_unified_diff(source_path, target_path)
                        from .ui import show_diff

                        show_diff(relpath, diff_lines, "source", "target")
                        choice = show_conflict_resolution_prompt(relpath, source_info, target_info, special_keys)

                    if choice == "keep_source":
                        plan.files_to_copy.append((source_path, target_path))
                    elif choice == "use_target":
                        plan.files_to_copy.append((target_path, source_path))
                    elif choice == "auto":
                        # Same as auto_resolve logic
                        if source_path.stat().st_mtime > target_path.stat().st_mtime:
                            plan.files_to_copy.append((source_path, target_path))
                        else:
                            plan.files_to_copy.append((target_path, source_path))
                    # else: skip

                # Clear conflicts as they're now resolved
                plan.conflicts.clear()

            # Handle orphaned files
            if plan.orphaned_files:
                from .ui import show_orphaned_file_action_prompt, show_orphaned_files_prompt

                show_warning(
                    f"Found {len(plan.orphaned_files)} orphaned file(s) in target (never synced)"
                )

                # Check if orphaned files might be due to symlinks not being followed
                if not self.config.settings.follow_symlinks:
                    # Check if any orphaned files are under directories that might be symlinks
                    orphan_dirs = set()
                    for orphan_path in plan.orphaned_files:
                        relpath = str(orphan_path.relative_to(tool.target))
                        parts = relpath.split("/")
                        if len(parts) > 1:
                            orphan_dirs.add(parts[0] + "/" + parts[1])  # First two levels

                    if orphan_dirs:
                        show_warning(
                            f"Note: 'follow_symlinks' is disabled. If source has symlinks, "
                            f"their files won't be detected. Affected paths: {', '.join(sorted(orphan_dirs)[:5])}"
                            + ("..." if len(orphan_dirs) > 5 else "")
                        )

                # Ask user what to do with all orphaned files
                bulk_choice = show_orphaned_files_prompt(len(plan.orphaned_files))

                if bulk_choice == "delete_all":
                    # Delete all orphaned files
                    for orphan_path in plan.orphaned_files:
                        relpath = str(orphan_path.relative_to(tool.target))
                        plan.files_to_delete.append((orphan_path, "target"))
                        plan.confirmed_deletions.add(orphan_path)  # Mark as already confirmed
                        show_info(f"Will delete orphaned file: {relpath}")
                elif bulk_choice == "sync_back_all":
                    # Sync all back to source
                    for orphan_path in plan.orphaned_files:
                        relpath = str(orphan_path.relative_to(tool.target))
                        source_dest = tool.source / relpath
                        plan.files_to_copy.append((orphan_path, source_dest))
                        show_info(f"Will sync back to source: {relpath}")
                elif bulk_choice == "select":
                    # Handle individually
                    for orphan_path in plan.orphaned_files:
                        relpath = str(orphan_path.relative_to(tool.target))
                        choice = show_orphaned_file_action_prompt(relpath)

                        if choice == "delete":
                            plan.files_to_delete.append((orphan_path, "target"))
                            plan.confirmed_deletions.add(orphan_path)  # Mark as already confirmed
                            show_info(f"Will delete: {relpath}")
                        elif choice == "sync_back":
                            source_dest = tool.source / relpath
                            plan.files_to_copy.append((orphan_path, source_dest))
                            show_info(f"Will sync back to source: {relpath}")
                        elif choice == "view":
                            # Open in editor (using $EDITOR or 'less')
                            import os
                            import subprocess

                            editor = os.environ.get("EDITOR", "less")
                            try:
                                subprocess.run([editor, str(orphan_path)], check=False)
                            except Exception as e:
                                show_error(f"Failed to open editor: {e}")

                            # Ask again after viewing
                            choice = show_orphaned_file_action_prompt(relpath)
                            if choice == "delete":
                                plan.files_to_delete.append((orphan_path, "target"))
                                plan.confirmed_deletions.add(orphan_path)  # Mark as already confirmed
                            elif choice == "sync_back":
                                source_dest = tool.source / relpath
                                plan.files_to_copy.append((orphan_path, source_dest))
                        # else: skip

                # Clear orphaned files as they're now handled
                plan.orphaned_files.clear()

            # Handle deletions with confirmation
            if plan.files_to_delete:
                confirmed_deletions = []

                for path, location in plan.files_to_delete:
                    relpath = str(
                        path.relative_to(tool.source if location == "source" else tool.target)
                    )

                    # Skip confirmation if already confirmed (e.g., from orphaned file handling)
                    if path in plan.confirmed_deletions:
                        confirmed_deletions.append((path, location))
                        state.record_deletion(f"{tool.name}/{relpath}", "unknown", "confirmed")
                        continue

                    # Check if confirmation is needed based on location
                    needs_confirmation = (
                        self.config.settings.confirm_destructive_source and location == "source"
                    ) or (
                        self.config.settings.confirm_destructive_target and location == "target"
                    )

                    if needs_confirmation:
                        choice = show_deletion_prompt(
                            relpath,
                            "target" if location == "source" else "source",
                            location,
                        )

                        if choice == "delete":
                            confirmed_deletions.append((path, location))
                            state.record_deletion(f"{tool.name}/{relpath}", "unknown", "confirmed")
                        elif choice == "sync_back":
                            # Sync file back to the location it was deleted from
                            if location == "source":
                                # Deleted from source, sync back from target
                                source_dest = tool.source / relpath
                                plan.files_to_copy.append((path, source_dest))
                                show_info(f"Will sync back to source: {relpath}")
                            else:
                                # Deleted from target, sync back from source
                                target_dest = tool.target / relpath
                                source_path = tool.source / relpath
                                if source_path.exists():
                                    plan.files_to_copy.append((source_path, target_dest))
                                    show_info(f"Will sync back to target: {relpath}")
                        elif choice == "view":
                            # Open file in editor and ask again
                            import os
                            import subprocess

                            editor = os.environ.get("EDITOR", "less")
                            try:
                                subprocess.run([editor, str(path)], check=False)
                            except Exception as e:
                                show_error(f"Failed to open editor: {e}")

                            # Ask again after viewing
                            choice = show_deletion_prompt(
                                relpath,
                                "target" if location == "source" else "source",
                                location,
                            )
                            if choice == "delete":
                                confirmed_deletions.append((path, location))
                                state.record_deletion(f"{tool.name}/{relpath}", "unknown", "confirmed")
                            elif choice == "sync_back":
                                if location == "source":
                                    source_dest = tool.source / relpath
                                    plan.files_to_copy.append((path, source_dest))
                                else:
                                    target_dest = tool.target / relpath
                                    source_path = tool.source / relpath
                                    if source_path.exists():
                                        plan.files_to_copy.append((source_path, target_dest))
                        elif choice == "skip":
                            show_info(f"Skipped deletion of {relpath}")
                    else:
                        # Auto-delete (no confirmation required)
                        confirmed_deletions.append((path, location))

                plan.files_to_delete = confirmed_deletions

            # Create backup before making changes
            if plan.files_to_copy or plan.files_to_delete:
                files_to_backup = {}
                for source, dest in plan.files_to_copy:
                    if dest.exists():
                        files_to_backup[dest] = source
                for path, _ in plan.files_to_delete:
                    files_to_backup[path] = None

                if files_to_backup:
                    backup_dir = self.backup_manager.create_backup(
                        tool.name,
                        plan.direction.value,
                        self._direction_str(plan.direction),
                        state_manager.machine_id,
                        files_to_backup,
                    )
                    show_info(f"Created backup: {backup_dir.name}")

            # Execute copies
            for source, dest in plan.files_to_copy:
                try:
                    # Confirm before overwriting source files in pull mode
                    if (
                        plan.direction == SyncDirection.PULL
                        and dest.exists()
                        and self.config.settings.confirm_destructive_source
                        and not auto_resolve
                    ):
                        relpath = str(dest.relative_to(tool.source))
                        special_keys = self._get_special_handling_keys(tool, source.name)
                        if special_keys:
                            keys_str = ", ".join(special_keys)
                            prompt_msg = f"Update sections ({keys_str}) in source file {relpath}?"
                        else:
                            prompt_msg = f"Overwrite source file {relpath}?"
                        if not confirm_action(prompt_msg):
                            show_info(f"Skipped: {relpath}")
                            continue

                    # Check if this file has special handling
                    source_name = source.name
                    if source_name in tool.special_handling:
                        handling = tool.special_handling[source_name]
                        keys_str = ", ".join(handling.include_keys) if handling.include_keys else "all"
                        show_info(f"Partial sync for {source_name} - updating sections: {keys_str}")

                        process_special_file(
                            source,
                            dest,
                            handling.mode,
                            handling.include_keys,
                            handling.exclude_patterns,
                        )
                    else:
                        # Normal file copy
                        safe_copy_file(source, dest, create_parents=True)

                    # Update state
                    # Determine base_path based on which file is the actual source
                    # For files being copied: source contains the file, dest is the destination
                    # Need to determine which directory the source file belongs to
                    if source.is_relative_to(tool.source):
                        base_path = tool.source
                    elif source.is_relative_to(tool.target):
                        base_path = tool.target
                    else:
                        # Fallback to plan direction
                        base_path = tool.source if plan.direction == SyncDirection.PUSH else tool.target

                    metadata = FileMetadata.from_file(source, base_path)
                    state.update_file(metadata, tool.name)

                    show_success(f"Synced: {metadata.relative_path}")
                except Exception as e:
                    show_error(f"Failed to copy {source}: {e}")
                    return False

            # Execute deletions
            for path, location in plan.files_to_delete:
                try:
                    from .files import safe_delete_file

                    # Don't create .deleted files - BackupManager already handles backups
                    safe_delete_file(path, backup=False)
                    relpath = str(
                        path.relative_to(tool.source if location == "source" else tool.target)
                    )
                    state.remove_file(f"{tool.name}/{relpath}")
                    show_success(f"Deleted: {relpath}")
                except Exception as e:
                    show_error(f"Failed to delete {path}: {e}")

            # Save state
            state_manager.save_state(state)

            show_success(f"Sync completed for {tool.name}")
            return True

        except Exception as e:
            show_error(f"Sync failed: {e}")
            return False

    def _plan_to_changes(self, plan: SyncPlan) -> list[FileChange]:
        """Convert sync plan to FileChange list for UI."""
        changes = []

        for source, dest in plan.files_to_copy:
            # Determine relative path
            if plan.direction == SyncDirection.PUSH:
                relpath = str(source.relative_to(plan.tool.source))
            else:
                relpath = str(source.relative_to(plan.tool.target))

            # Get special handling keys if applicable
            special_keys = self._get_special_handling_keys(plan.tool, source.name)

            # Determine change type
            if dest.exists():
                change_type = ChangeType.MODIFIED
                # For special_handling files, diff only the extracted keys
                # to avoid exposing unsynced content (e.g. secrets).
                source_extracted = self._extract_special_handling_content(plan.tool, source)
                dest_extracted = self._extract_special_handling_content(plan.tool, dest)
                if source_extracted is not None and dest_extracted is not None:
                    diff_stats = count_diff_lines_from_strings(
                        source_extracted, dest_extracted, str(source), str(dest)
                    )
                else:
                    diff_stats = count_diff_lines(source, dest)
            else:
                change_type = ChangeType.NEW
                diff_stats = None

            changes.append(FileChange(relpath, change_type, diff_stats, special_handling_keys=special_keys))

        for path, _ in plan.files_to_delete:
            # Determine relative path
            if plan.direction == SyncDirection.PUSH:
                relpath = str(path.relative_to(plan.tool.target))
            else:
                relpath = str(path.relative_to(plan.tool.source))

            changes.append(FileChange(relpath, ChangeType.DELETED))

        for source, target in plan.conflicts:
            relpath = str(source.relative_to(plan.tool.source))
            special_keys = self._get_special_handling_keys(plan.tool, source.name)
            changes.append(FileChange(relpath, ChangeType.CONFLICT, special_handling_keys=special_keys))

        for source, target in plan.reverse_suggestions:
            relpath = str(source.relative_to(plan.tool.source))
            special_keys = self._get_special_handling_keys(plan.tool, source.name)
            # For special_handling files, diff only extracted keys
            source_extracted = self._extract_special_handling_content(plan.tool, source)
            target_extracted = self._extract_special_handling_content(plan.tool, target)
            if source_extracted is not None and target_extracted is not None:
                diff_stats = count_diff_lines_from_strings(
                    source_extracted, target_extracted, str(source), str(target)
                )
            else:
                diff_stats = count_diff_lines(source, target)
            changes.append(
                FileChange(
                    relpath,
                    ChangeType.MODIFIED,
                    diff_stats,
                    warnings=["Target is newer than source"],
                    special_handling_keys=special_keys,
                )
            )

        for orphan_path in plan.orphaned_files:
            relpath = str(orphan_path.relative_to(plan.tool.target))
            changes.append(
                FileChange(
                    relpath,
                    ChangeType.ORPHANED,
                    warnings=["File exists in target but not in source (never synced)"],
                )
            )

        return changes

    def _direction_str(self, direction: SyncDirection) -> str:
        """Get human-readable direction string."""
        if direction == SyncDirection.PUSH:
            return "source → target"
        elif direction == SyncDirection.PULL:
            return "target → source"
        else:
            return "bidirectional"
