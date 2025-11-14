"""Command-line interface for agentic-sync."""

from pathlib import Path

import click

from . import __version__
from .backup import BackupManager
from .config import Config
from .propagate import run_propagation
from .sync import SyncDirection, SyncEngine
from .ui import console, show_error, show_info, show_success, show_warning


@click.group(invoke_without_command=True)
@click.option("--version", is_flag=True, help="Show version and exit")
@click.option("--tool", "-t", default=None, help="Sync specific tool only")
@click.option(
    "--push", "direction", flag_value="push", default=True, help="Push source → target (default)"
)
@click.option("--pull", "direction", flag_value="pull", help="Pull target → source")
@click.option("--bidirectional", "direction", flag_value="sync", help="Bidirectional sync")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would happen without making changes")
@click.option("--auto", "-y", is_flag=True, help="Auto-resolve conflicts using timestamps")
@click.option("--config", "-c", type=click.Path(exists=True), default=None, help="Config file path")
@click.pass_context
def cli(ctx, version, tool, direction, dry_run, auto, config):
    """Agentic Sync - Configuration synchronisation for agentic coding tools.

    By default, runs 'sync --push' to push source → target.

    Use 'sync-agentic-tools sync --help' for detailed sync options.
    """
    if version:
        console.print(f"agentic-sync version {__version__}")
        ctx.exit(0)

    # If no subcommand, run sync (default behaviour)
    if ctx.invoked_subcommand is None:
        ctx.invoke(sync_cmd, tool=tool, direction=direction, dry_run=dry_run, auto=auto, config=config)


@cli.command("sync")
@click.option("--tool", "-t", default=None, help="Sync specific tool only")
@click.option(
    "--push", "direction", flag_value="push", default=True, help="Push source → target (default)"
)
@click.option("--pull", "direction", flag_value="pull", help="Pull target → source")
@click.option("--bidirectional", "direction", flag_value="sync", help="Bidirectional sync")
@click.option("--dry-run", "-n", is_flag=True, help="Show what would happen without making changes")
@click.option("--auto", "-y", is_flag=True, help="Auto-resolve conflicts using timestamps")
@click.option("--config", "-c", type=click.Path(exists=True), default=None, help="Config file path")
def sync_cmd(tool: str | None, direction: str, dry_run: bool, auto: bool, config: str | None):
    """Synchronise tool configurations."""
    try:
        # Load config
        config_path = Path(config) if config is not None else None
        cfg = Config.load(config_path)

        # Validate config
        errors = cfg.validate()
        if errors:
            show_error("Configuration validation failed:")
            for error in errors:
                console.print(f"  - {error}")
            return

        # Show propagation warnings
        warnings = cfg.get_propagation_warnings()
        if warnings:
            for warning in warnings:
                show_warning(warning)

        # Create sync engine
        engine = SyncEngine(cfg, dry_run=dry_run)

        # Determine direction
        sync_direction = {
            "push": SyncDirection.PUSH,
            "pull": SyncDirection.PULL,
            "sync": SyncDirection.SYNC,
        }[direction]

        # Sync specified tool or all enabled tools
        if tool:
            success = engine.sync_tool(tool, sync_direction, auto_resolve=auto)
            if not success:
                raise click.ClickException(f"Sync failed for tool: {tool}")
        else:
            # Sync all enabled tools
            for tool_name, tool_config in cfg.tools.items():
                if tool_config.enabled:
                    engine.sync_tool(tool_name, sync_direction, auto_resolve=auto)

        # Run propagation rules if configured
        if cfg.propagate:
            run_propagation(cfg, dry_run=dry_run)

    except FileNotFoundError as e:
        show_error(str(e))
        raise click.ClickException("Configuration file not found")
    except Exception as e:
        show_error(f"Unexpected error: {e}")
        raise


