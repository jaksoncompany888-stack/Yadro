"""
Yadro v0 - Task Manager

Core task operations: enqueue, claim, pause, resume, succeed, fail, cancel.
Implements state machine with atomic operations and audit logging.
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Any, Dict

from .models import Task, TaskStatus, PauseReason, TaskEvent
from ..storage import Database, to_json, from_json, now_iso


# Default configuration
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LEASE_TIMEOUT_SECONDS = 300  # 5 minutes

# Security limits
DEFAULT_MAX_QUEUED_PER_USER = 10
DEFAULT_MAX_ACTIVE_PER_USER = 3
DEFAULT_MAX_TASKS_PER_HOUR = 50


class TaskLimitError(Exception):
    """Raised when task limit is exceeded."""
    pass


class TaskManager:
    """
    Task Kernel - manages task lifecycle.
    
    State Machine:
        created → queued → running → paused → succeeded
                           │         │
                           │         ├→ cancelled
                           │         │
                           └→ failed (retry exhausted)
    
    Operations:
        - enqueue(): Create new task
        - claim(): Take task for execution (with lease)
        - heartbeat(): Extend lease
        - pause(reason): Pause task
        - resume(): Resume paused task
        - succeed(result): Complete successfully
        - fail(error): Mark as failed (with retry logic)
        - cancel(): Cancel task
    
    Security:
        - Max queued tasks per user
        - Max active tasks per user
        - Max tasks per hour per user
    """
    
    def __init__(
        self,
        db: Optional[Database] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        lease_timeout_seconds: int = DEFAULT_LEASE_TIMEOUT_SECONDS,
        max_queued_per_user: int = DEFAULT_MAX_QUEUED_PER_USER,
        max_active_per_user: int = DEFAULT_MAX_ACTIVE_PER_USER,
        max_tasks_per_hour: int = DEFAULT_MAX_TASKS_PER_HOUR,
    ):
        """
        Initialize TaskManager.
        
        Args:
            db: Database instance. If None, creates default.
            max_attempts: Default max retry attempts.
            lease_timeout_seconds: How long a claim lease lasts.
            max_queued_per_user: Max queued tasks per user.
            max_active_per_user: Max active (queued+running+paused) tasks per user.
            max_tasks_per_hour: Max tasks created per hour per user.
        """
        self._db = db
        self._max_attempts = max_attempts
        self._lease_timeout = lease_timeout_seconds
        self._max_queued_per_user = max_queued_per_user
        self._max_active_per_user = max_active_per_user
        self._max_tasks_per_hour = max_tasks_per_hour
        self._worker_id = str(uuid.uuid4())[:8]
    
    @property
    def db(self) -> Database:
        """Get database instance (lazy init)."""
        if self._db is None:
            self._db = Database()
        return self._db
    
    # ==================== ENQUEUE ====================
    
    def enqueue(
        self,
        user_id: int,
        task_type: str = "general",
        input_text: Optional[str] = None,
        input_data: Optional[Dict] = None,
        max_attempts: Optional[int] = None,
        skip_limits: bool = False,
    ) -> Task:
        """
        Create new task and add to queue.
        
        Args:
            user_id: User who created the task
            task_type: Type of task
            input_text: Text input from user
            input_data: Additional structured data
            max_attempts: Max retry attempts
            skip_limits: Skip security limits (for system tasks)
            
        Returns:
            Created Task object
            
        Raises:
            TaskLimitError: If user exceeds task limits
        """
        # Check security limits
        if not skip_limits:
            self._check_task_limits(user_id)
        
        if max_attempts is None:
            max_attempts = self._max_attempts
        
        now = now_iso()
        
        task_id = self.db.execute(
            """INSERT INTO tasks 
               (user_id, task_type, input_text, input_data, status, max_attempts, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                task_type,
                input_text,
                to_json(input_data or {}),
                TaskStatus.QUEUED.value,
                max_attempts,
                now,
                now,
            )
        )
        
        self._log_event(task_id, "enqueued", {
            "task_type": task_type,
            "input_text": input_text[:100] if input_text else None,
        })
        
        return self.get_task(task_id)
    
    # ==================== CLAIM ====================
    
    def claim(self, worker_id: Optional[str] = None) -> Optional[Task]:
        """
        Atomically claim next available task.
        
        Args:
            worker_id: ID of worker claiming task
            
        Returns:
            Claimed Task or None if queue empty
        """
        worker_id = worker_id or self._worker_id
        now = datetime.now(timezone.utc)
        lease_expires = now + timedelta(seconds=self._lease_timeout)
        
        with self.db.transaction():
            # Find claimable task (queued, or running with expired lease)
            row = self.db.fetch_one(
                """SELECT id FROM tasks 
                   WHERE (status = ? AND locked_by IS NULL)
                      OR (status = ? AND lease_expires_at < ?)
                   ORDER BY created_at ASC
                   LIMIT 1""",
                (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, now.isoformat())
            )
            
            if row is None:
                return None
            
            task_id = row["id"]
            
            # Claim it
            self.db.execute(
                """UPDATE tasks 
                   SET status = ?, 
                       locked_by = ?, 
                       locked_at = ?,
                       lease_expires_at = ?,
                       attempts = attempts + 1,
                       started_at = COALESCE(started_at, ?),
                       updated_at = ?
                   WHERE id = ?""",
                (
                    TaskStatus.RUNNING.value,
                    worker_id,
                    now.isoformat(),
                    lease_expires.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                )
            )
        
        self._log_event(task_id, "claimed", {
            "worker_id": worker_id,
            "lease_expires_at": lease_expires.isoformat(),
        })
        
        return self.get_task(task_id)
    
    # ==================== HEARTBEAT ====================
    
    def heartbeat(self, task_id: int, worker_id: Optional[str] = None) -> bool:
        """
        Extend lease for running task.
        
        Args:
            task_id: Task ID
            worker_id: Worker ID (must match current lock)
            
        Returns:
            True if successful, False if task not locked by this worker
        """
        worker_id = worker_id or self._worker_id
        now = datetime.now(timezone.utc)
        lease_expires = now + timedelta(seconds=self._lease_timeout)
        
        self.db.execute(
            """UPDATE tasks 
               SET lease_expires_at = ?, updated_at = ?
               WHERE id = ? AND locked_by = ? AND status = ?""",
            (
                lease_expires.isoformat(),
                now.isoformat(),
                task_id,
                worker_id,
                TaskStatus.RUNNING.value,
            )
        )
        
        # Check if update happened
        task = self.get_task(task_id)
        return task is not None and task.locked_by == worker_id
    
    # ==================== PAUSE ====================
    
    def pause(
        self, 
        task_id: int, 
        reason: PauseReason,
        data: Optional[Dict] = None,
    ) -> Optional[Task]:
        """
        Pause running task.
        
        Args:
            task_id: Task ID
            reason: Why task is paused
            data: Additional pause data
            
        Returns:
            Updated Task or None if not found
        """
        now = now_iso()
        
        self.db.execute(
            """UPDATE tasks 
               SET status = ?, 
                   pause_reason = ?,
                   locked_by = NULL,
                   locked_at = NULL,
                   lease_expires_at = NULL,
                   updated_at = ?
               WHERE id = ? AND status = ?""",
            (
                TaskStatus.PAUSED.value,
                reason.value,
                now,
                task_id,
                TaskStatus.RUNNING.value,
            )
        )
        
        self._log_event(task_id, "paused", {
            "reason": reason.value,
            **(data or {}),
        })
        
        return self.get_task(task_id)
    
    # ==================== RESUME ====================
    
    def resume(self, task_id: int) -> Optional[Task]:
        """
        Resume paused task (returns to queue).
        
        Args:
            task_id: Task ID
            
        Returns:
            Updated Task or None if not found
        """
        now = now_iso()
        
        self.db.execute(
            """UPDATE tasks 
               SET status = ?,
                   pause_reason = NULL,
                   updated_at = ?
               WHERE id = ? AND status = ?""",
            (
                TaskStatus.QUEUED.value,
                now,
                task_id,
                TaskStatus.PAUSED.value,
            )
        )
        
        self._log_event(task_id, "resumed", {})
        
        return self.get_task(task_id)
    
    # ==================== SUCCEED ====================
    
    def succeed(self, task_id: int, result: Any = None) -> Optional[Task]:
        """
        Mark task as successfully completed.
        
        Args:
            task_id: Task ID
            result: Task result data
            
        Returns:
            Updated Task or None if not found
        """
        now = now_iso()
        
        self.db.execute(
            """UPDATE tasks 
               SET status = ?,
                   result = ?,
                   locked_by = NULL,
                   locked_at = NULL,
                   lease_expires_at = NULL,
                   completed_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                TaskStatus.SUCCEEDED.value,
                to_json(result),
                now,
                now,
                task_id,
            )
        )
        
        self._log_event(task_id, "succeeded", {
            "result_preview": str(result)[:200] if result else None,
        })
        
        return self.get_task(task_id)
    
    # ==================== FAIL ====================
    
    def fail(self, task_id: int, error: str) -> Optional[Task]:
        """
        Mark task as failed.
        
        If attempts < max_attempts: returns to queue for retry.
        If attempts >= max_attempts: terminal failure.
        
        Args:
            task_id: Task ID
            error: Error message
            
        Returns:
            Updated Task or None if not found
        """
        task = self.get_task(task_id)
        if task is None:
            return None
        
        now = now_iso()
        
        # Check if we should retry
        if task.attempts < task.max_attempts:
            new_status = TaskStatus.QUEUED
            completed_at = None
            self._log_event(task_id, "retry_scheduled", {
                "error": error,
                "attempt": task.attempts,
                "max_attempts": task.max_attempts,
            })
        else:
            new_status = TaskStatus.FAILED
            completed_at = now
            self._log_event(task_id, "failed", {
                "error": error,
                "attempts": task.attempts,
            })
        
        self.db.execute(
            """UPDATE tasks 
               SET status = ?,
                   error = ?,
                   locked_by = NULL,
                   locked_at = NULL,
                   lease_expires_at = NULL,
                   completed_at = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                new_status.value,
                error,
                completed_at,
                now,
                task_id,
            )
        )
        
        return self.get_task(task_id)
    
    # ==================== CANCEL ====================
    
    def cancel(self, task_id: int, reason: str = "user_cancelled") -> Optional[Task]:
        """
        Cancel task.
        
        Cannot cancel already completed tasks.
        
        Args:
            task_id: Task ID
            reason: Cancellation reason
            
        Returns:
            Updated Task or None if not found
        """
        now = now_iso()
        
        self.db.execute(
            """UPDATE tasks 
               SET status = ?,
                   error = ?,
                   locked_by = NULL,
                   locked_at = NULL,
                   lease_expires_at = NULL,
                   completed_at = ?,
                   updated_at = ?
               WHERE id = ? AND status NOT IN (?, ?, ?)""",
            (
                TaskStatus.CANCELLED.value,
                reason,
                now,
                now,
                task_id,
                TaskStatus.SUCCEEDED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            )
        )
        
        self._log_event(task_id, "cancelled", {"reason": reason})
        
        return self.get_task(task_id)
    
    # ==================== QUERIES ====================
    
    def get_task(self, task_id: int) -> Optional[Task]:
        """Get task by ID."""
        row = self.db.fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return Task.from_row(row)
    
    def get_user_tasks(
        self, 
        user_id: int, 
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> List[Task]:
        """Get tasks for user."""
        if status:
            rows = self.db.fetch_all(
                """SELECT * FROM tasks 
                   WHERE user_id = ? AND status = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, status.value, limit)
            )
        else:
            rows = self.db.fetch_all(
                """SELECT * FROM tasks 
                   WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit)
            )
        return [Task.from_row(row) for row in rows]
    
    def get_queue_size(self) -> int:
        """Get number of tasks waiting in queue."""
        return self.db.fetch_value(
            "SELECT COUNT(*) FROM tasks WHERE status = ?",
            (TaskStatus.QUEUED.value,),
            default=0,
        )
    
    def get_task_events(self, task_id: int, limit: int = 100) -> List[TaskEvent]:
        """Get events for task (newest first)."""
        rows = self.db.fetch_all(
            """SELECT * FROM task_events 
               WHERE task_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (task_id, limit)
        )
        return [TaskEvent.from_row(row) for row in rows]
    
    # ==================== HELPERS ====================
    
    def _log_event(
        self, 
        task_id: int, 
        event_type: str, 
        event_data: Dict,
        step_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> int:
        """Log task event to audit trail."""
        return self.db.execute(
            """INSERT INTO task_events 
               (task_id, event_type, event_data, step_id, tool_name, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                event_type,
                to_json(event_data),
                step_id,
                tool_name,
                now_iso(),
            )
        )
    
    def update_step(
        self,
        task_id: int,
        plan_id: str,
        step_id: Optional[str],
    ) -> None:
        """Update current step tracking."""
        self.db.execute(
            """UPDATE tasks 
               SET current_plan_id = ?, current_step_id = ?, updated_at = ?
               WHERE id = ?""",
            (plan_id, step_id, now_iso(), task_id)
        )
    
    # ==================== SECURITY ====================
    
    def _check_task_limits(self, user_id: int) -> None:
        """
        Check task limits for user.
        
        Raises:
            TaskLimitError: If any limit is exceeded
        """
        # Check queued tasks limit
        queued_count = self.db.fetch_value(
            """SELECT COUNT(*) FROM tasks 
               WHERE user_id = ? AND status = ?""",
            (user_id, TaskStatus.QUEUED.value),
            default=0,
        )
        
        if queued_count >= self._max_queued_per_user:
            raise TaskLimitError(
                f"Too many queued tasks: {queued_count}/{self._max_queued_per_user}"
            )
        
        # Check active tasks limit (queued + running only, NOT paused)
        # paused ждут пользователя — не должны блокировать новые задачи
        active_count = self.db.fetch_value(
            """SELECT COUNT(*) FROM tasks
               WHERE user_id = ? AND status IN (?, ?)""",
            (
                user_id,
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
            ),
            default=0,
        )
        
        if active_count >= self._max_active_per_user:
            raise TaskLimitError(
                f"Too many active tasks: {active_count}/{self._max_active_per_user}"
            )
        
        # Check tasks per hour
        hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        tasks_per_hour = self.db.fetch_value(
            """SELECT COUNT(*) FROM tasks 
               WHERE user_id = ? AND created_at >= ?""",
            (user_id, hour_ago),
            default=0,
        )
        
        if tasks_per_hour >= self._max_tasks_per_hour:
            raise TaskLimitError(
                f"Too many tasks per hour: {tasks_per_hour}/{self._max_tasks_per_hour}"
            )
    
    def get_user_limits_status(self, user_id: int) -> Dict:
        """
        Get current limits status for user.
        
        Returns:
            Dict with limit usage info
        """
        queued_count = self.db.fetch_value(
            """SELECT COUNT(*) FROM tasks 
               WHERE user_id = ? AND status = ?""",
            (user_id, TaskStatus.QUEUED.value),
            default=0,
        )
        
        active_count = self.db.fetch_value(
            """SELECT COUNT(*) FROM tasks 
               WHERE user_id = ? AND status IN (?, ?, ?)""",
            (
                user_id,
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
                TaskStatus.PAUSED.value,
            ),
            default=0,
        )
        
        hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        tasks_per_hour = self.db.fetch_value(
            """SELECT COUNT(*) FROM tasks 
               WHERE user_id = ? AND created_at >= ?""",
            (user_id, hour_ago),
            default=0,
        )
        
        return {
            "queued": {
                "used": queued_count,
                "limit": self._max_queued_per_user,
            },
            "active": {
                "used": active_count,
                "limit": self._max_active_per_user,
            },
            "per_hour": {
                "used": tasks_per_hour,
                "limit": self._max_tasks_per_hour,
            },
        }
