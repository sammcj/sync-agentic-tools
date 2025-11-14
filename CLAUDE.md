# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agentic Sync is a configuration synchronisation tool for agentic coding tools (Claude Code, Cline, Cursor, Gemini CLI, etc.). It provides bidirectional sync between source tool directories and target locations with multi-machine awareness, conflict resolution, and cross-tool file propagation with transformations.

## Development Commands

### Installation & Setup
```bash
# Create virtual environment and install in editable mode
uv venv .venv && source .venv/bin/activate
source .venv/bin/activate
uv pip install -e .
```

### Running the Tool
```bash
# Main command (defaults to push mode)
sync-agentic-tools

# Specific sync modes
sync-agentic-tools --push              # Source → target
sync-agentic-tools --pull              # Target → source
sync-agentic-tools --bidirectional     # Two-way sync

# Dry run to see what would happen
sync-agentic-tools --dry-run

# Sync specific tool only
sync-agentic-tools --tool claude

# Check status without changes
sync-agentic-tools status

# Manage backups
sync-agentic-tools list-backups
sync-agentic-tools restore <backup-id>
sync-agentic-tools clean-backups
```

### Configuration
```bash
# Create default config at ~/.sync-agentic-tools.yaml
sync-agentic-tools init-config

# Use custom config file
sync-agentic-tools --config /path/to/config.yaml
```

### Testing
The `tests/` directory exists but is currently empty. No test runner is configured yet.

#### Testing Safely

**IMPORTANT**: During development, always test with safe copies of your configuration directories:

```bash
# Create test copies
mkdir -p ~/test_config_copies
cp -r ~/.claude ~/test_config_copies/claude-test
cp -r ~/Documents/Cline ~/test_config_copies/cline-test

# Update config to point to test copies
# Edit ~/.sync-agentic-tools.yaml to use test paths

# Test sync operations
sync-agentic-tools --dry-run
```

## Architecture

### Core Components

**Three-way merge logic (sync.py)**
- `SyncEngine` orchestrates the sync process with three-way merge semantics
- Compares source, target, and last-known state to detect conflicts and changes
- Handles push (source→target), pull (target→source), and bidirectional sync modes
- Creates `SyncPlan` objects containing files to copy, delete, and conflicts to resolve

**State tracking (state.py)**
- `StateManager` persists sync state in `.sync-state/<machine-id>.json` files
- Stores file checksums, sizes, mtimes, and last sync times per machine
- Tracks deletions with timestamps and user decisions
- Enables multi-machine awareness by storing separate state per machine ID

**Configuration system (config.py)**
- `Config.load()` reads `~/.sync-agentic-tools.yaml` by default
- Each tool has: source path, target path, include/exclude glob patterns
- Special file handling for extracting specific JSON keys (e.g., `settings.json` → only `permissions` key)
- Global settings for backups, safety confirmations, rename detection

**File operations (files.py)**
- Safe file copying with checksumming to detect changes
- Content-based comparison using SHA256 hashes
- Respects glob patterns for inclusion/exclusion

**Backup system (backup.py)**
- Creates timestamped backups before destructive operations
- Stores backup manifests in `~/.agentic-sync-backups/`
- Supports restoration via backup ID
- Auto-cleanup based on age and count retention policies

**Cross-tool propagation (propagate.py)**
- Copies files or directories between tools with transformations in three modes:
  - **Source→source**: Direct copy between tool source directories (fast, no target)
  - **Target→target**: Copy between tool target directories (traditional)
  - **Absolute paths**: Bypass tool config entirely
- Supports recursive directory propagation - all files are copied with transformations applied
- Supports `sed` regex replacements and section removal
- Example: `~/.claude/CLAUDE.md` → `~/.gemini/GEMINI.md` with "Claude Code" → "Gemini CLI"
- Example: `~/.claude/commands/` → `~/Documents/Cline/Workflows/` (entire directory)
- Runs after sync operations complete
- Validation warns if propagated files are also in sync include patterns

