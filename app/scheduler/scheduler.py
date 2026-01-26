"""
Yadro v0 - Scheduler Service

Manages scheduled and recurring tasks.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from .models import Schedule, ScheduleStatus
from ..storage import Database, to_json, from_json, now_iso
from ..kernel import TaskManager


class Scheduler:
    """
    Scheduler - manages scheduled tasks.
    
    Operations:
        - schedule_at(): One-time schedule
        - schedule_cron(): Recurring schedule  
        - schedule_delay(): Schedule after delay
        - cancel(): Cancel schedule
        - pause(): Pause schedule
        - resume(): Resume schedule
        - process_due(): Process due schedules
    """
    
    def __init__(
        self,
        db: Optional[Database] = None,
        task_manager: Optional[TaskManager] = None,
    ):
        """
        Initialize Scheduler.
        
        Args:
            db: Database instance
            task_manager: TaskManager for creating tasks
        """
        self._db = db
        self._task_manager = task_manager
    
    @property
    def db(self) -> Database:
        """Get database instance (lazy init)."""
        if self._db is None:
            self._db = Database()
        return self._db
    
    @property
    def task_manager(self) -> TaskManager:
        """Get task manager (lazy init)."""
        if self._task_manager is None:
            self._task_manager = TaskManager(db=self.db)
        return self._task_manager
    
    # ==================== SCHEDULE OPERATIONS ====================
    
    def schedule_at(
        self,
        user_id: int,
        task_spec: Dict[str, Any],
        run_at: datetime,
    ) -> Schedule:
        """
        Create one-time scheduled task.
        
        Args:
            user_id: User ID
            task_spec: Task template {task_type, input_text, input_data}
            run_at: When to run
            
        Returns:
            Created Schedule
        """
        now = now_iso()
        
        schedule_id = self.db.execute(
            """INSERT INTO schedules 
               (user_id, task_spec, run_at, next_run_at, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                to_json(task_spec),
                run_at.isoformat(),
                run_at.isoformat(),
                ScheduleStatus.PENDING.value,
                now,
                now,
            )
        )
        
        return self.get_schedule(schedule_id)
    
    def schedule_cron(
        self,
        user_id: int,
        task_spec: Dict[str, Any],
        cron: str,
        start_at: Optional[datetime] = None,
    ) -> Schedule:
        """
        Create recurring scheduled task.
        
        Args:
            user_id: User ID
            task_spec: Task template
            cron: Cron expression (e.g., "0 9 * * *" for daily at 9am)
            start_at: When to start (default: now)
            
        Returns:
            Created Schedule
        """
        now = datetime.now(timezone.utc)
        start_at = start_at or now
        
        # Calculate next run time from cron
        next_run = self._get_next_cron_time(cron, start_at)
        
        now_str = now_iso()
        
        schedule_id = self.db.execute(
            """INSERT INTO schedules 
               (user_id, task_spec, cron, next_run_at, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                to_json(task_spec),
                cron,
                next_run.isoformat() if next_run else None,
                ScheduleStatus.PENDING.value,
                now_str,
                now_str,
            )
        )
        
        return self.get_schedule(schedule_id)
    
    def schedule_delay(
        self,
        user_id: int,
        task_spec: Dict[str, Any],
        delay_seconds: int,
    ) -> Schedule:
        """
        Schedule task to run after delay.
        
        Args:
            user_id: User ID
            task_spec: Task template
            delay_seconds: Seconds to wait
            
        Returns:
            Created Schedule
        """
        run_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        return self.schedule_at(user_id, task_spec, run_at)
    
    def cancel(self, schedule_id: int) -> Optional[Schedule]:
        """
        Cancel a schedule.
        
        Args:
            schedule_id: Schedule ID
            
        Returns:
            Updated Schedule or None
        """
        now = now_iso()
        
        self.db.execute(
            """UPDATE schedules 
               SET status = ?, updated_at = ?
               WHERE id = ? AND status = ?""",
            (
                ScheduleStatus.CANCELLED.value,
                now,
                schedule_id,
                ScheduleStatus.PENDING.value,
            )
        )
        
        return self.get_schedule(schedule_id)
    
    def pause(self, schedule_id: int) -> Optional[Schedule]:
        """Pause a schedule."""
        now = now_iso()
        
        self.db.execute(
            """UPDATE schedules 
               SET status = ?, updated_at = ?
               WHERE id = ? AND status = ?""",
            (
                ScheduleStatus.PAUSED.value,
                now,
                schedule_id,
                ScheduleStatus.PENDING.value,
            )
        )
        
        return self.get_schedule(schedule_id)
    
    def resume(self, schedule_id: int) -> Optional[Schedule]:
        """Resume a paused schedule."""
        now = now_iso()
        
        self.db.execute(
            """UPDATE schedules 
               SET status = ?, updated_at = ?
               WHERE id = ? AND status = ?""",
            (
                ScheduleStatus.PENDING.value,
                now,
                schedule_id,
                ScheduleStatus.PAUSED.value,
            )
        )
        
        return self.get_schedule(schedule_id)
    
    # ==================== QUERIES ====================
    
    def get_schedule(self, schedule_id: int) -> Optional[Schedule]:
        """Get schedule by ID."""
        row = self.db.fetch_one(
            "SELECT * FROM schedules WHERE id = ?",
            (schedule_id,)
        )
        return Schedule.from_row(row)
    
    def list_pending(self, user_id: int, limit: int = 50) -> List[Schedule]:
        """List pending schedules for user."""
        rows = self.db.fetch_all(
            """SELECT * FROM schedules 
               WHERE user_id = ? AND status = ?
               ORDER BY next_run_at ASC LIMIT ?""",
            (user_id, ScheduleStatus.PENDING.value, limit)
        )
        return [Schedule.from_row(row) for row in rows]
    
    def list_all(self, user_id: int, limit: int = 50) -> List[Schedule]:
        """List all schedules for user."""
        rows = self.db.fetch_all(
            """SELECT * FROM schedules 
               WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit)
        )
        return [Schedule.from_row(row) for row in rows]
    
    def get_due_schedules(self, now: Optional[datetime] = None) -> List[Schedule]:
        """Get schedules that are due to run."""
        if now is None:
            now = datetime.now(timezone.utc)
        
        rows = self.db.fetch_all(
            """SELECT * FROM schedules 
               WHERE status = ? AND next_run_at <= ?
               ORDER BY next_run_at ASC""",
            (ScheduleStatus.PENDING.value, now.isoformat())
        )
        return [Schedule.from_row(row) for row in rows]
    
    # ==================== PROCESSING ====================
    
    def process_due(self) -> int:
        """
        Process all due schedules.
        
        Creates tasks for due schedules.
        
        Returns:
            Number of tasks created
        """
        due = self.get_due_schedules()
        count = 0
        
        for schedule in due:
            try:
                self._execute_schedule(schedule)
                count += 1
            except Exception as e:
                # Log error but continue processing
                print(f"Error processing schedule {schedule.id}: {e}")
        
        return count
    
    def _execute_schedule(self, schedule: Schedule) -> None:
        """Execute a single schedule."""
        now = datetime.now(timezone.utc)
        now_str = now_iso()
        
        # Create task from spec
        spec = schedule.task_spec
        self.task_manager.enqueue(
            user_id=schedule.user_id,
            task_type=spec.get("task_type", "general"),
            input_text=spec.get("input_text"),
            input_data=spec.get("input_data"),
        )
        
        # Update schedule
        if schedule.is_recurring:
            # Calculate next run time
            next_run = self._get_next_cron_time(schedule.cron, now)
            
            self.db.execute(
                """UPDATE schedules 
                   SET last_run_at = ?, next_run_at = ?, run_count = run_count + 1, updated_at = ?
                   WHERE id = ?""",
                (now_str, next_run.isoformat() if next_run else None, now_str, schedule.id)
            )
        else:
            # One-time schedule - mark as executed
            self.db.execute(
                """UPDATE schedules 
                   SET status = ?, last_run_at = ?, run_count = run_count + 1, updated_at = ?
                   WHERE id = ?""",
                (ScheduleStatus.EXECUTED.value, now_str, now_str, schedule.id)
            )
    
    # ==================== CRON HELPERS ====================
    
    def _get_next_cron_time(
        self, 
        cron: str, 
        after: datetime,
    ) -> Optional[datetime]:
        """
        Calculate next run time from cron expression.
        
        MVP: Simple implementation for common patterns.
        Scale: Use croniter library for full cron support.
        
        Supported patterns:
        - "* * * * *" - every minute
        - "0 * * * *" - every hour
        - "0 9 * * *" - daily at 9:00
        - "30 * * * *" - every hour at :30
        """
        try:
            parts = cron.split()
            if len(parts) != 5:
                return after + timedelta(hours=1)  # Fallback
            
            minute, hour, day, month, weekday = parts
            
            # Every minute
            if cron == "* * * * *":
                return after + timedelta(minutes=1)
            
            # Every hour at specific minute
            if minute.isdigit() and hour == "*" and day == "*" and month == "*":
                target_minute = int(minute)
                next_time = after.replace(minute=target_minute, second=0, microsecond=0)
                if next_time <= after:
                    next_time += timedelta(hours=1)
                return next_time
            
            # Daily at specific time
            if minute.isdigit() and hour.isdigit() and day == "*" and month == "*":
                target_minute = int(minute)
                target_hour = int(hour)
                
                next_time = after.replace(
                    hour=target_hour, 
                    minute=target_minute, 
                    second=0, 
                    microsecond=0
                )
                if next_time <= after:
                    next_time += timedelta(days=1)
                return next_time
            
            # Fallback: 1 hour from now
            return after + timedelta(hours=1)
            
        except Exception:
            return after + timedelta(hours=1)
