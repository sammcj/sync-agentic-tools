"""Configuration management for agentic-sync."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from importlib.resources import files
except ImportError:
    # Python < 3.9 fallback
    from importlib_resources import files


@dataclass
class Settings:
    """Global settings for sync operations."""

    backup_retention_days: int = 30
    backup_retention_count: int = 30
    auto_cleanup_backups: bool = True
    compress_old_backups: bool = True
    follow_symlinks: bool = False
    respect_gitignore: bool = True
    confirm_destructive_source: bool = True
    confirm_destructive_target: bool = False
    show_diff_threshold: int = 100
    detect_renames: bool = True
    rename_similarity_threshold: float = 1.0


@dataclass
class SpecialHandling:
    """Special file handling configuration."""

    mode: str = "extract_keys"
    include_keys: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)


@dataclass
class ToolConfig:
    """Configuration for a single tool."""

    name: str
    enabled: bool
    source: Path
    target: Path
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    exclude_rulesets: list[str] = field(default_factory=list)
    special_handling: dict[str, SpecialHandling] = field(default_factory=dict)


@dataclass
class PropagationTarget:
    """Target for cross-tool propagation."""

    tool: str | None = None
    target_file: str | None = None
    dest_path: str | None = None
    file_pattern: str | None = None
    transforms: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PropagationRule:
    """Rule for cross-tool propagation."""

    source_tool: str | None = None
    source_file: str | None = None
    source_path: str | None = None
    targets: list[PropagationTarget] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Main configuration object."""

    settings: Settings
    tools: dict[str, ToolConfig]
    exclude_rulesets: dict[str, list[str]] = field(default_factory=dict)
    propagate: list[PropagationRule] = field(default_factory=list)

    @classmethod
    def load(cls, config_path: Path | None = None) -> "Config":
        """
        Load configuration from YAML file.

        Args:
            config_path: Path to config file. If None, uses default location.

        Returns:
            Config object

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid
        """
        if config_path is None:
            config_path = cls.default_config_path()

        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found: {config_path}\n"
                f"Create one using: sync-agentic-tools init-config"
            )

        with open(config_path) as f:
            data = yaml.safe_load(f)

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """
        Create Config from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            Config object
        """
        # Parse settings
        settings_data = data.get("settings", {})
        settings = Settings(**settings_data)

        # Parse exclude rulesets
        exclude_rulesets = data.get("exclude_rulesets", {})

        # Parse tools
        tools = {}
        for tool_name, tool_data in data.get("tools", {}).items():
            # Expand paths
            source = Path(tool_data["source"]).expanduser()
            target = Path(tool_data["target"]).expanduser()

            # Parse special handling
            special_handling = {}
            for file_name, handling_data in tool_data.get("special_handling", {}).items():
                special_handling[file_name] = SpecialHandling(**handling_data)

            # Merge exclude patterns from rulesets
            tool_exclude = tool_data.get("exclude", [])
            tool_rulesets = tool_data.get("exclude_rulesets", [])

            # Collect patterns from referenced rulesets
            merged_exclude = []
            for ruleset_name in tool_rulesets:
                if ruleset_name in exclude_rulesets:
                    merged_exclude.extend(exclude_rulesets[ruleset_name])

            # Add tool-specific excludes
            merged_exclude.extend(tool_exclude)

            tools[tool_name] = ToolConfig(
                name=tool_name,
                enabled=tool_data.get("enabled", True),
                source=source,
                target=target,
                include=tool_data.get("include", []),
                exclude=merged_exclude,
                exclude_rulesets=tool_rulesets,
                special_handling=special_handling,
            )

        # Parse propagation rules
        propagate = []
        for rule_data in data.get("propagate", []):
            targets = []
            for target_data in rule_data.get("targets", []):
                # Filter out exclude from target_data as it belongs to the rule level
                filtered_target_data = {k: v for k, v in target_data.items() if k != "exclude"}
                targets.append(PropagationTarget(**filtered_target_data))

            propagate.append(
                PropagationRule(
                    source_tool=rule_data.get("source_tool"),
                    source_file=rule_data.get("source_file"),
                    source_path=rule_data.get("source_path"),
                    targets=targets,
                    exclude=rule_data.get("exclude", []),
                )
            )

        return cls(
            settings=settings,
            tools=tools,
            exclude_rulesets=exclude_rulesets,
            propagate=propagate,
        )

    @staticmethod
    def default_config_path() -> Path:
        """Get default configuration file path."""
        return Path.home() / ".sync-agentic-tools.yaml"

    def validate(self) -> list[str]:
        """
        Validate configuration.

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []

        # Check that at least one tool is enabled
        if not any(tool.enabled for tool in self.tools.values()):
            errors.append("No tools are enabled in configuration")

        # Check that paths exist and rulesets are valid
        for tool_name, tool in self.tools.items():
            if tool.enabled:
                if not tool.source.exists():
                    errors.append(f"Tool '{tool_name}': source path does not exist: {tool.source}")
                if not tool.target.exists():
                    errors.append(f"Tool '{tool_name}': target path does not exist: {tool.target}")

            # Validate referenced exclude rulesets exist
            for ruleset_name in tool.exclude_rulesets:
                if ruleset_name not in self.exclude_rulesets:
                    errors.append(
                        f"Tool '{tool_name}': references unknown exclude ruleset '{ruleset_name}'"
                    )

        # Check propagation rules
        for i, rule in enumerate(self.propagate):
            # Must have either source_tool or source_path
            if not rule.source_tool and not rule.source_path:
                errors.append(
                    f"Propagation rule {i}: must specify either 'source_tool' or 'source_path'"
                )

            # If source_tool specified, validate it exists
            if rule.source_tool and rule.source_tool not in self.tools:
                errors.append(
                    f"Propagation rule {i}: references unknown source tool: {rule.source_tool}"
                )

            # Validate targets
            for j, target in enumerate(rule.targets):
                # Check if target_file looks like an absolute path
                if target.target_file and (
                    target.target_file.startswith("/") or target.target_file.startswith("~")
                ):
                    if target.tool:
                        errors.append(
                            f"Propagation rule {i}, target {j}: 'target_file' appears to be an absolute path ('{target.target_file}') "
                            f"but 'tool' is also specified. Use 'dest_path' for absolute paths, or remove 'tool' prefix from path."
                        )

                # Must have either (tool + target_file) or dest_path
                if not target.dest_path:
                    if not target.tool:
                        errors.append(
                            f"Propagation rule {i}, target {j}: must specify either 'dest_path' (for absolute paths) "
                            f"or 'tool' + 'target_file' (for tool-relative paths)"
                        )
                    elif not target.target_file:
                        errors.append(
                            f"Propagation rule {i}, target {j}: 'tool' is specified but 'target_file' is missing"
                        )

                # If tool specified, validate it exists
                if target.tool and target.tool not in self.tools:
                    errors.append(
                        f"Propagation rule {i}, target {j}: references unknown tool '{target.tool}'. "
                        f"Either define this tool in the 'tools' section, or use 'dest_path' with an absolute path instead."
                    )

        return errors

    def get_propagation_warnings(self) -> list[str]:
        """
        Get warnings about potential issues with propagation rules.

        Returns:
            List of warning messages
        """
        warnings = []

        # Check if propagation targets are also included in tool sync patterns
        for rule in self.propagate:
            for target in rule.targets:
                # Only check tool-based targets
                if not target.tool:
                    continue

                tool = self.tools.get(target.tool)
                if not tool or not tool.enabled:
                    continue

                # Get the target file path
                target_file = target.target_file or target.dest_path
                if not target_file:
                    continue

                # Check if this file would be synced
                from .utils import matches_patterns

                if matches_patterns(target_file, tool.include, tool.exclude):
                    warnings.append(
                        f"Tool '{target.tool}': file '{target_file}' is a propagation target "
                        f"but also matches sync include patterns. "
                        f"Consider adding it to exclude patterns to prevent conflicts."
                    )

        return warnings

    @staticmethod
    def create_template() -> str:
        """
        Create a template configuration file content.

        Reads from the bundled default-config.yaml template file.
        """
        try:
            # Try to read from package resources
            template_file = files("sync_agentic_tools.templates").joinpath("default-config.yaml")
            return template_file.read_text()
        except Exception:
            # Fallback: try relative to this file
            template_path = Path(__file__).parent / "templates" / "default-config.yaml"
            if template_path.exists():
                return template_path.read_text()
            else:
                raise FileNotFoundError(
                    "Could not find default-config.yaml template file. "
                    "Please reinstall the package."
                )