**UI layer (ui.py)**
- Rich-based terminal UI with colour-coded output
- Interactive conflict resolution with diff viewing
- Summary tables showing pending changes by category

### Sync Flow

1. Load config from `~/.sync-agentic-tools.yaml`
2. Load state from `.sync-state/<machine-id>.json` in tool's target parent dir
3. Scan source and target directories using glob patterns
4. Compare files using three-way merge (source, target, last-known state)
5. Classify as: new, modified, deleted, or conflicted
6. Show summary and prompt for confirmation if needed
7. Create backup before destructive operations
8. Execute sync plan (copy/delete files)
9. Update state with new checksums and timestamps
10. Run propagation rules if configured

### Key Design Decisions

**State storage location**: State files live in `.sync-state/` directory at the target parent level, allowing all tools to share the same state root while keeping per-tool state separate.

**Multi-machine awareness**: Each machine has its own state file identified by `machine_id` (from MAC address hash), enabling safe multi-machine sync without overwrites.

**Special file handling**: Allows syncing only specific parts of files (e.g., just the `permissions` key from `settings.json`), avoiding syncing unwanted source-specific configuration.

**Propagation transformations**: Enables maintaining derived versions of files across tools (e.g., adapting Claude rules for Cline) with automatic transformations.

## Configuration Structure

Config file at `~/.sync-agentic-tools.yaml` has four main sections:

### settings
Global behaviour: backup retention, safety confirmations, rename detection, diff thresholds

### exclude_rulesets (optional)
Reusable sets of exclude patterns that can be referenced by multiple tools:
- Define named rulesets containing glob patterns
- Tools can reference one or more rulesets via `exclude_rulesets: [ruleset_name]`
- Patterns from rulesets are merged with tool-specific `exclude` patterns
- Eliminates duplication of common exclude patterns across tools

Example:
```yaml
exclude_rulesets:
  common:
    - "**/.DS_Store"
    - "**/*.log"
  private:
    - "skills/private-*/**"
```

### tools
Per-tool configuration with:
- `source`: Source directory path (e.g., `~/.claude`)
- `target`: Target directory path (e.g., `~/git/sammcj/agentic-coding/Claude`)
- `include`: Glob patterns for files to sync
- `exclude`: Glob patterns to ignore (merged with patterns from `exclude_rulesets`)
- `exclude_rulesets`: List of ruleset names to apply (e.g., `["common", "private"]`)
- `special_handling`: File-specific extraction rules

### propagate (optional)
Rules for copying files between tools with transformations:
- **Source specification** (choose one):
  - `source_tool` + `source_file`: Tool-relative path (uses target directory)
  - `source_path`: Absolute path
- **Targets**: List of destinations, each with (choose one):
  - `tool` + `target_file`: Tool-relative path (uses target directory)
  - `dest_path`: Absolute path
- **Transforms**: Optional list of `sed` or `remove_xml_sections` transformations
- **Note**: If propagating to source directories using absolute paths, add files to tool's `exclude` patterns to prevent sync conflicts

## Common Development Tasks

### Adding a new tool
1. Edit `~/.sync-agentic-tools.yaml`
2. Add tool entry with paths and patterns
3. Run `sync-agentic-tools status` to verify
4. Run first sync with `--dry-run` to preview

### Debugging sync issues
1. Use `--dry-run` to see planned operations
2. Check state files in `.sync-state/` directory
3. Verify glob patterns match expected files
4. Review backup manifests in `~/.agentic-sync-backups/`

### Testing with safe copies
Always test with copies of real config directories:
```bash
mkdir -p ~/test_config_copies
cp -r ~/.claude ~/test_config_copies/claude-test
# Update config to point to test paths
sync-agentic-tools --dry-run
```

## Python Specific Notes

- Requires Python 3.13+
- Uses Click for CLI framework
- Uses Rich for terminal UI
- Uses PyYAML for config parsing
- Type hints used throughout with dataclasses
- No tests currently implemented
- Entry point: `sync_agentic_tools.cli:main` defined in pyproject.toml
