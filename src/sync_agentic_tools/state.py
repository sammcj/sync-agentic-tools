"""State tracking for agentic-sync."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .files import FileMetadata
from .utils import get_machine_id


@dataclass
class FileState:
    """State information for a single file."""

    checksum: str
    last_synced: str  # ISO format datetime


@dataclass
class DeletionRecord:
    """Record of a deleted file."""

    deleted_at: str  # ISO format datetime
    checksum: str
    decision: str  # "confirmed", "skipped", "pending"


@dataclass
class SyncState:
    """Sync state for a machine."""

    machine_id: str
    hostname: str
    last_sync: str  # ISO format datetime
    files: dict[str, FileState] = field(default_factory=dict)
    deletions: dict[str, DeletionRecord] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialisation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SyncState":
        """Create from dictionary."""
        # Handle backwards compatibility - filter out old fields (size, mtime)
        files = {}
        for path, file_data in data.get("files", {}).items():
            # Only keep fields that FileState accepts
            filtered_data = {
                "checksum": file_data["checksum"],
                "last_synced": file_data["last_synced"],
            }
            files[path] = FileState(**filtered_data)

        deletions = {
            path: DeletionRecord(**del_data) for path, del_data in data.get("deletions", {}).items()
        }

        return cls(
            machine_id=data["machine_id"],
            hostname=data["hostname"],
            last_sync=data["last_sync"],
            files=files,
            deletions=deletions,
        )

    def update_file(self, metadata: FileMetadata, tool_name: str) -> None:
        """
        Update file state.

        Args:
            metadata: File metadata
            tool_name: Tool name for path prefix
        """
        # Store relative path with tool prefix
        relative_path = f"{tool_name}/{metadata.relative_path}"

        self.files[relative_path] = FileState(
            checksum=metadata.checksum,
            last_synced=datetime.now().isoformat(),
        )

    def record_deletion(self, relative_path: str, checksum: str, decision: str = "pending") -> None:
        """
        Record a file deletion.

        Args:
            relative_path: Relative path to deleted file
            checksum: Checksum of deleted file
            decision: User decision ("confirmed", "skipped", "pending")
        """
        self.deletions[relative_path] = DeletionRecord(
            deleted_at=datetime.now().isoformat(), checksum=checksum, decision=decision
        )

    def remove_file(self, relative_path: str) -> None:
        """
        Remove file from state.

        Args:
            relative_path: Relative path to remove
        """
        if relative_path in self.files:
            del self.files[relative_path]

    def get_file_state(self, relative_path: str) -> FileState | None:
        """
        Get state for a file.

        Args:
            relative_path: Relative path

        Returns:
            FileState or None if not found
        """
        return self.files.get(relative_path)

    def has_deletion_record(self, relative_path: str) -> bool:
        """
        Check if file has a deletion record.

        Args:
            relative_path: Relative path

        Returns:
            True if deletion record exists
        """
        return relative_path in self.deletions


class StateManager:
    """Manages sync state files."""

    def __init__(self, target_path: Path):
        """
        Initialise state manager.

        Args:
            target_path: Path to target root
        """
        self.target_path = target_path
        self.state_dir = target_path / ".sync-state"
        self.machine_id = get_machine_id()
        self.hostname = self.machine_id.split("-")[0]

    def load_state(self) -> SyncState:
        """
        Load state for current machine.

        Returns:
            SyncState object (creates new if doesn't exist)
        """
        state_file = self._get_state_file_path()

        if state_file.exists():
            with open(state_file) as f:
                data = json.load(f)
                return SyncState.from_dict(data)
        else:
            # Create new state
            return SyncState(
                machine_id=self.machine_id,
                hostname=self.hostname,
                last_sync=datetime.now().isoformat(),
            )

    def save_state(self, state: SyncState) -> None:
        """
        Save state for current machine.

        Args:
            state: State to save
        """
        # Ensure state directory exists
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Update timestamp
        state.last_sync = datetime.now().isoformat()

        state_file = self._get_state_file_path()

        # Atomic write using temporary file
        temp_file = state_file.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            json.dump(state.to_dict(), f, indent=2)

        # Rename to final location (atomic on POSIX)
        temp_file.replace(state_file)

    def load_all_states(self) -> dict[str, SyncState]:
        """
        Load states from all machines.

        Returns:
            Dictionary mapping machine_id to SyncState
        """
        states = {}

        if not self.state_dir.exists():
            return states

        for state_file in self.state_dir.glob("*.json"):
            try:
                with open(state_file) as f:
                    data = json.load(f)
                    state = SyncState.from_dict(data)
                    states[state.machine_id] = state
            except (json.JSONDecodeError, KeyError):
                # Skip invalid state files
                continue

        return states

    def get_most_recent_state_for_file(
        self, relative_path: str, exclude_current: bool = False
    ) -> FileState | None:
        """
        Get most recent state for a file across all machines.

        Args:
            relative_path: Relative path to file
            exclude_current: Exclude current machine from search

        Returns:
            Most recent FileState or None
        """
        all_states = self.load_all_states()
        most_recent = None
        most_recent_time = None

        for machine_id, state in all_states.items():
            if exclude_current and machine_id == self.machine_id:
                continue

            file_state = state.get_file_state(relative_path)
            if file_state:
                synced_time = datetime.fromisoformat(file_state.last_synced)
                if most_recent_time is None or synced_time > most_recent_time:
                    most_recent = file_state
                    most_recent_time = synced_time

        return most_recent

    def _get_state_file_path(self) -> Path:
        """Get path to state file for current machine."""
        return self.state_dir / f"{self.hostname}.json"
