"""
Yadro v0 - Memory Service Models

Data classes for memory items and search results.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List
from enum import Enum


class MemoryType(str, Enum):
    """Types of memory items."""
    FACT = "fact"           # User facts (name, preferences)
    DECISION = "decision"   # Decisions made
    CONTEXT = "context"     # Conversation context
    TASK = "task"           # Task summaries
    FEEDBACK = "feedback"   # User feedback


@dataclass
class MemoryItem:
    """
    A single memory item.
    """
    id: Optional[int] = None
    user_id: int = 0
    memory_type: MemoryType = MemoryType.CONTEXT
    content: str = ""
    
    # Metadata
    source_task_id: Optional[int] = None
    importance: float = 0.5  # 0.0 - 1.0
    
    # Timestamps
    created_at: Optional[datetime] = None
    accessed_at: Optional[datetime] = None
    
    # Optional structured data
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "source_task_id": self.source_task_id,
            "importance": self.importance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "accessed_at": self.accessed_at.isoformat() if self.accessed_at else None,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_row(cls, row) -> "MemoryItem":
        """Create from database row."""
        from ..storage import from_json
        
        # Convert sqlite3.Row to dict if needed
        if not isinstance(row, dict):
            row = dict(row)
        
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            source_task_id=row.get("source_task_id"),
            importance=row.get("importance", 0.5),
            created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else None,
            accessed_at=datetime.fromisoformat(row["accessed_at"]) if row.get("accessed_at") else None,
            metadata=from_json(row.get("metadata", "{}")),
        )


@dataclass
class SearchResult:
    """
    Memory search result with relevance score.
    """
    item: MemoryItem
    score: float  # Relevance score
    snippet: Optional[str] = None  # Highlighted snippet
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "item": self.item.to_dict(),
            "score": self.score,
            "snippet": self.snippet,
        }


@dataclass
class MemoryContext:
    """
    Aggregated memory context for LLM.
    """
    user_facts: List[MemoryItem] = field(default_factory=list)
    recent_decisions: List[MemoryItem] = field(default_factory=list)
    relevant_context: List[MemoryItem] = field(default_factory=list)
    task_history: List[MemoryItem] = field(default_factory=list)
    
    def to_prompt(self) -> str:
        """Convert to prompt string for LLM."""
        parts = []
        
        if self.user_facts:
            facts = "\n".join(f"- {m.content}" for m in self.user_facts)
            parts.append(f"User facts:\n{facts}")
        
        if self.recent_decisions:
            decisions = "\n".join(f"- {m.content}" for m in self.recent_decisions[:5])
            parts.append(f"Recent decisions:\n{decisions}")
        
        if self.relevant_context:
            context = "\n".join(f"- {m.content}" for m in self.relevant_context[:5])
            parts.append(f"Relevant context:\n{context}")
        
        if self.task_history:
            history = "\n".join(f"- {m.content}" for m in self.task_history[:3])
            parts.append(f"Recent tasks:\n{history}")
        
        return "\n\n".join(parts) if parts else ""
    
    def is_empty(self) -> bool:
        """Check if context is empty."""
        return not any([
            self.user_facts,
            self.recent_decisions,
            self.relevant_context,
            self.task_history,
        ])
