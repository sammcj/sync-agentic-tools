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

        include_paths, traversal_paths = _compute_traversal_paths(include_keys)
        filtered_data = _filter_dict_by_paths(data, include_paths, traversal_paths)

        return json.dumps(filtered_data, indent=2)

    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Failed to extract keys from {source_file}: {e}")


def _merge_dicts_source_order(
    source: dict,
    dest: dict,
    include_paths: set[str] | None = None,
    traversal_paths: set[str] | None = None,
    prefix: str = "",
) -> dict:
    """Merge *dest* into *source*, with source providing both ordering and values.

    Source keys come first in source order.  Dest-only keys are appended
    in their original dest order.

    When *include_paths* and *traversal_paths* are provided, the merge
    distinguishes between:
    - **include paths**: the source value replaces the dest value entirely
      (no recursive merge), because the source is authoritative for these.
    - **traversal paths**: containers that are recursed into so that
      dest-only sibling keys are preserved.
    - **other paths**: recursed into by default for backward compatibility.
    """
    result: dict = {}
    # First pass: source keys in source order (source values win)
    for key in source:
        full_path = f"{prefix}.{key}" if prefix else key
        if key in dest and isinstance(source[key], dict) and isinstance(dest[key], dict):
            # If this is an explicit include path, replace entirely --
            # the source is authoritative for these keys.
            if include_paths and full_path in include_paths:
                result[key] = source[key]
            else:
                result[key] = _merge_dicts_source_order(
                    source[key], dest[key], include_paths, traversal_paths, full_path
                )
        else:
            result[key] = source[key]
    # Second pass: dest-only keys appended in dest order
    for key in dest:
        if key not in result:
            result[key] = dest[key]
    return result


def _compute_traversal_paths(include_keys: list[str]) -> tuple[set[str], set[str]]:
    """Compute include paths and traversal paths from a list of include keys."""
    include_paths = set(include_keys)
    traversal_paths: set[str] = set()
    for path in include_paths:
        parts = path.split(".")
        for i in range(len(parts) - 1):
            traversal_paths.add(".".join(parts[: i + 1]))
    return include_paths, traversal_paths


def merge_json_keys(
    dest_file: Path, extracted_content: str, include_keys: list[str] | None = None
) -> None:
    """
    Merge extracted JSON keys into destination file, preserving key ordering.

    When the destination already exists, its key ordering is kept for
    existing keys and new keys are appended in source order.  Keys that
    are in *include_keys* replace the destination value entirely (the
    source is authoritative); container keys are merged recursively so
    that dest-only sibling keys are preserved.

    Args:
        dest_file: Path to destination JSON file
        extracted_content: JSON string with keys to merge
        include_keys: The original include key paths, used to determine
            which keys should replace vs merge recursively.
    """
    try:
        extracted_data = json.loads(extracted_content)

        if dest_file.exists():
            dest_data = _load_json_or_jsonc(dest_file)
            if include_keys:
                inc_paths, trav_paths = _compute_traversal_paths(include_keys)
            else:
                inc_paths, trav_paths = None, None
            merged = _merge_dicts_source_order(
                extracted_data, dest_data, inc_paths, trav_paths
            )
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

        # Merge into destination, passing include_keys so the merge
        # knows which keys to replace entirely vs merge recursively.
        merge_json_keys(dest_file, extracted_content, include_keys)

        return True
    else:
        raise ValueError(f"Unknown special file mode: {mode}")
