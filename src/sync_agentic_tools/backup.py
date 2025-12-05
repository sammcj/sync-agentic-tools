"""Backup management for agentic-sync."""

import gzip
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class BackupChange:
    """Record of a change being backed up."""

    file: str
    action: str  # "modified", "deleted", "created"
    size_before: int | None = None
    size_after: int | None = None
    checksum_before: str | None = None
    checksum_after: str | None = None


@dataclass
class BackupManifest:
    """Manifest for a backup."""

    timestamp: str
    operation: str  # "push", "pull", "sync"
    direction: str  # "source→target", "target→source", "bidirectional"
    tool: str
    machine_id: str
    changes: list[BackupChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialisation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BackupManifest":
        """Create from dictionary."""
        changes = [BackupChange(**change) for change in data.get("changes", [])]
        return cls(
            timestamp=data["timestamp"],
            operation=data["operation"],
            direction=data["direction"],
            tool=data["tool"],
            machine_id=data["machine_id"],
            changes=changes,
        )


class BackupManager:
    """Manages backup operations."""

    def __init__(self, backup_root: Path | None = None):
        """
        Initialise backup manager.

        Args:
            backup_root: Root directory for backups (default: ~/.agentic-sync/backups)
        """
        if backup_root is None:
            backup_root = Path.home() / ".agentic-sync" / "backups"

        self.backup_root = backup_root
        self.backup_root.mkdir(parents=True, exist_ok=True)

    def create_backup(
        self,
        tool_name: str,
        operation: str,
        direction: str,
        machine_id: str,
        files_to_backup: dict[Path, Path | None],
    ) -> Path:
        """
        Create a backup before performing operations.

        Args:
            tool_name: Name of tool being synced
            operation: Operation type ("push", "pull", "sync")
            direction: Direction of sync
            machine_id: Machine identifier
            files_to_backup: Dict mapping source paths to optional destination paths
                           (None destination means file will be deleted)

        Returns:
            Path to backup directory
        """
        # Generate backup ID
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_id = f"{timestamp}_{operation}_{tool_name}"
        backup_dir = self.backup_root / backup_id
        backup_dir.mkdir(parents=True, exist_ok=True)

        files_dir = backup_dir / "files"
        files_dir.mkdir(exist_ok=True)

        # Create manifest
        manifest = BackupManifest(
            timestamp=datetime.now().isoformat(),
            operation=operation,
            direction=direction,
            tool=tool_name,
            machine_id=machine_id,
        )

        # Backup each file
        for source, dest in files_to_backup.items():
            if not source.exists():
                continue

            # Determine action
            if dest is None:
                action = "deleted"
            elif not dest.exists():
                action = "created"
            else:
                action = "modified"

            # Create backup copy
            relative_path = source.name
            backup_file = files_dir / relative_path
            counter = 1
            while backup_file.exists():
                backup_file = files_dir / f"{source.stem}_{counter}{source.suffix}"
                counter += 1

            shutil.copy2(source, backup_file)

            # Record change
            change = BackupChange(
                file=str(source),
                action=action,
                size_before=source.stat().st_size if source.exists() else None,
                size_after=dest.stat().st_size if dest and dest.exists() else None,
            )
            manifest.changes.append(change)

        # Save manifest
        manifest_file = backup_dir / "manifest.json"
        with open(manifest_file, "w") as f:
            json.dump(manifest.to_dict(), f, indent=2)

        return backup_dir

    def list_backups(self, tool_name: str | None = None) -> list[dict[str, str]]:
        """
        List available backups.

        Args:
            tool_name: Filter by tool name (None = all tools)

        Returns:
            List of backup info dictionaries
        """
        backups = []

        for backup_dir in sorted(self.backup_root.iterdir(), reverse=True):
            if not backup_dir.is_dir():
                continue

            manifest_file = backup_dir / "manifest.json"
            if not manifest_file.exists():
                continue

            try:
                with open(manifest_file) as f:
                    manifest_data = json.load(f)
                    manifest = BackupManifest.from_dict(manifest_data)

                if tool_name and manifest.tool != tool_name:
                    continue

                backups.append(
                    {
                        "id": backup_dir.name,
                        "timestamp": manifest.timestamp,
                        "tool": manifest.tool,
                        "operation": manifest.operation,
                        "changes": len(manifest.changes),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue

        return backups

    def restore_backup(self, backup_id: str) -> BackupManifest:
        """
        Restore files from a backup.

        Args:
            backup_id: Backup identifier

        Returns:
            BackupManifest for restored backup

        Raises:
            FileNotFoundError: If backup doesn't exist
        """
        backup_dir = self.backup_root / backup_id
        if not backup_dir.exists():
            raise FileNotFoundError(f"Backup not found: {backup_id}")

        manifest_file = backup_dir / "manifest.json"
        with open(manifest_file) as f:
            manifest = BackupManifest.from_dict(json.load(f))

        files_dir = backup_dir / "files"

        # Restore each file
        for change in manifest.changes:
            original_path = Path(change.file)
            # Find backup file (may have counter suffix)
            backup_files = list(files_dir.glob(f"{original_path.stem}*{original_path.suffix}"))

            if backup_files:
                backup_file = backup_files[0]
                # Restore file
                original_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, original_path)

        return manifest

    def cleanup_old_backups(self, retention_days: int = 30, retention_count: int = 30) -> int:
        """
        Clean up old backups based on retention policy.

        Args:
            retention_days: Keep backups newer than this many days
            retention_count: Keep at least this many recent backups

        Returns:
            Number of backups deleted
        """
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        all_backups = []

        # Collect all backups with timestamps
        for backup_dir in self.backup_root.iterdir():
            if not backup_dir.is_dir():
                continue

            manifest_file = backup_dir / "manifest.json"
            if not manifest_file.exists():
                continue

            try:
                with open(manifest_file) as f:
                    manifest_data = json.load(f)
                    timestamp = datetime.fromisoformat(manifest_data["timestamp"])
                    all_backups.append((backup_dir, timestamp))
            except (json.JSONDecodeError, KeyError):
                continue

        # Sort by timestamp (newest first)
        all_backups.sort(key=lambda x: x[1], reverse=True)

        deleted_count = 0

        # Keep at least retention_count backups
        for i, (backup_dir, timestamp) in enumerate(all_backups):
            if i < retention_count:
                continue  # Keep recent backups

            if timestamp < cutoff_date:
                # Delete old backup
                shutil.rmtree(backup_dir)
                deleted_count += 1

        return deleted_count

    def compress_old_backups(self, age_days: int = 7) -> int:
        """
        Compress backups older than specified age.

        Args:
            age_days: Compress backups older than this many days

        Returns:
            Number of backups compressed
        """
        cutoff_date = datetime.now() - timedelta(days=age_days)
        compressed_count = 0

        for backup_dir in self.backup_root.iterdir():
            if not backup_dir.is_dir():
                continue

            files_dir = backup_dir / "files"
            if not files_dir.exists():
                continue

            manifest_file = backup_dir / "manifest.json"
            if not manifest_file.exists():
                continue

            try:
                with open(manifest_file) as f:
                    manifest_data = json.load(f)
                    timestamp = datetime.fromisoformat(manifest_data["timestamp"])

                if timestamp < cutoff_date:
                    # Compress files in this backup
                    for file_path in files_dir.iterdir():
                        if file_path.suffix != ".gz":
                            # Compress file
                            with open(file_path, "rb") as f_in:
                                with gzip.open(f"{file_path}.gz", "wb") as f_out:
                                    shutil.copyfileobj(f_in, f_out)
                            file_path.unlink()
                    compressed_count += 1
            except (json.JSONDecodeError, KeyError):
                continue

        return compressed_count
