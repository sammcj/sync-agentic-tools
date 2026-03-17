"""Tests for config module."""

from pathlib import Path

import pytest
import yaml

from sync_agentic_tools.config import (
    Config,
    Settings,
    SpecialHandling,
    ToolConfig,
)


class TestSettings:
    """Test Settings dataclass."""

    def test_default_settings(self):
        """Test default settings values."""
        settings = Settings()
        assert settings.backup_retention_days == 30
        assert settings.backup_retention_count == 30
        assert settings.auto_cleanup_backups is True
        assert settings.compress_old_backups is True
        assert settings.follow_symlinks is False
        assert settings.respect_gitignore is True
        assert settings.confirm_destructive_source is True
        assert settings.confirm_destructive_target is False
        assert settings.show_diff_threshold == 20
        assert settings.detect_renames is True
        assert settings.rename_similarity_threshold == 1.0

    def test_custom_settings(self):
        """Test creating settings with custom values."""
        settings = Settings(
            backup_retention_days=60,
            follow_symlinks=True,
            respect_gitignore=False,
        )
        assert settings.backup_retention_days == 60
        assert settings.follow_symlinks is True
        assert settings.respect_gitignore is False


class TestSpecialHandling:
    """Test SpecialHandling dataclass."""

    def test_default_special_handling(self):
        """Test default special handling values."""
        handling = SpecialHandling()
        assert handling.mode == "extract_keys"
        assert handling.include_keys == []
        assert handling.exclude_patterns == []

    def test_custom_special_handling(self):
        """Test creating special handling with custom values."""
        handling = SpecialHandling(
            mode="extract_keys",
            include_keys=["permissions", "settings"],
            exclude_patterns=["*.tmp"],
        )
        assert handling.mode == "extract_keys"
        assert "permissions" in handling.include_keys
        assert "*.tmp" in handling.exclude_patterns


