"""Commit and push a git repository once per day, with safety checks."""

from .sync import MAX_FILE_BYTES, UsageError, sync

__version__ = "1.0.0"
__all__ = ["MAX_FILE_BYTES", "UsageError", "sync", "__version__"]
