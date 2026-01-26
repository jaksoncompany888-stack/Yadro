"""
Yadro v0 - Executor Models

Data classes for execution: Plan, Step, ExecutionContext.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any, List, Dict
from enum import Enum
import uuid
import json


class StepStatus(str, Enum):
    """Step execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepAction(str, Enum):
    """Types of step actions."""
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    APPROVAL = "approval"
    CONDITION = "condition"
    AGGREGATE = "aggregate"


@dataclass
class Step:
    """Single step in execution plan."""
    step_id: str
    action: StepAction
    action_data: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    
    # Execution state
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    error: Optional[str] = None
    
    # Rollback support
    snapshot_ref: Optional[str] = None
    
    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    @classmethod
    def create(
        cls,
        action: StepAction,
        action_data: Optional[Dict] = None,
        depends_on: Optional[List[str]] = None,
    ) -> "Step":
        """Create new step with generated ID."""
        return cls(
            step_id=str(uuid.uuid4())[:8],
            action=action,
            action_data=action_data or {},
            depends_on=depends_on or [],
        )
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage."""
        return {
            "step_id": self.step_id,
            "action": self.action.value,
            "action_data": self.action_data,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "snapshot_ref": self.snapshot_ref,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Step":
        """Create from dictionary."""
        return cls(
            step_id=data["step_id"],
            action=StepAction(data["action"]),
            action_data=data.get("action_data", {}),
            depends_on=data.get("depends_on", []),
            status=StepStatus(data.get("status", "pending")),
            result=data.get("result"),
            error=data.get("error"),
            snapshot_ref=data.get("snapshot_ref"),
        )


@dataclass
class Plan:
    """
    Execution plan for a task.
    
    Contains ordered steps to complete the task.
    """
    plan_id: str
    task_id: int
    steps: List[Step] = field(default_factory=list)
    current_step_index: int = 0
    created_at: Optional[datetime] = None
    
    @classmethod
    def create(cls, task_id: int, steps: Optional[List[Step]] = None) -> "Plan":
        """Create new plan with generated ID."""
        return cls(
            plan_id=str(uuid.uuid4())[:8],
            task_id=task_id,
            steps=steps or [],
            created_at=datetime.now(timezone.utc),
        )
    
    @property
    def current_step(self) -> Optional[Step]:
        """Get current step."""
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None
    
    @property
    def is_complete(self) -> bool:
        """Check if all steps are done."""
        return all(
            s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
            for s in self.steps
        )
    
    @property
    def has_failed(self) -> bool:
        """Check if any step failed."""
        return any(s.status == StepStatus.FAILED for s in self.steps)
    
    def get_step(self, step_id: str) -> Optional[Step]:
        """Get step by ID."""
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None
    
    def get_next_step(self) -> Optional[Step]:
        """Get next pending step (respecting dependencies)."""
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            
            # Check dependencies
            deps_satisfied = all(
                self.get_step(dep_id) and 
                self.get_step(dep_id).status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
                for dep_id in step.depends_on
            )
            
            if deps_satisfied:
                return step
        
        return None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Plan":
        """Create from dictionary."""
        return cls(
            plan_id=data["plan_id"],
            task_id=data["task_id"],
            steps=[Step.from_dict(s) for s in data.get("steps", [])],
            current_step_index=data.get("current_step_index", 0),
        )


@dataclass
class ExecutionContext:
    """
    Context passed through execution.
    
    Contains all state needed for step execution.
    """
    task_id: int
    user_id: int
    plan: Optional[Plan] = None
    
    # Task input
    input_text: Optional[str] = None
    input_data: Dict[str, Any] = field(default_factory=dict)
    
    # Accumulated results from previous steps
    step_results: Dict[str, Any] = field(default_factory=dict)
    
    # Memory context
    memory_context: List[Dict] = field(default_factory=list)
    
    # Execution limits
    steps_executed: int = 0
    max_steps: int = 20
    start_time: Optional[datetime] = None
    max_wall_time_seconds: int = 300
    
    @property
    def is_over_step_limit(self) -> bool:
        """Check if step limit exceeded."""
        return self.steps_executed >= self.max_steps
    
    @property
    def is_over_time_limit(self) -> bool:
        """Check if time limit exceeded."""
        if self.start_time is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return elapsed >= self.max_wall_time_seconds
    
    def add_step_result(self, step_id: str, result: Any) -> None:
        """Store result from completed step."""
        self.step_results[step_id] = result
    
    def get_step_result(self, step_id: str) -> Any:
        """Get result from previous step."""
        return self.step_results.get(step_id)