class TestToolConfig:
    """Test ToolConfig dataclass."""

    def test_tool_config_creation(self, tmp_path):
        """Test creating tool configuration."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        tool = ToolConfig(
            name="test_tool",
            enabled=True,
            source=source,
            target=target,
            include=["*.py"],
            exclude=["*.pyc"],
        )

        assert tool.name == "test_tool"
        assert tool.enabled is True
        assert tool.source == source
        assert tool.target == target
        assert "*.py" in tool.include
        assert "*.pyc" in tool.exclude


class TestConfig:
    """Test Config class."""

    def test_from_dict_basic(self, tmp_path):
        """Test creating config from dictionary."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {
                "backup_retention_days": 60,
                "respect_gitignore": True,
            },
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                    "include": ["*.py"],
                    "exclude": ["*.pyc"],
                }
            },
        }

        config = Config.from_dict(config_dict)

        assert config.settings.backup_retention_days == 60
        assert config.settings.respect_gitignore is True
        assert "test_tool" in config.tools
        assert config.tools["test_tool"].enabled is True

    def test_from_dict_with_special_handling(self, tmp_path):
        """Test creating config with special file handling."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                    "include": ["*.json"],
                    "exclude": [],
                    "special_handling": {
                        "settings.json": {
                            "mode": "extract_keys",
                            "include_keys": ["permissions"],
                        }
                    },
                }
            },
        }

        config = Config.from_dict(config_dict)

        assert "settings.json" in config.tools["test_tool"].special_handling
        handling = config.tools["test_tool"].special_handling["settings.json"]
        assert handling.mode == "extract_keys"
        assert "permissions" in handling.include_keys

    def test_from_dict_with_propagation(self, tmp_path):
        """Test creating config with propagation rules."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "tools": {
                "tool1": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                },
                "tool2": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                },
            },
            "propagate": [
                {
                    "source_tool": "tool1",
                    "source_file": "RULES.md",
                    "targets": [
                        {
                            "tool": "tool2",
                            "target_file": "RULES.md",
                            "transforms": [{"type": "sed", "pattern": "s/Tool1/Tool2/g"}],
                        }
                    ],
                }
            ],
        }

        config = Config.from_dict(config_dict)

        assert len(config.propagate) == 1
        rule = config.propagate[0]
        assert rule.source_tool == "tool1"
        assert rule.source_file == "RULES.md"
        assert len(rule.targets) == 1
        assert rule.targets[0].tool == "tool2"

    def test_load_from_file(self, tmp_path):
        """Test loading config from YAML file."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_file = tmp_path / "config.yaml"
        config_data = {
            "settings": {
                "backup_retention_days": 90,
                "respect_gitignore": True,
            },
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                    "include": ["*.md"],
                }
            },
        }

        with open(config_file, "w") as f:
            yaml.dump(config_data, f)

        config = Config.load(config_file)

        assert config.settings.backup_retention_days == 90
        assert "test_tool" in config.tools

    def test_load_nonexistent_file(self, tmp_path):
        """Test loading nonexistent config file."""
        config_file = tmp_path / "nonexistent.yaml"

        with pytest.raises(FileNotFoundError):
            Config.load(config_file)

    def test_validate_no_enabled_tools(self, tmp_path):
        """Test validation with no enabled tools."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "tools": {
                "disabled_tool": {
                    "enabled": False,
                    "source": str(source),
                    "target": str(target),
                }
            },
        }

        config = Config.from_dict(config_dict)
        errors = config.validate()

        assert len(errors) > 0
        assert any("No tools are enabled" in error for error in errors)

    def test_validate_nonexistent_paths(self, tmp_path):
        """Test validation with nonexistent paths."""
        config_dict = {
            "settings": {},
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(tmp_path / "nonexistent_source"),
                    "target": str(tmp_path / "nonexistent_target"),
                }
            },
        }

        config = Config.from_dict(config_dict)
        errors = config.validate()

        assert len(errors) >= 2
        assert any("source path does not exist" in error for error in errors)
        assert any("target path does not exist" in error for error in errors)

    def test_validate_propagation_missing_source(self, tmp_path):
        """Test validation with invalid propagation rule."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "tools": {
                "tool1": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                }
            },
            "propagate": [
                {
                    "targets": [{"tool": "tool1", "target_file": "file.txt"}]
                    # Missing source_tool or source_path
                }
            ],
        }

        config = Config.from_dict(config_dict)
        errors = config.validate()

        assert len(errors) > 0
        assert any(
            "must specify either 'source_tool' or 'source_path'" in error for error in errors
        )

    def test_validate_propagation_unknown_tool(self, tmp_path):
        """Test validation with unknown tool reference."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "tools": {
                "tool1": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                }
            },
            "propagate": [
                {
                    "source_tool": "unknown_tool",
                    "source_file": "file.txt",
                    "targets": [{"tool": "tool1", "target_file": "file.txt"}],
                }
            ],
        }

        config = Config.from_dict(config_dict)
        errors = config.validate()

        assert len(errors) > 0
        assert any("unknown source tool" in error for error in errors)

    def test_default_config_path(self):
        """Test default config path."""
        path = Config.default_config_path()
        assert path == Path.home() / ".sync-agentic-tools.yaml"

    def test_create_template(self):
        """Test creating template config."""
        template = Config.create_template()
        assert isinstance(template, str)
        assert "settings:" in template
        assert "tools:" in template
        assert "respect_gitignore:" in template

    def test_exclude_rulesets_basic(self, tmp_path):
        """Test basic exclude rulesets functionality."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "exclude_rulesets": {
                "common": ["**/.DS_Store", "**/*.log"],
                "private": ["**/private/**"],
            },
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                    "exclude_rulesets": ["common"],
                    "exclude": ["**/*.tmp"],
                }
            },
        }

        config = Config.from_dict(config_dict)

        # Check rulesets are stored
        assert "common" in config.exclude_rulesets
        assert "private" in config.exclude_rulesets

        # Check patterns are merged into tool config
        tool = config.tools["test_tool"]
        assert "**/.DS_Store" in tool.exclude
        assert "**/*.log" in tool.exclude
        assert "**/*.tmp" in tool.exclude
        # Private ruleset shouldn't be included
        assert "**/private/**" not in tool.exclude

    def test_exclude_rulesets_multiple(self, tmp_path):
        """Test using multiple rulesets."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "exclude_rulesets": {
                "common": ["**/.DS_Store"],
                "private": ["**/private/**"],
                "temp": ["**/*.tmp", "**/temp/**"],
            },
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                    "exclude_rulesets": ["common", "private", "temp"],
                }
            },
        }

        config = Config.from_dict(config_dict)
        tool = config.tools["test_tool"]

        # All patterns from all rulesets should be present
        assert "**/.DS_Store" in tool.exclude
        assert "**/private/**" in tool.exclude
        assert "**/*.tmp" in tool.exclude
        assert "**/temp/**" in tool.exclude

    def test_exclude_rulesets_validation(self, tmp_path):
        """Test validation of unknown rulesets."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "exclude_rulesets": {
                "common": ["**/.DS_Store"],
            },
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                    "exclude_rulesets": ["unknown_ruleset"],
                }
            },
        }

        config = Config.from_dict(config_dict)
        errors = config.validate()

        assert len(errors) > 0
        assert any("unknown exclude ruleset" in error for error in errors)

    def test_exclude_rulesets_empty(self, tmp_path):
        """Test tool with no rulesets."""
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        config_dict = {
            "settings": {},
            "exclude_rulesets": {
                "common": ["**/.DS_Store"],
            },
            "tools": {
                "test_tool": {
                    "enabled": True,
                    "source": str(source),
                    "target": str(target),
                    "exclude": ["**/*.tmp"],
                }
            },
        }

        config = Config.from_dict(config_dict)
        tool = config.tools["test_tool"]

        # Only tool-specific excludes should be present
        assert "**/*.tmp" in tool.exclude
        assert "**/.DS_Store" not in tool.exclude
