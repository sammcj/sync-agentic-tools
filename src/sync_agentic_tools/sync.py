"""Core sync logic for agentic-sync."""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .backup import BackupManager
from .config import Config, ToolConfig
from .diff import count_diff_lines, generate_unified_diff
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

            # Extract the relevant parts from both files
            try:
                source_extracted = extract_json_keys(
                    source_path, handling.include_keys, handling.exclude_patterns
                )
                target_extracted = extract_json_keys(
                    target_path, handling.include_keys, handling.exclude_patterns
                )

                # Compare checksums of extracted content
                import hashlib

                source_hash = hashlib.sha256(source_extracted.encode()).hexdigest()
                target_hash = hashlib.sha256(target_extracted.encode()).hexdigest()

                return source_hash == target_hash
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
        ):
            show_success(f"No changes to sync for {tool_name}")
            return True

        # Show summary
        changes = self._plan_to_changes(plan)
        direction_str = self._direction_str(direction)
        show_summary(changes, tool_name, direction_str)

        # In dry-run mode, stop here
        if self.dry_run:
            show_info("Dry run mode - no changes made")
            return True

        # Execute sync
        return self._execute_sync(tool, plan, state, state_manager, auto_resolve)

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
        )

        # Find files in source and target
        source_files = find_files(
            tool.source,
            tool.include,
            tool.exclude,
            self.config.settings.follow_symlinks,
            self.config.settings.respect_gitignore,
        )

        target_files = find_files(
            tool.target,
            tool.include,
            tool.exclude,
            self.config.settings.follow_symlinks,
            self.config.settings.respect_gitignore,
        )

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
            # File deleted from source
            if file_state:  # Was previously synced
                plan.files_to_delete.append((target_path, "target"))

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

                    from datetime import datetime

                    source_mtime = source_path.stat().st_mtime
                    target_mtime = target_path.stat().st_mtime

                    source_info = f"modified {datetime.fromtimestamp(source_mtime).strftime('%Y-%m-%d %H:%M:%S')}"
                    target_info = f"modified {datetime.fromtimestamp(target_mtime).strftime('%Y-%m-%d %H:%M:%S')}"

                    choice = show_reverse_sync_prompt(relpath, source_info, target_info)

                    if choice == "diff":
                        # Show diff and ask again
                        diff_lines, _ = generate_unified_diff(source_path, target_path)
                        from .ui import show_diff

                        show_diff(relpath, diff_lines, "source", "target")
                        choice = show_reverse_sync_prompt(relpath, source_info, target_info)

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
                        choice = show_conflict_resolution_prompt(relpath, source_info, target_info)

                    if choice == "diff":
                        # Show diff and ask again
                        diff_lines, _ = generate_unified_diff(source_path, target_path)
                        from .ui import show_diff

                        show_diff(relpath, diff_lines, "source", "target")
                        choice = show_conflict_resolution_prompt(relpath, source_info, target_info)

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

            # Handle deletions with confirmation
            if plan.files_to_delete:
                confirmed_deletions = []

                for path, location in plan.files_to_delete:
                    relpath = str(
                        path.relative_to(tool.source if location == "source" else tool.target)
                    )

                    if self.config.settings.confirm_destructive_source and location == "source":
                        choice = show_deletion_prompt(
                            relpath,
                            "target" if location == "source" else "source",
                            location,
                        )

                        if choice == "delete":
                            confirmed_deletions.append((path, location))
                            state.record_deletion(f"{tool.name}/{relpath}", "unknown", "confirmed")
                        elif choice == "skip":
                            show_info(f"Skipped deletion of {relpath}")
                        # else keep_both: do nothing
                    else:
                        # Auto-delete for target files (less dangerous)
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
                        if not confirm_action(f"Overwrite source file {relpath}?"):
                            show_info(f"Skipped: {relpath}")
                            continue

                    # Check if this file has special handling
                    source_name = source.name
                    if source_name in tool.special_handling:
                        handling = tool.special_handling[source_name]
                        show_info(f"Applying special handling for {source_name}")

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

                    safe_delete_file(path, backup=True)
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

            # Determine change type
            if dest.exists():
                change_type = ChangeType.MODIFIED
                diff_stats = count_diff_lines(source, dest)
            else:
                change_type = ChangeType.NEW
                diff_stats = None

            changes.append(FileChange(relpath, change_type, diff_stats))

        for path, _ in plan.files_to_delete:
            # Determine relative path
            if plan.direction == SyncDirection.PUSH:
                relpath = str(path.relative_to(plan.tool.target))
            else:
                relpath = str(path.relative_to(plan.tool.source))

            changes.append(FileChange(relpath, ChangeType.DELETED))

        for source, target in plan.conflicts:
            relpath = str(source.relative_to(plan.tool.source))
            changes.append(FileChange(relpath, ChangeType.CONFLICT))

        for source, target in plan.reverse_suggestions:
            relpath = str(source.relative_to(plan.tool.source))
            diff_stats = count_diff_lines(source, target)
            changes.append(
                FileChange(
                    relpath,
                    ChangeType.MODIFIED,
                    diff_stats,
                    warnings=["Target is newer than source"],
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
