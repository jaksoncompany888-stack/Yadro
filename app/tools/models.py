"""
Yadro v0 - Tool Runtime Models

Data classes for tools and their execution.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any, Dict, List, Callable
from enum import Enum


class ToolImpact(str, Enum):
    """Tool impact level."""
    LOW = "low"          # Read-only, no side effects
    MEDIUM = "medium"    # Moderate changes, reversible
    HIGH = "high"        # Significant changes, may need approval


@dataclass
class ToolSpec:
    """
    Tool specification.
    
    Defines a tool's metadata and behavior.
    """
    name: str
    description: str
    handler: Callable  # Function to execute
    
    # Classification
    impact: ToolImpact = ToolImpact.LOW
    requires_approval: bool = False
    sandbox: bool = False
    
    # Limits
    timeout_seconds: int = 60
    
    # Allowed task types (empty = all)
    allowed_task_types: List[str] = field(default_factory=list)
    
    # Parameters schema (for validation)
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (without handler)."""
        return {
            "name": self.name,
            "description": self.description,
            "impact": self.impact.value,
            "requires_approval": self.requires_approval,
            "sandbox": self.sandbox,
            "timeout_seconds": self.timeout_seconds,
            "allowed_task_types": self.allowed_task_types,
            "parameters": self.parameters,
        }


@dataclass
class ToolResult:
    """
    Result of tool execution.
    """
    success: bool
    data: Any = None
    error: Optional[str] = None
    
    # Execution metadata
    tool_name: Optional[str] = None
    execution_time_ms: Optional[int] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "tool_name": self.tool_name,
            "execution_time_ms": self.execution_time_ms,
        }


@dataclass
class ToolCall:
    """
    Record of a tool call.
    """
    tool_name: str
    parameters: Dict[str, Any]
    result: Optional[ToolResult] = None
    
    # Context
    task_id: Optional[int] = None
    user_id: Optional[int] = None
    step_id: Optional[str] = None
    
    # Timing
    called_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    @property
    def execution_time_ms(self) -> Optional[int]:
        """Calculate execution time in milliseconds."""
        if self.called_at and self.completed_at:
            delta = self.completed_at - self.called_at
            return int(delta.total_seconds() * 1000)
        return None
