"""
Yadro v0 - Task Kernel Models

Data classes for tasks and their states.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any, Dict
from enum import Enum
import json


class TaskStatus(str, Enum):
    """Task state machine states."""
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    
    @property
    def is_terminal(self) -> bool:
        """Check if status is terminal (task finished)."""
        return self in (
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )
    
    @property
    def is_active(self) -> bool:
        """Check if task is active (can be worked on)."""
        return self in (
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.PAUSED,
        )


class PauseReason(str, Enum):
    """Reasons for pausing a task."""
    APPROVAL = "approval"
    DEPENDENCY = "dependency"
    RATE_LIMIT = "rate_limit"


@dataclass
class Task:
    """
    Task entity.
    
    Represents a unit of work in the system.
    """
    id: int
    user_id: int
    task_type: str = "general"
    input_text: Optional[str] = None
    input_data: Dict[str, Any] = field(default_factory=dict)
    
    # State machine
    status: TaskStatus = TaskStatus.CREATED
    pause_reason: Optional[PauseReason] = None
    
    # Retry logic
    attempts: int = 0
    max_attempts: int = 3
    
    # Locking
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    
    # Plan tracking
    current_plan_id: Optional[str] = None
    current_step_id: Optional[str] = None
    
    # Results
    result: Optional[Any] = None
    error: Optional[str] = None
    
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> Optional["Task"]:
        """Create Task from database row."""
        if row is None:
            return None
        
        # Convert sqlite3.Row to dict
        data = dict(row)
        
        # Parse JSON fields
        input_data = data.get("input_data")
        if isinstance(input_data, str):
            input_data = json.loads(input_data) if input_data else {}
        
        result = data.get("result")
        if isinstance(result, str) and result:
            result = json.loads(result)
        
        # Parse enums
        status = data.get("status", "created")
        if isinstance(status, str):
            status = TaskStatus(status)
        
        pause_reason = data.get("pause_reason")
        if pause_reason and isinstance(pause_reason, str):
            pause_reason = PauseReason(pause_reason)
        
        # Parse timestamps
        def parse_ts(val) -> Optional[datetime]:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                # Handle ISO format with or without timezone
                val = val.replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(val)
                except ValueError:
                    return None
            return None
        
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            task_type=data.get("task_type", "general"),
            input_text=data.get("input_text"),
            input_data=input_data or {},
            status=status,
            pause_reason=pause_reason,
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            locked_by=data.get("locked_by"),
            locked_at=parse_ts(data.get("locked_at")),
            lease_expires_at=parse_ts(data.get("lease_expires_at")),
            current_plan_id=data.get("current_plan_id"),
            current_step_id=data.get("current_step_id"),
            result=result,
            error=data.get("error"),
            created_at=parse_ts(data.get("created_at")),
            updated_at=parse_ts(data.get("updated_at")),
            started_at=parse_ts(data.get("started_at")),
            completed_at=parse_ts(data.get("completed_at")),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "task_type": self.task_type,
            "input_text": self.input_text,
            "input_data": self.input_data,
            "status": self.status.value if isinstance(self.status, TaskStatus) else self.status,
            "pause_reason": self.pause_reason.value if self.pause_reason else None,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "locked_by": self.locked_by,
            "current_plan_id": self.current_plan_id,
            "current_step_id": self.current_step_id,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class TaskEvent:
    """
    Task event for audit log.
    
    Records every state change and action.
    """
    id: int
    task_id: int
    event_type: str
    event_data: Dict[str, Any] = field(default_factory=dict)
    step_id: Optional[str] = None
    tool_name: Optional[str] = None
    created_at: Optional[datetime] = None
    
    @classmethod
    def from_row(cls, row) -> Optional["TaskEvent"]:
        """Create TaskEvent from database row."""
        if row is None:
            return None
        
        data = dict(row)
        
        event_data = data.get("event_data")
        if isinstance(event_data, str):
            event_data = json.loads(event_data) if event_data else {}
        
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            event_type=data["event_type"],
            event_data=event_data or {},
            step_id=data.get("step_id"),
            tool_name=data.get("tool_name"),
            created_at=data.get("created_at"),
        )
