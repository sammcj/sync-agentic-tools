"""File operations for agentic-sync."""

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class FileMetadata:
    """Metadata for a file."""

    path: Path
    checksum: str
    size: int
    mtime: datetime
    relative_path: str

    @classmethod
    def from_file(cls, file_path: Path, base_path: Path) -> "FileMetadata":
        """
        Create metadata from a file.

        Args:
            file_path: Path to file
            base_path: Base path for calculating relative path

        Returns:
            FileMetadata object
        """
        stat = file_path.stat()
        relative_path = str(file_path.relative_to(base_path))

        return cls(
            path=file_path,
            checksum=compute_checksum(file_path),
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime),
            relative_path=relative_path,
        )


def compute_checksum(file_path: Path, algorithm: str = "sha256") -> str:
    """
    Compute checksum of a file.

    Args:
        file_path: Path to file
        algorithm: Hash algorithm (default: sha256)

    Returns:
        Checksum string in format "algorithm:hexdigest"
    """
    hasher = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        # Read in chunks to handle large files
        while chunk := f.read(8192):
            hasher.update(chunk)
    return f"{algorithm}:{hasher.hexdigest()}"


def files_are_identical(file1: Path, file2: Path) -> bool:
    """
    Check if two files are identical by comparing checksums.

    Args:
        file1: First file path
        file2: Second file path

    Returns:
        True if files have same content
    """
    if not file1.exists() or not file2.exists():
        return False

    # Quick size check first
    if file1.stat().st_size != file2.stat().st_size:
        return False

    # Compare checksums
    return compute_checksum(file1) == compute_checksum(file2)


def safe_copy_file(
    source: Path, dest: Path, create_parents: bool = True, backup: bool = False
) -> None:
    """
    Safely copy a file with directory creation and optional backup.

    Args:
        source: Source file path
        dest: Destination file path
        create_parents: Create parent directories if they don't exist
        backup: Create backup of destination if it exists

    Raises:
        FileNotFoundError: If source doesn't exist
        IsADirectoryError: If dest is a directory
    """
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    if dest.is_dir():
        raise IsADirectoryError(f"Destination is a directory: {dest}")

    # Create parent directories
    if create_parents:
        dest.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing destination if requested
    if backup and dest.exists():
        backup_path = dest.with_suffix(dest.suffix + ".bak")
        shutil.copy2(source, backup_path)

    # Copy file preserving metadata
    shutil.copy2(source, dest)


def safe_delete_file(file_path: Path, backup: bool = False) -> None:
    """
    Safely delete a file with optional backup.

    Args:
        file_path: Path to file to delete
        backup: Create backup before deletion

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if backup:
        backup_path = file_path.with_suffix(file_path.suffix + ".deleted")
        shutil.move(str(file_path), str(backup_path))
    else:
        file_path.unlink()


def read_file_lines(file_path: Path) -> list[str]:
    """
    Read file lines for diff generation.

    Args:
        file_path: Path to file

    Returns:
        List of lines (with newlines preserved)
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        # Binary file or different encoding
        return []


def count_lines(file_path: Path) -> int:
    """
    Count lines in a file.

    Args:
        file_path: Path to file

    Returns:
        Number of lines (0 for binary files)
    """
    lines = read_file_lines(file_path)
    return len(lines)


def is_text_file(file_path: Path, sample_size: int = 8192) -> bool:
    """
    Check if file is likely a text file.

    Args:
        file_path: Path to file
        sample_size: Number of bytes to sample

    Returns:
        True if file appears to be text
    """
    try:
        with open(file_path, "rb") as f:
            sample = f.read(sample_size)
            # Check for null bytes (common in binary files)
            if b"\x00" in sample:
                return False
            # Try to decode as UTF-8
            sample.decode("utf-8")
            return True
    except (UnicodeDecodeError, OSError):
        return False