@cli.command("status")
@click.option("--tool", "-t", default=None, help="Show status for specific tool only")
@click.option("--config", "-c", type=click.Path(exists=True), default=None, help="Config file path")
def status_cmd(tool: str | None, config: str | None):
    """Show sync status without making changes."""
    try:
        config_path = Path(config) if config is not None else None
        cfg = Config.load(config_path)

        # Validate config
        errors = cfg.validate()
        if errors:
            show_error("Configuration validation failed:")
            for error in errors:
                console.print(f"  - {error}")
            return

        # Show propagation warnings
        warnings = cfg.get_propagation_warnings()
        if warnings:
            for warning in warnings:
                show_warning(warning)

        engine = SyncEngine(cfg, dry_run=True)

        if tool:
            engine.sync_tool(tool, SyncDirection.PUSH)
        else:
            for tool_name, tool_config in cfg.tools.items():
                if tool_config.enabled:
                    engine.sync_tool(tool_name, SyncDirection.PUSH)

    except Exception as e:
        show_error(f"Error: {e}")
        raise click.ClickException(str(e))


@cli.command("init-config")
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Output path (default: ~/.sync-agentic-tools.yaml)",
)
@click.option("--force", "-f", is_flag=True, help="Overwrite existing config")
def init_config_cmd(output: str | None, force: bool):
    """Create a template configuration file."""
    try:
        if output:
            config_path = Path(output)
        else:
            config_path = Config.default_config_path()

        if config_path.exists() and not force:
            show_error(f"Config file already exists: {config_path}")
            show_info("Use --force to overwrite")
            return

        # Create template
        template = Config.create_template()

        # Write to file
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            f.write(template)

        show_success(f"Created config file: {config_path}")
        show_info("Edit this file to configure your sync settings")

    except Exception as e:
        show_error(f"Failed to create config: {e}")
        raise click.ClickException(str(e))


@cli.command("list-backups")
@click.option("--tool", "-t", default=None, help="Filter by tool name")
def list_backups_cmd(tool: str | None):
    """List available backups."""
    try:
        backup_manager = BackupManager()
        backups = backup_manager.list_backups(tool)

        if not backups:
            show_info("No backups found")
            return

        from rich.table import Table

        table = Table(title="Available Backups")
        table.add_column("Backup ID", style="cyan")
        table.add_column("Tool", style="magenta")
        table.add_column("Operation", style="yellow")
        table.add_column("Changes", style="green", justify="right")
        table.add_column("Timestamp", style="blue")

        for backup in backups:
            table.add_row(
                backup["id"],
                backup["tool"],
                backup["operation"],
                str(backup["changes"]),
                backup["timestamp"],
            )

        console.print(table)

    except Exception as e:
        show_error(f"Error listing backups: {e}")
        raise click.ClickException(str(e))


@cli.command("restore")
@click.argument("backup_id")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def restore_cmd(backup_id: str, yes: bool):
    """Restore from a backup."""
    try:
        backup_manager = BackupManager()

        if not yes:
            from .ui import confirm_action

            if not confirm_action(f"Restore backup {backup_id}?"):
                show_info("Restore cancelled")
                return

        show_info(f"Restoring backup: {backup_id}")
        manifest = backup_manager.restore_backup(backup_id)

        show_success(f"Restored {len(manifest.changes)} file(s)")

    except FileNotFoundError as e:
        show_error(str(e))
        raise click.ClickException("Backup not found")
    except Exception as e:
        show_error(f"Restore failed: {e}")
        raise click.ClickException(str(e))


@cli.command("clean-backups")
@click.option("--days", "-d", default=30, help="Keep backups newer than N days")
@click.option("--count", "-c", default=30, help="Keep at least N recent backups")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def clean_backups_cmd(days: int, count: int, yes: bool):
    """Clean up old backups."""
    try:
        if not yes:
            from .ui import confirm_action

            if not confirm_action(
                f"Delete backups older than {days} days (keeping {count} recent)?"
            ):
                show_info("Cleanup cancelled")
                return

        backup_manager = BackupManager()
        deleted_count = backup_manager.cleanup_old_backups(days, count)

        show_success(f"Deleted {deleted_count} old backup(s)")

    except Exception as e:
        show_error(f"Cleanup failed: {e}")
        raise click.ClickException(str(e))


def main():
    """Entry point for CLI."""
    cli()


if __name__ == "__main__":
    main()
