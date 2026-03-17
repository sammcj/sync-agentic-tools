"""Tests for sync module."""

from sync_agentic_tools.backup import BackupManager
from sync_agentic_tools.config import Config, Settings, ToolConfig
from sync_agentic_tools.sync import SyncDirection, SyncEngine


class TestSyncDirection:
    """Test SyncDirection enum."""

    def test_sync_direction_values(self):
        """Test sync direction enum values."""
        assert SyncDirection.PUSH.value == "push"
        assert SyncDirection.PULL.value == "pull"
        assert SyncDirection.SYNC.value == "sync"


class TestSyncEngine:
    """Test SyncEngine class."""

    def test_sync_engine_init(self, tmp_path):
        """Test initialising sync engine."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config = Config(
            settings=Settings(),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=["*.txt"],
                    exclude=[],
                )
            },
        )

        engine = SyncEngine(config, dry_run=True)

        assert engine.config == config
        assert engine.dry_run is True
        assert engine.backup_manager is not None

    def test_sync_unknown_tool(self, tmp_path):
        """Test syncing unknown tool."""
        config = Config(settings=Settings(), tools={})
        engine = SyncEngine(config, dry_run=True)

        result = engine.sync_tool("unknown_tool", SyncDirection.PUSH)

        assert result is False

    def test_sync_disabled_tool(self, tmp_path):
        """Test syncing disabled tool."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config = Config(
            settings=Settings(),
            tools={
                "disabled_tool": ToolConfig(
                    name="disabled_tool",
                    enabled=False,
                    source=source,
                    target=target,
                )
            },
        )

        engine = SyncEngine(config, dry_run=True)
        result = engine.sync_tool("disabled_tool", SyncDirection.PUSH)

        assert result is False

    def test_sync_no_changes(self, tmp_path):
        """Test sync with no changes."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config = Config(
            settings=Settings(respect_gitignore=False),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=["*.txt"],
                    exclude=[],
                )
            },
        )

        engine = SyncEngine(config, dry_run=True)
        result = engine.sync_tool("test_tool", SyncDirection.PUSH)

        assert result is True

    def test_sync_new_file_push(self, tmp_path):
        """Test syncing new file in push mode."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        # Create file in source
        (source / "test.txt").write_text("content")

        config = Config(
            settings=Settings(respect_gitignore=False),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=["*.txt"],
                    exclude=[],
                )
            },
        )

        # Dry run first
        engine = SyncEngine(config, dry_run=True)
        result = engine.sync_tool("test_tool", SyncDirection.PUSH)

        assert result is True

        # Actual sync
        engine = SyncEngine(config, dry_run=False)
        result = engine.sync_tool("test_tool", SyncDirection.PUSH)

        assert result is True
        assert (target / "test.txt").exists()
        assert (target / "test.txt").read_text() == "content"

    def test_sync_new_file_pull(self, tmp_path):
        """Test syncing new file in pull mode."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        # Create file in target
        (target / "test.txt").write_text("content")

        config = Config(
            settings=Settings(respect_gitignore=False),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=["*.txt"],
                    exclude=[],
                )
            },
        )

        # Dry run first
        engine = SyncEngine(config, dry_run=True)
        result = engine.sync_tool("test_tool", SyncDirection.PULL)

        assert result is True

        # Actual sync
        engine = SyncEngine(config, dry_run=False)
        result = engine.sync_tool("test_tool", SyncDirection.PULL, auto_resolve=True)

        assert result is True
        assert (source / "test.txt").exists()
        assert (source / "test.txt").read_text() == "content"

    def test_sync_modified_file_push(self, tmp_path):
        """Test syncing modified file in push mode."""
        import os
        import time

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        # Create target first, then source, to ensure source is strictly newer
        (target / "test.txt").write_text("old content")
        time.sleep(0.05)
        (source / "test.txt").write_text("new content")
        # Belt-and-braces: force source mtime to be 1s ahead
        now = time.time()
        os.utime(source / "test.txt", (now, now))
        os.utime(target / "test.txt", (now - 1, now - 1))

        config = Config(
            settings=Settings(respect_gitignore=False, show_diff_threshold=0),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=["*.txt"],
                    exclude=[],
                )
            },
        )

        # Sync in push mode
        engine = SyncEngine(config, dry_run=False)
        engine.backup_manager = BackupManager(backup_root=tmp_path / "backups")
        result = engine.sync_tool("test_tool", SyncDirection.PUSH)

        assert result is True
        assert (target / "test.txt").read_text() == "new content"

    def test_sync_respects_include_patterns(self, tmp_path):
        """Test that sync respects include patterns."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        # Create various files
        (source / "include.txt").write_text("include")
        (source / "exclude.log").write_text("exclude")

        config = Config(
            settings=Settings(respect_gitignore=False),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=["*.txt"],
                    exclude=[],
                )
            },
        )

        engine = SyncEngine(config, dry_run=False)
        result = engine.sync_tool("test_tool", SyncDirection.PUSH)

        assert result is True
        assert (target / "include.txt").exists()
        assert not (target / "exclude.log").exists()

    def test_sync_respects_exclude_patterns(self, tmp_path):
        """Test that sync respects exclude patterns."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        # Create various files
        (source / "keep.txt").write_text("keep")
        (source / "ignore.tmp").write_text("ignore")

        config = Config(
            settings=Settings(respect_gitignore=False),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=["*.txt", "*.tmp"],
                    exclude=["*.tmp"],
                )
            },
        )

        engine = SyncEngine(config, dry_run=False)
        result = engine.sync_tool("test_tool", SyncDirection.PUSH)

        assert result is True
        assert (target / "keep.txt").exists()
        assert not (target / "ignore.tmp").exists()

    def test_sync_respects_gitignore(self, tmp_path):
        """Test that sync respects gitignore files."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        # Create gitignore
        (source / ".gitignore").write_text("*.log\n")

        # Create files
        (source / "keep.txt").write_text("keep")
        (source / "ignore.log").write_text("ignore")

        config = Config(
            settings=Settings(respect_gitignore=True),
            tools={
                "test_tool": ToolConfig(
                    name="test_tool",
                    enabled=True,
                    source=source,
                    target=target,
                    include=[],  # Include all
                    exclude=[],
                )
            },
        )

        engine = SyncEngine(config, dry_run=False)
        result = engine.sync_tool("test_tool", SyncDirection.PUSH)

        assert result is True
        assert (target / "keep.txt").exists()
        assert not (target / "ignore.log").exists()
