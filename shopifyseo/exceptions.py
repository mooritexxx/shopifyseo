"""Project-level exception hierarchy — safe to import from anywhere (no project imports)."""


class SyncCancelledError(RuntimeError):
    """Raised when a dashboard sync operation is cancelled by the user."""


class AICancelledError(RuntimeError):
    """Raised when an AI generation job is cancelled by the user."""
