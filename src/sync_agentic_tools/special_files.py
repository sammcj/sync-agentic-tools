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


def _get_nested(data: dict, dotted_key: str):
    """Traverse *data* following a dot-separated key path.

    Returns (value, True) if the path exists, (None, False) otherwise.
    """
    parts = dotted_key.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None, False
    return current, True


def _set_nested(data: dict, dotted_key: str, value) -> None:
    """Set a value in *data* at the location described by a dot-separated key,
    creating intermediate dicts as needed."""
    parts = dotted_key.split(".")
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def extract_json_keys(
    source_file: Path, include_keys: list[str], exclude_patterns: list[str] | None = None
) -> str:
    """
    Extract specific keys from a JSON/JSONC file.

    Keys may be top-level (e.g. ``"plugin"``) or dot-separated paths into
    nested objects (e.g. ``"provider.llama_cpp.npm"``).

    Args:
        source_file: Path to source JSON/JSONC file
        include_keys: Keys or dotted paths to include
        exclude_patterns: List of patterns to exclude (not yet implemented)

    Returns:
        JSON string with only included keys
    """
    try:
        data = _load_json_or_jsonc(source_file)

        filtered_data: dict = {}
        for key in include_keys:
            value, found = _get_nested(data, key)
            if found:
                _set_nested(filtered_data, key, value)

        return json.dumps(filtered_data, indent=2)

    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Failed to extract keys from {source_file}: {e}")


def merge_json_keys(dest_file: Path, extracted_content: str, include_keys: list[str]) -> None:
    """
    Merge extracted JSON keys into destination file.

    Args:
        dest_file: Path to destination JSON file
        extracted_content: JSON string with keys to merge
        include_keys: List of keys that were extracted
    """
    try:
        # Load existing destination file if it exists
        if dest_file.exists():
            dest_data = _load_json_or_jsonc(dest_file)
        else:
            dest_data = {}

        # Load extracted data
        extracted_data = json.loads(extracted_content)

        # Merge: update only the specified keys (supports dotted paths)
        for key in include_keys:
            value, found = _get_nested(extracted_data, key)
            if found:
                _set_nested(dest_data, key, value)

        # Write back to destination
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_file, "w") as f:
            json.dump(dest_data, f, indent=2)
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
        merge_json_keys(dest_file, extracted_content, include_keys)

        return True
    else:
        raise ValueError(f"Unknown special file mode: {mode}")
