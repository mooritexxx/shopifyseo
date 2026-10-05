"""Common database exceptions mapped across backends."""
from __future__ import annotations


class DatabaseError(Exception):
    """Base class for all database errors."""
    pass


class IntegrityError(DatabaseError):
    """Constraint violation (unique, foreign key, check, etc.)."""
    pass


class OperationalError(DatabaseError):
    """Database operational error (connection failed, syntax, etc.)."""
    pass


class LockError(OperationalError):
    """Database is locked or busy."""
    pass


class ProgrammingError(DatabaseError):
    """Programming error (invalid SQL, wrong param count, etc.)."""
    pass
