"""Tests for state module."""

import json

from sync_agentic_tools.files import FileMetadata
from sync_agentic_tools.state import DeletionRecord, FileState, StateManager, SyncState


class TestFileState:
    """Test FileState dataclass."""

    def test_file_state_creation(self):
        """Test creating file state."""
        state = FileState(
            checksum="sha256:abc123",
            last_synced="2025-01-01T12:00:00",
        )

        assert state.checksum == "sha256:abc123"
        assert state.last_synced == "2025-01-01T12:00:00"


class TestDeletionRecord:
    """Test DeletionRecord dataclass."""

    def test_deletion_record_creation(self):
        """Test creating deletion record."""
        record = DeletionRecord(
            deleted_at="2025-01-01T12:00:00",
            checksum="sha256:abc123",
            decision="confirmed",
        )

        assert record.deleted_at == "2025-01-01T12:00:00"
        assert record.checksum == "sha256:abc123"
        assert record.decision == "confirmed"


class TestSyncState:
    """Test SyncState class."""

    def test_sync_state_creation(self):
        """Test creating sync state."""
        state = SyncState(
            machine_id="test-machine-12345678",
            hostname="test-machine",
            last_sync="2025-01-01T12:00:00",
        )

        assert state.machine_id == "test-machine-12345678"
        assert state.hostname == "test-machine"
        assert state.last_sync == "2025-01-01T12:00:00"
        assert len(state.files) == 0
        assert len(state.deletions) == 0

    def test_to_dict(self):
        """Test converting state to dictionary."""
        state = SyncState(
            machine_id="test-12345678",
            hostname="test",
            last_sync="2025-01-01T12:00:00",
        )

        state_dict = state.to_dict()

        assert state_dict["machine_id"] == "test-12345678"
        assert state_dict["hostname"] == "test"
        assert state_dict["last_sync"] == "2025-01-01T12:00:00"
        assert "files" in state_dict
        assert "deletions" in state_dict

    def test_from_dict(self):
        """Test creating state from dictionary."""
        data = {
            "machine_id": "test-12345678",
            "hostname": "test",
            "last_sync": "2025-01-01T12:00:00",
            "files": {
                "test/file.txt": {
                    "checksum": "sha256:abc123",
                    "last_synced": "2025-01-01T12:00:00",
                }
            },
            "deletions": {
                "test/deleted.txt": {
                    "deleted_at": "2025-01-01T11:30:00",
                    "checksum": "sha256:def456",
                    "decision": "confirmed",
                }
            },
        }

        state = SyncState.from_dict(data)

        assert state.machine_id == "test-12345678"
        assert "test/file.txt" in state.files
        assert state.files["test/file.txt"].checksum == "sha256:abc123"
        assert "test/deleted.txt" in state.deletions

    def test_from_dict_backwards_compatible(self):
        """Test loading old state format with size and mtime fields."""
        # Old format state file with size and mtime
        old_format_data = {
            "machine_id": "test-12345678",
            "hostname": "test",
            "last_sync": "2025-01-01T12:00:00",
            "files": {
                "test/file.txt": {
                    "checksum": "sha256:abc123",
                    "size": 1024,
                    "mtime": "2025-01-01T11:00:00",
                    "last_synced": "2025-01-01T12:00:00",
                }
            },
            "deletions": {},
        }

        # Should load successfully, ignoring size and mtime
        state = SyncState.from_dict(old_format_data)

        assert state.machine_id == "test-12345678"
        assert "test/file.txt" in state.files
        assert state.files["test/file.txt"].checksum == "sha256:abc123"
        assert state.files["test/file.txt"].last_synced == "2025-01-01T12:00:00"

    def test_update_file(self, tmp_path):
        """Test updating file state."""
        state = SyncState(
            machine_id="test-12345678",
            hostname="test",
            last_sync="2025-01-01T12:00:00",
        )

        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        metadata = FileMetadata.from_file(test_file, tmp_path)
        state.update_file(metadata, "test_tool")

        assert "test_tool/test.txt" in state.files
        file_state = state.files["test_tool/test.txt"]
        assert file_state.checksum == metadata.checksum

    def test_record_deletion(self):
        """Test recording file deletion."""
        state = SyncState(
            machine_id="test-12345678",
            hostname="test",
            last_sync="2025-01-01T12:00:00",
        )

        state.record_deletion("test/file.txt", "sha256:abc123", "confirmed")

        assert "test/file.txt" in state.deletions
        record = state.deletions["test/file.txt"]
        assert record.checksum == "sha256:abc123"
        assert record.decision == "confirmed"

    def test_remove_file(self):
        """Test removing file from state."""
        state = SyncState(
            machine_id="test-12345678",
            hostname="test",
            last_sync="2025-01-01T12:00:00",
        )

        state.files["test/file.txt"] = FileState(
            checksum="sha256:abc123",
            last_synced="2025-01-01T12:00:00",
        )

        state.remove_file("test/file.txt")

        assert "test/file.txt" not in state.files

    def test_get_file_state(self):
        """Test getting file state."""
        state = SyncState(
            machine_id="test-12345678",
            hostname="test",
            last_sync="2025-01-01T12:00:00",
        )

        state.files["test/file.txt"] = FileState(
            checksum="sha256:abc123",
            last_synced="2025-01-01T12:00:00",
        )

        file_state = state.get_file_state("test/file.txt")
        assert file_state is not None
        assert file_state.checksum == "sha256:abc123"

        nonexistent = state.get_file_state("test/nonexistent.txt")
        assert nonexistent is None

    def test_has_deletion_record(self):
        """Test checking for deletion record."""
        state = SyncState(
            machine_id="test-12345678",
            hostname="test",
            last_sync="2025-01-01T12:00:00",
        )

        state.deletions["test/deleted.txt"] = DeletionRecord(
            deleted_at="2025-01-01T11:30:00",
            checksum="sha256:abc123",
            decision="confirmed",
        )

        assert state.has_deletion_record("test/deleted.txt")
        assert not state.has_deletion_record("test/other.txt")


