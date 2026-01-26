"""
Yadro v0 - Scheduler Models

Data classes for schedules.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from enum import Enum
import json


class ScheduleStatus(str, Enum):
    """Schedule status."""
    PENDING = "pending"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass
class Schedule:
    """
    Schedule entity.
    
    Represents a scheduled or recurring task.
    """
    id: int
    user_id: int
    task_spec: Dict[str, Any]  # Task template
    
    # Timing
    run_at: Optional[datetime] = None  # One-time schedule
    cron: Optional[str] = None  # Recurring (cron expression)
    
    # Status
    status: ScheduleStatus = ScheduleStatus.PENDING
    
    # Tracking
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    run_count: int = 0
    
    # Timestamps
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @property
    def is_recurring(self) -> bool:
        """Check if this is a recurring schedule."""
        return self.cron is not None
    
    @classmethod
    def from_row(cls, row) -> Optional["Schedule"]:
        """Create Schedule from database row."""
        if row is None:
            return None
        
        data = dict(row)
        
        # Parse JSON
        task_spec = data.get("task_spec")
        if isinstance(task_spec, str):
            task_spec = json.loads(task_spec) if task_spec else {}
        
        # Parse status
        status = data.get("status", "pending")
        if isinstance(status, str):
            status = ScheduleStatus(status)
        
        # Parse timestamps
        def parse_ts(val) -> Optional[datetime]:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                val = val.replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(val)
                except ValueError:
                    return None
            return None
        
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            task_spec=task_spec or {},
            run_at=parse_ts(data.get("run_at")),
            cron=data.get("cron"),
            status=status,
            next_run_at=parse_ts(data.get("next_run_at")),
            last_run_at=parse_ts(data.get("last_run_at")),
            run_count=data.get("run_count", 0),
            created_at=parse_ts(data.get("created_at")),
            updated_at=parse_ts(data.get("updated_at")),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "task_spec": self.task_spec,
            "run_at": self.run_at.isoformat() if self.run_at else None,
            "cron": self.cron,
            "status": self.status.value if isinstance(self.status, ScheduleStatus) else self.status,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "run_count": self.run_count,
        }
