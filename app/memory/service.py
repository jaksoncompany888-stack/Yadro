"""
Yadro v0 - Memory Service

Stores and retrieves user memories with full-text search.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from .models import MemoryItem, MemoryType, SearchResult, MemoryContext
from ..storage import Database, to_json, from_json, now_iso


class MemoryService:
    """
    Memory Service - manages user memories.
    
    Features:
    - Store facts, decisions, context
    - Full-text search via SQLite FTS5
    - Build context for LLM
    - Importance-based retrieval
    - Automatic cleanup of old memories
    """
    
    # Limits
    MAX_MEMORIES_PER_USER = 1000
    MAX_CONTEXT_ITEMS = 20
    DEFAULT_SEARCH_LIMIT = 10
    
    def __init__(self, db: Optional[Database] = None):
        """
        Initialize MemoryService.
        
        Args:
            db: Database instance
        """
        self._db = db
    
    @property
    def db(self) -> Database:
        """Get database (lazy init)."""
        if self._db is None:
            self._db = Database()
        return self._db
    
    # ==================== STORE ====================
    
    def store(
        self,
        user_id: int,
        content: str,
        memory_type: MemoryType = MemoryType.CONTEXT,
        source_task_id: Optional[int] = None,
        importance: float = 0.5,
        metadata: Optional[Dict] = None,
    ) -> MemoryItem:
        """
        Store a memory item.
        
        Args:
            user_id: User ID
            content: Memory content
            memory_type: Type of memory
            source_task_id: Related task ID
            importance: Importance score (0-1)
            metadata: Additional metadata
            
        Returns:
            Created MemoryItem
        """
        now = now_iso()
        
        # Check limit
        count = self.db.fetch_value(
            "SELECT COUNT(*) FROM memory_items WHERE user_id = ?",
            (user_id,),
            default=0,
        )
        
        if count >= self.MAX_MEMORIES_PER_USER:
            # Remove oldest low-importance items
            self._cleanup_old_memories(user_id)
        
        # Insert memory
        memory_id = self.db.execute(
            """INSERT INTO memory_items 
               (user_id, memory_type, content, source_task_id, importance, metadata, created_at, accessed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                memory_type.value,
                content,
                source_task_id,
                importance,
                to_json(metadata or {}),
                now,
                now,
            )
        )
        
        # Update FTS index
        self.db.execute(
            """INSERT INTO memory_fts (rowid, content)
               VALUES (?, ?)""",
            (memory_id, content)
        )
        
        return MemoryItem(
            id=memory_id,
            user_id=user_id,
            memory_type=memory_type,
            content=content,
            source_task_id=source_task_id,
            importance=importance,
            metadata=metadata or {},
            created_at=datetime.fromisoformat(now),
            accessed_at=datetime.fromisoformat(now),
        )
    
    def store_fact(self, user_id: int, content: str, **kwargs) -> MemoryItem:
        """Store a user fact (high importance)."""
        return self.store(
            user_id=user_id,
            content=content,
            memory_type=MemoryType.FACT,
            importance=kwargs.get("importance", 0.9),
            **{k: v for k, v in kwargs.items() if k != "importance"},
        )
    
    def store_decision(
        self,
        user_id: int,
        content: str,
        source_task_id: Optional[int] = None,
        **kwargs,
    ) -> MemoryItem:
        """Store a decision."""
        return self.store(
            user_id=user_id,
            content=content,
            memory_type=MemoryType.DECISION,
            source_task_id=source_task_id,
            importance=kwargs.get("importance", 0.7),
            **{k: v for k, v in kwargs.items() if k != "importance"},
        )
    
    def store_task_summary(
        self,
        user_id: int,
        task_id: int,
        summary: str,
        **kwargs,
    ) -> MemoryItem:
        """Store a task summary."""
        return self.store(
            user_id=user_id,
            content=summary,
            memory_type=MemoryType.TASK,
            source_task_id=task_id,
            importance=kwargs.get("importance", 0.6),
            **{k: v for k, v in kwargs.items() if k != "importance"},
        )
    
    # ==================== SEARCH ====================
    
    def search(
        self,
        user_id: int,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        memory_types: Optional[List[MemoryType]] = None,
    ) -> List[SearchResult]:
        """
        Search memories using full-text search.
        
        Args:
            user_id: User ID
            query: Search query
            limit: Max results
            memory_types: Filter by types
            
        Returns:
            List of SearchResult
        """
        # Build query with FTS
        if memory_types:
            type_values = ",".join(f"'{t.value}'" for t in memory_types)
            rows = self.db.fetch_all(
                f"""SELECT m.*, fts.rank
                   FROM memory_items m
                   JOIN memory_fts fts ON m.id = fts.rowid
                   WHERE fts.content MATCH ?
                     AND m.user_id = ?
                     AND m.memory_type IN ({type_values})
                   ORDER BY fts.rank
                   LIMIT ?""",
                (query, user_id, limit)
            )
        else:
            rows = self.db.fetch_all(
                """SELECT m.*, fts.rank
                   FROM memory_items m
                   JOIN memory_fts fts ON m.id = fts.rowid
                   WHERE fts.content MATCH ?
                     AND m.user_id = ?
                   ORDER BY fts.rank
                   LIMIT ?""",
                (query, user_id, limit)
            )
        
        results = []
        for row in rows:
            # Convert sqlite3.Row to dict
            row_dict = dict(row) if not isinstance(row, dict) else row
            item = MemoryItem.from_row(row_dict)
            # FTS rank is negative, convert to positive score
            score = -row_dict.get("rank", 0) if row_dict.get("rank") else 0.5
            results.append(SearchResult(item=item, score=score))
            
            # Update accessed_at
            self._touch(item.id)
        
        return results
    
    def search_simple(
        self,
        user_id: int,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> List[MemoryItem]:
        """
        Simple search returning just items.
        
        Args:
            user_id: User ID
            query: Search query
            limit: Max results
            
        Returns:
            List of MemoryItem
        """
        results = self.search(user_id, query, limit)
        return [r.item for r in results]
    
    # ==================== RETRIEVE ====================
    
    def get(self, memory_id: int) -> Optional[MemoryItem]:
        """Get memory by ID."""
        row = self.db.fetch_one(
            "SELECT * FROM memory_items WHERE id = ?",
            (memory_id,)
        )
        
        if row:
            self._touch(memory_id)
            return MemoryItem.from_row(row)
        return None
    
    def get_by_type(
        self,
        user_id: int,
        memory_type: MemoryType,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> List[MemoryItem]:
        """Get memories by type."""
        rows = self.db.fetch_all(
            """SELECT * FROM memory_items 
               WHERE user_id = ? AND memory_type = ?
               ORDER BY importance DESC, created_at DESC
               LIMIT ?""",
            (user_id, memory_type.value, limit)
        )
        return [MemoryItem.from_row(row) for row in rows]
    
    def get_recent(
        self,
        user_id: int,
        limit: int = DEFAULT_SEARCH_LIMIT,
        hours: int = 24,
    ) -> List[MemoryItem]:
        """Get recent memories."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        
        rows = self.db.fetch_all(
            """SELECT * FROM memory_items 
               WHERE user_id = ? AND created_at >= ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, cutoff, limit)
        )
        return [MemoryItem.from_row(row) for row in rows]
    
    def get_facts(self, user_id: int) -> List[MemoryItem]:
        """Get all user facts."""
        return self.get_by_type(user_id, MemoryType.FACT, limit=50)
    
    # ==================== CONTEXT BUILDING ====================
    
    def build_context(
        self,
        user_id: int,
        query: Optional[str] = None,
        task_type: Optional[str] = None,
    ) -> MemoryContext:
        """
        Build memory context for LLM.
        
        Args:
            user_id: User ID
            query: Optional query for relevance search
            task_type: Optional task type for filtering
            
        Returns:
            MemoryContext with relevant memories
        """
        context = MemoryContext()
        
        # Always get user facts
        context.user_facts = self.get_facts(user_id)
        
        # Get recent decisions
        context.recent_decisions = self.get_by_type(
            user_id, MemoryType.DECISION, limit=5
        )
        
        # Get task history
        context.task_history = self.get_by_type(
            user_id, MemoryType.TASK, limit=3
        )
        
        # If query provided, search for relevant context
        if query:
            results = self.search(user_id, query, limit=5)
            context.relevant_context = [r.item for r in results]
        else:
            # Get recent context
            context.relevant_context = self.get_recent(user_id, limit=5, hours=24)
        
        return context
    
    # ==================== DELETE ====================
    
    def delete(self, memory_id: int) -> bool:
        """Delete memory by ID."""
        # Delete from FTS
        self.db.execute(
            "DELETE FROM memory_fts WHERE rowid = ?",
            (memory_id,)
        )
        
        # Delete from main table
        self.db.execute(
            "DELETE FROM memory_items WHERE id = ?",
            (memory_id,)
        )
        return True
    
    def delete_by_user(self, user_id: int) -> int:
        """Delete all memories for user."""
        # Get IDs for FTS cleanup
        rows = self.db.fetch_all(
            "SELECT id FROM memory_items WHERE user_id = ?",
            (user_id,)
        )
        
        for row in rows:
            self.db.execute(
                "DELETE FROM memory_fts WHERE rowid = ?",
                (row["id"],)
            )
        
        self.db.execute(
            "DELETE FROM memory_items WHERE user_id = ?",
            (user_id,)
        )
        return len(rows)
    
    # ==================== HELPERS ====================
    
    def _touch(self, memory_id: int) -> None:
        """Update accessed_at timestamp."""
        self.db.execute(
            "UPDATE memory_items SET accessed_at = ? WHERE id = ?",
            (now_iso(), memory_id)
        )
    
    def _cleanup_old_memories(self, user_id: int, keep: int = 500) -> int:
        """
        Remove old low-importance memories.
        
        Keeps most recent and high-importance items.
        """
        # Get IDs to delete (oldest, lowest importance)
        rows = self.db.fetch_all(
            """SELECT id FROM memory_items 
               WHERE user_id = ?
               ORDER BY importance ASC, accessed_at ASC
               LIMIT ?""",
            (user_id, self.MAX_MEMORIES_PER_USER - keep)
        )
        
        deleted = 0
        for row in rows:
            self.delete(row["id"])
            deleted += 1
        
        return deleted
    
    def get_stats(self, user_id: int) -> Dict:
        """Get memory statistics for user."""
        total = self.db.fetch_value(
            "SELECT COUNT(*) FROM memory_items WHERE user_id = ?",
            (user_id,),
            default=0,
        )
        
        by_type = {}
        for memory_type in MemoryType:
            count = self.db.fetch_value(
                "SELECT COUNT(*) FROM memory_items WHERE user_id = ? AND memory_type = ?",
                (user_id, memory_type.value),
                default=0,
            )
            by_type[memory_type.value] = count
        
        return {
            "total": total,
            "by_type": by_type,
            "limit": self.MAX_MEMORIES_PER_USER,
        }
