"""
Yadro v0 - Cost Tracker

Tracks LLM token usage and costs.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, List

from .models import LLMResponse, ModelConfig
from ..storage import Database, to_json, now_iso


@dataclass
class UsageSummary:
    """Summary of token usage."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    call_count: int = 0
    
    def add(self, response: LLMResponse) -> None:
        """Add response to summary."""
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        self.total_tokens += response.total_tokens
        self.total_cost_usd += response.cost_usd
        self.call_count += 1


class CostTracker:
    """
    Tracks LLM costs and usage.
    
    Features:
    - Per-user tracking
    - Per-task tracking
    - Budget alerts
    - Usage summaries
    """
    
    def __init__(self, db: Optional[Database] = None):
        """
        Initialize CostTracker.
        
        Args:
            db: Database for persistence
        """
        self._db = db
        
        # In-memory cache for current session
        self._user_usage: Dict[int, UsageSummary] = {}
        self._task_usage: Dict[int, UsageSummary] = {}
    
    @property
    def db(self) -> Database:
        """Get database (lazy init)."""
        if self._db is None:
            self._db = Database()
        return self._db
    
    def record(
        self,
        response: LLMResponse,
        user_id: Optional[int] = None,
        task_id: Optional[int] = None,
        operation: str = "llm_call",
    ) -> None:
        """
        Record LLM usage.
        
        Args:
            response: LLM response with usage info
            user_id: Optional user ID
            task_id: Optional task ID
            operation: Operation type
        """
        # Update in-memory cache
        if user_id:
            if user_id not in self._user_usage:
                self._user_usage[user_id] = UsageSummary()
            self._user_usage[user_id].add(response)
        
        if task_id:
            if task_id not in self._task_usage:
                self._task_usage[task_id] = UsageSummary()
            self._task_usage[task_id].add(response)
        
        # Persist to database
        self.db.execute(
            """INSERT INTO costs 
               (user_id, task_id, operation, model, input_tokens, output_tokens, cost_usd, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                task_id,
                operation,
                response.model,
                response.input_tokens,
                response.output_tokens,
                response.cost_usd,
                now_iso(),
            )
        )
    
    def get_user_usage(
        self,
        user_id: int,
        from_date: Optional[datetime] = None,
    ) -> UsageSummary:
        """
        Get usage summary for user.
        
        Args:
            user_id: User ID
            from_date: Optional start date filter
            
        Returns:
            UsageSummary
        """
        if from_date:
            row = self.db.fetch_one(
                """SELECT 
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cost_usd), 0) as cost_usd,
                       COUNT(*) as call_count
                   FROM costs 
                   WHERE user_id = ? AND created_at >= ?""",
                (user_id, from_date.isoformat())
            )
        else:
            row = self.db.fetch_one(
                """SELECT 
                       COALESCE(SUM(input_tokens), 0) as input_tokens,
                       COALESCE(SUM(output_tokens), 0) as output_tokens,
                       COALESCE(SUM(cost_usd), 0) as cost_usd,
                       COUNT(*) as call_count
                   FROM costs 
                   WHERE user_id = ?""",
                (user_id,)
            )
        
        if row:
            return UsageSummary(
                total_input_tokens=row["input_tokens"],
                total_output_tokens=row["output_tokens"],
                total_tokens=row["input_tokens"] + row["output_tokens"],
                total_cost_usd=row["cost_usd"],
                call_count=row["call_count"],
            )
        
        return UsageSummary()
    
    def get_task_usage(self, task_id: int) -> UsageSummary:
        """
        Get usage summary for task.
        
        Args:
            task_id: Task ID
            
        Returns:
            UsageSummary
        """
        row = self.db.fetch_one(
            """SELECT 
                   COALESCE(SUM(input_tokens), 0) as input_tokens,
                   COALESCE(SUM(output_tokens), 0) as output_tokens,
                   COALESCE(SUM(cost_usd), 0) as cost_usd,
                   COUNT(*) as call_count
               FROM costs 
               WHERE task_id = ?""",
            (task_id,)
        )
        
        if row:
            return UsageSummary(
                total_input_tokens=row["input_tokens"],
                total_output_tokens=row["output_tokens"],
                total_tokens=row["input_tokens"] + row["output_tokens"],
                total_cost_usd=row["cost_usd"],
                call_count=row["call_count"],
            )
        
        return UsageSummary()
    
    def check_budget(
        self,
        user_id: int,
        budget_usd: float,
        from_date: Optional[datetime] = None,
    ) -> bool:
        """
        Check if user is within budget.
        
        Args:
            user_id: User ID
            budget_usd: Budget limit
            from_date: Period start date
            
        Returns:
            True if within budget
        """
        usage = self.get_user_usage(user_id, from_date)
        return usage.total_cost_usd < budget_usd
    
    def get_remaining_budget(
        self,
        user_id: int,
        budget_usd: float,
        from_date: Optional[datetime] = None,
    ) -> float:
        """
        Get remaining budget for user.
        
        Args:
            user_id: User ID
            budget_usd: Total budget
            from_date: Period start date
            
        Returns:
            Remaining budget in USD
        """
        usage = self.get_user_usage(user_id, from_date)
        return max(0, budget_usd - usage.total_cost_usd)
    
    def clear_cache(self) -> None:
        """Clear in-memory cache."""
        self._user_usage.clear()
        self._task_usage.clear()