class TestStateManager:
    """Test StateManager class."""

    def test_state_manager_init(self, tmp_path):
        """Test initialising state manager."""
        manager = StateManager(tmp_path)

        assert manager.target_path == tmp_path
        assert manager.state_dir == tmp_path / ".sync-state"
        assert manager.machine_id is not None
        assert manager.hostname is not None

    def test_load_state_new(self, tmp_path):
        """Test loading state when none exists."""
        manager = StateManager(tmp_path)
        state = manager.load_state()

        assert state.machine_id == manager.machine_id
        assert state.hostname == manager.hostname
        assert len(state.files) == 0

    def test_save_and_load_state(self, tmp_path):
        """Test saving and loading state."""
        manager = StateManager(tmp_path)
        state = manager.load_state()

        # Add some data
        state.files["test/file.txt"] = FileState(
            checksum="sha256:abc123",
            last_synced="2025-01-01T12:00:00",
        )

        # Save state
        manager.save_state(state)

        # Load state again
        loaded_state = manager.load_state()

        assert "test/file.txt" in loaded_state.files
        assert loaded_state.files["test/file.txt"].checksum == "sha256:abc123"

    def test_save_creates_directory(self, tmp_path):
        """Test that save creates state directory."""
        manager = StateManager(tmp_path)
        state = manager.load_state()

        assert not manager.state_dir.exists()

        manager.save_state(state)

        assert manager.state_dir.exists()
        assert manager.state_dir.is_dir()

    def test_save_updates_timestamp(self, tmp_path):
        """Test that save updates last_sync timestamp."""
        manager = StateManager(tmp_path)
        state = manager.load_state()

        original_time = state.last_sync
        manager.save_state(state)

        # Timestamp should be updated
        assert state.last_sync != original_time

    def test_load_all_states_empty(self, tmp_path):
        """Test loading all states when none exist."""
        manager = StateManager(tmp_path)
        all_states = manager.load_all_states()

        assert len(all_states) == 0

    def test_load_all_states_multiple(self, tmp_path):
        """Test loading states from multiple machines."""
        # Create multiple state files
        state_dir = tmp_path / ".sync-state"
        state_dir.mkdir()

        # Machine 1
        state1 = {
            "machine_id": "machine1-12345678",
            "hostname": "machine1",
            "last_sync": "2025-01-01T12:00:00",
            "files": {},
            "deletions": {},
        }
        with open(state_dir / "machine1.json", "w") as f:
            json.dump(state1, f)

        # Machine 2
        state2 = {
            "machine_id": "machine2-87654321",
            "hostname": "machine2",
            "last_sync": "2025-01-01T13:00:00",
            "files": {},
            "deletions": {},
        }
        with open(state_dir / "machine2.json", "w") as f:
            json.dump(state2, f)

        manager = StateManager(tmp_path)
        all_states = manager.load_all_states()

        assert len(all_states) == 2
        assert "machine1-12345678" in all_states
        assert "machine2-87654321" in all_states

    def test_get_most_recent_state_for_file(self, tmp_path):
        """Test getting most recent state for a file."""
        state_dir = tmp_path / ".sync-state"
        state_dir.mkdir()

        # Machine 1 - older sync
        state1 = {
            "machine_id": "machine1-12345678",
            "hostname": "machine1",
            "last_sync": "2025-01-01T12:00:00",
            "files": {
                "test/file.txt": {
                    "checksum": "sha256:old",
                    "last_synced": "2025-01-01T12:00:00",
                }
            },
            "deletions": {},
        }
        with open(state_dir / "machine1.json", "w") as f:
            json.dump(state1, f)

        # Machine 2 - newer sync
        state2 = {
            "machine_id": "machine2-87654321",
            "hostname": "machine2",
            "last_sync": "2025-01-01T14:00:00",
            "files": {
                "test/file.txt": {
                    "checksum": "sha256:new",
                    "last_synced": "2025-01-01T14:00:00",
                }
            },
            "deletions": {},
        }
        with open(state_dir / "machine2.json", "w") as f:
            json.dump(state2, f)

        manager = StateManager(tmp_path)
        most_recent = manager.get_most_recent_state_for_file("test/file.txt")

        assert most_recent is not None
        assert most_recent.checksum == "sha256:new"

    def test_get_most_recent_state_exclude_current(self, tmp_path):
        """Test getting most recent state excluding current machine."""
        manager = StateManager(tmp_path)

        # Save current machine state
        state = manager.load_state()
        state.files["test/file.txt"] = FileState(
            checksum="sha256:current",
            last_synced="2025-01-01T15:00:00",
        )
        manager.save_state(state)

        # Create another machine state
        state_dir = tmp_path / ".sync-state"
        state2 = {
            "machine_id": "other-machine-87654321",
            "hostname": "other-machine",
            "last_sync": "2025-01-01T14:00:00",
            "files": {
                "test/file.txt": {
                    "checksum": "sha256:other",
                    "last_synced": "2025-01-01T14:00:00",
                }
            },
            "deletions": {},
        }
        with open(state_dir / "other-machine.json", "w") as f:
            json.dump(state2, f)

        # Get most recent excluding current machine
        most_recent = manager.get_most_recent_state_for_file("test/file.txt", exclude_current=True)

        assert most_recent is not None
        assert most_recent.checksum == "sha256:other"
