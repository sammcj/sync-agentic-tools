"""Special file handling for agentic-sync."""

import json
import re
from pathlib import Path

# Pattern to strip single-line (//) and multi-line (/* */) comments from JSONC,
# while preserving strings that contain comment-like sequences.
_JSONC_COMMENT_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'  # double-quoted string (skip)
    r"|'(?:[^'\\]|\\.)*'"  # single-quoted string (skip)
    r"|//[^\n]*"  # single-line comment
    r"|/\*[\s\S]*?\*/",  # multi-line comment
)

# Trailing commas before closing } or ]
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _parse_jsonc(text: str) -> dict:
    """Parse JSONC (JSON with Comments) text into a dict.

    Strips // and /* */ comments and trailing commas before delegating to
    the stdlib json parser. Safe to call on plain JSON too.
    """
    stripped = _JSONC_COMMENT_RE.sub(
        lambda m: m.group(0) if m.group(0)[0] in ('"', "'") else "", text
    )
    stripped = _TRAILING_COMMA_RE.sub(r"\1", stripped)
    return json.loads(stripped)


def _load_json_or_jsonc(filepath: Path) -> dict:
    """Read and parse a JSON or JSONC file."""
    with open(filepath) as f:
        text = f.read()
    if filepath.suffix == ".jsonc":
        return _parse_jsonc(text)
    return json.loads(text)


def _filter_dict_by_paths(data: dict, include_paths: set[str], traversal_paths: set[str],
                          prefix: str = "") -> dict:
    """Recursively filter *data* to only include keys matching *include_paths*,
    preserving the original key ordering at every nesting level.

    *traversal_paths* contains ancestor prefixes that need to be traversed
    (e.g. ``"provider"`` for include path ``"provider.llama_cpp.npm"``).
    """
    result: dict = {}
    for key, value in data.items():
        full_path = f"{prefix}.{key}" if prefix else key
        if full_path in include_paths:
            result[key] = value
        elif full_path in traversal_paths and isinstance(value, dict):
            filtered = _filter_dict_by_paths(value, include_paths, traversal_paths, full_path)
            if filtered:
                result[key] = filtered
    return result


def extract_json_keys(
    source_file: Path, include_keys: list[str], exclude_patterns: list[str] | None = None
) -> str:
    """
    Extract specific keys from a JSON/JSONC file.

    Keys may be top-level (e.g. ``"plugin"``) or dot-separated paths into
    nested objects (e.g. ``"provider.llama_cpp.npm"``).

    The output preserves the key ordering of the source file at every
    nesting level.

    Args:
        source_file: Path to source JSON/JSONC file
        include_keys: Keys or dotted paths to include
        exclude_patterns: List of patterns to exclude (not yet implemented)

    Returns:
        JSON string with only included keys
    """
    try:
        data = _load_json_or_jsonc(source_file)

        include_paths = set(include_keys)
        # Pre-compute ancestor paths that need traversal
        traversal_paths: set[str] = set()
        for path in include_paths:
            parts = path.split(".")
            for i in range(len(parts) - 1):
                traversal_paths.add(".".join(parts[: i + 1]))

        filtered_data = _filter_dict_by_paths(data, include_paths, traversal_paths)

        return json.dumps(filtered_data, indent=2)

    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Failed to extract keys from {source_file}: {e}")


def _merge_dicts_source_order(source: dict, dest: dict) -> dict:
    """Merge *dest* into *source*, with source providing both ordering and values.

    Source keys come first in source order.  For shared dict-valued keys
    the merge recurses so that dest-only nested keys are preserved.
    Dest-only top-level keys are appended in their original dest order.
    """
    result: dict = {}
    # First pass: source keys in source order (source values win)
    for key in source:
        if key in dest and isinstance(source[key], dict) and isinstance(dest[key], dict):
            result[key] = _merge_dicts_source_order(source[key], dest[key])
        else:
            result[key] = source[key]
    # Second pass: dest-only keys appended in dest order
    for key in dest:
        if key not in result:
            result[key] = dest[key]
    return result


def merge_json_keys(dest_file: Path, extracted_content: str) -> None:
    """
    Merge extracted JSON keys into destination file, preserving key ordering.

    When the destination already exists, its key ordering is kept for
    existing keys and new keys are appended in source order.  When it
    doesn't exist yet, source ordering is used directly.

    Args:
        dest_file: Path to destination JSON file
        extracted_content: JSON string with keys to merge
    """
    try:
        extracted_data = json.loads(extracted_content)

        if dest_file.exists():
            dest_data = _load_json_or_jsonc(dest_file)
            # Source ordering wins; dest-only keys are appended
            merged = _merge_dicts_source_order(extracted_data, dest_data)
        else:
            # No destination yet -- use extracted data directly so source
            # key ordering is preserved exactly.
            merged = extracted_data

        # Write back to destination
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_file, "w") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")  # Add trailing newline

    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Failed to merge keys into {dest_file}: {e}")


def process_special_file(
    source_file: Path,
    dest_file: Path,
    mode: str,
    include_keys: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> bool:
    """
    Process a file with special handling.

    Args:
        source_file: Source file path
        dest_file: Destination file path
        mode: Processing mode ("extract_keys", "copy", etc.)
        include_keys: Keys to include (for extract_keys mode)
        exclude_patterns: Patterns to exclude

    Returns:
        True if processed successfully
    """
    if mode == "extract_keys":
        if not include_keys:
            raise ValueError("include_keys required for extract_keys mode")

        # Extract keys from source
        extracted_content = extract_json_keys(source_file, include_keys, exclude_patterns)

        # Merge into destination
        merge_json_keys(dest_file, extracted_content)

        return True
    else:
        raise ValueError(f"Unknown special file mode: {mode}")
