"""
Yadro v0 - Tool Registry

Manages tool registration and lookup.
"""
from typing import Optional, Dict, List, Callable

from .models import ToolSpec, ToolImpact


class ToolRegistry:
    """
    Tool Registry - manages available tools.
    
    Operations:
        - register(): Add tool to registry
        - get(): Get tool by name
        - list(): List all tools
        - list_for_task_type(): List tools for specific task type
    """
    
    def __init__(self):
        """Initialize empty registry."""
        self._tools: Dict[str, ToolSpec] = {}
    
    def register(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        impact: ToolImpact = ToolImpact.LOW,
        requires_approval: bool = False,
        sandbox: bool = False,
        timeout_seconds: int = 60,
        allowed_task_types: Optional[List[str]] = None,
        parameters: Optional[Dict] = None,
    ) -> ToolSpec:
        """
        Register a tool.
        
        Args:
            name: Unique tool name
            handler: Function to execute
            description: Tool description
            impact: Impact level
            requires_approval: Whether tool needs approval
            sandbox: Whether tool runs in sandbox
            timeout_seconds: Execution timeout
            allowed_task_types: Task types that can use this tool
            parameters: Parameter schema
            
        Returns:
            Created ToolSpec
        """
        spec = ToolSpec(
            name=name,
            handler=handler,
            description=description,
            impact=impact,
            requires_approval=requires_approval,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            allowed_task_types=allowed_task_types or [],
            parameters=parameters or {},
        )
        
        self._tools[name] = spec
        return spec
    
    def register_spec(self, spec: ToolSpec) -> None:
        """Register a ToolSpec directly."""
        self._tools[spec.name] = spec
    
    def unregister(self, name: str) -> bool:
        """
        Remove tool from registry.
        
        Returns:
            True if removed, False if not found
        """
        if name in self._tools:
            del self._tools[name]
            return True
        return False
    
    def get(self, name: str) -> Optional[ToolSpec]:
        """Get tool by name."""
        return self._tools.get(name)
    
    def exists(self, name: str) -> bool:
        """Check if tool exists."""
        return name in self._tools
    
    def list(self) -> List[ToolSpec]:
        """List all registered tools."""
        return list(self._tools.values())
    
    def list_names(self) -> List[str]:
        """List all tool names."""
        return list(self._tools.keys())
    
    def list_for_task_type(self, task_type: str) -> List[ToolSpec]:
        """
        List tools allowed for specific task type.
        
        Args:
            task_type: Task type to filter by
            
        Returns:
            List of allowed tools
        """
        result = []
        for spec in self._tools.values():
            # Empty allowed_task_types means all types allowed
            if not spec.allowed_task_types or task_type in spec.allowed_task_types:
                result.append(spec)
        return result
    
    def clear(self) -> None:
        """Remove all tools from registry."""
        self._tools.clear()


# Global registry instance
registry = ToolRegistry()
