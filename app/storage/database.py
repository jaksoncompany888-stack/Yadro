"""
Yadro v0 - Database Module

Thread-safe SQLite database with connection pooling.
"""
import sqlite3
import threading
import json
from datetime import datetime, date
from pathlib import Path
from typing import Any, Optional, List, Dict, Union
from contextlib import contextmanager

from .schema import init_schema


class Database:
    """
    Thread-safe SQLite database manager.
    
    Uses thread-local connections for safety.
    NOT a singleton - create instances as needed, but typically use one per app.
    """
    
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        """
        Initialize database.
        
        Args:
            db_path: Path to database file. If None, uses default from settings.
        """
        if db_path is None:
            from ..config.settings import settings
            db_path = settings.database.path
        
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._local = threading.local()
        self._lock = threading.Lock()
        
        # Initialize schema on first connection
        conn = self._get_connection()
        init_schema(conn)
    
    def _in_transaction(self) -> bool:
        """Check if currently in a transaction."""
        return getattr(self._local, 'in_transaction', False)
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            # Проверяем целостность базы перед подключением
            try:
                conn = sqlite3.connect(
                    str(self._db_path),
                    check_same_thread=False,
                    timeout=5.0,
                )
                conn.row_factory = sqlite3.Row
                # Проверка целостности
                result = conn.execute("PRAGMA integrity_check").fetchone()
                if result[0] != "ok":
                    conn.close()
                    raise sqlite3.DatabaseError("Database corrupted")
            except sqlite3.DatabaseError:
                # База повреждена — удаляем и создаём заново
                import os
                for ext in ['', '-shm', '-wal']:
                    path = str(self._db_path) + ext
                    if os.path.exists(path):
                        os.remove(path)
                conn = sqlite3.connect(
                    str(self._db_path),
                    check_same_thread=False,
                    timeout=5.0,
                )
                conn.row_factory = sqlite3.Row

            # Enable foreign keys, use DELETE mode (safer than WAL on small servers)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = DELETE")  # Безопаснее WAL для t2.micro
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")

            self._local.connection = conn

        return self._local.connection
    
    @property
    def connection(self) -> sqlite3.Connection:
        """Get current thread's connection."""
        return self._get_connection()
    
    def execute(
        self,
        sql: str,
        params: tuple = (),
    ) -> int:
        """
        Execute SQL and return last row id.
        
        Args:
            sql: SQL statement
            params: Query parameters
            
        Returns:
            Last inserted row ID (for INSERT) or rows affected
        """
        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        if not self._in_transaction():
            conn.commit()
        return cursor.lastrowid
    
    def execute_many(
        self,
        sql: str,
        params_list: List[tuple],
    ) -> int:
        """
        Execute SQL for multiple parameter sets.
        
        Args:
            sql: SQL statement
            params_list: List of parameter tuples
            
        Returns:
            Number of rows affected
        """
        conn = self._get_connection()
        cursor = conn.executemany(sql, params_list)
        if not self._in_transaction():
            conn.commit()
        return cursor.rowcount
    
    def fetch_one(
        self,
        sql: str,
        params: tuple = (),
    ) -> Optional[sqlite3.Row]:
        """
        Fetch single row.
        
        Args:
            sql: SQL query
            params: Query parameters
            
        Returns:
            Row or None
        """
        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        return cursor.fetchone()
    
    def fetch_all(
        self,
        sql: str,
        params: tuple = (),
    ) -> List[sqlite3.Row]:
        """
        Fetch all rows.
        
        Args:
            sql: SQL query
            params: Query parameters
            
        Returns:
            List of rows
        """
        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        return cursor.fetchall()
    
    def fetch_value(
        self,
        sql: str,
        params: tuple = (),
        default: Any = None,
    ) -> Any:
        """
        Fetch single value from first column of first row.
        
        Args:
            sql: SQL query
            params: Query parameters
            default: Default value if no result
            
        Returns:
            Value or default
        """
        row = self.fetch_one(sql, params)
        if row is None:
            return default
        return row[0]
    
    @contextmanager
    def transaction(self):
        """
        Context manager for transactions.
        
        Usage:
            with db.transaction():
                db.execute(...)
                db.execute(...)
        """
        conn = self._get_connection()
        self._local.in_transaction = True
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._local.in_transaction = False
    
    def close(self) -> None:
        """Close current thread's connection."""
        if hasattr(self._local, 'connection') and self._local.connection:
            self._local.connection.close()
            self._local.connection = None


# JSON helpers

def to_json(obj: Any) -> str:
    """
    Convert object to JSON string.
    
    Handles datetime objects.
    """
    def default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")
    
    return json.dumps(obj, default=default, ensure_ascii=False)


def from_json(s: Optional[str]) -> Any:
    """
    Parse JSON string.
    
    Returns None for None or empty string.
    """
    if not s:
        return None
    return json.loads(s)


def now_iso() -> str:
    """Get current UTC time as ISO string."""
    from datetime import timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
