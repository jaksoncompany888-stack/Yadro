"""
Yadro v0 - Executor (Layer 3)

The brain of the system - orchestrates task execution.
"""
import time
import threading
from datetime import datetime, timezone
from typing import Optional
import traceback

from .models import Plan, Step, StepStatus, ExecutionContext
from .plan_manager import PlanManager
from .step_executor import StepExecutor, ApprovalRequired
from ..kernel import TaskManager, Task, TaskStatus, PauseReason
from ..storage import Database, to_json, from_json, FileStorage


# Default configuration
DEFAULT_MAX_STEPS = 20
DEFAULT_MAX_WALL_TIME = 300  # 5 minutes
DEFAULT_WORKER_SLEEP = 1  # 1 second


class LimitExceeded(Exception):
    """Raised when execution limits are exceeded."""
    pass


class ExecutionError(Exception):
    """Raised when execution fails."""
    pass


class Executor:
    """
    Main Executor - runs tasks through the agent loop.
    
    Worker Loop:
        - claim task from Kernel
        - run_task
        - succeed / fail / pause
    
    Agent Loop (per task):
        - build plan
        - execute steps
        - check limits
        - handle approvals
    """
    
    def __init__(
        self,
        db: Optional[Database] = None,
        task_manager: Optional[TaskManager] = None,
        plan_manager: Optional[PlanManager] = None,
        step_executor: Optional[StepExecutor] = None,
        file_storage: Optional[FileStorage] = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_wall_time_seconds: int = DEFAULT_MAX_WALL_TIME,
        worker_sleep_seconds: int = DEFAULT_WORKER_SLEEP,
    ):
        """
        Initialize Executor.
        
        Args:
            db: Database instance
            task_manager: TaskManager instance
            plan_manager: PlanManager instance
            step_executor: StepExecutor instance
            file_storage: FileStorage for saving plans
            max_steps: Maximum steps per task
            max_wall_time_seconds: Maximum wall time per task
            worker_sleep_seconds: Sleep time between claims
        """
        self._db = db
        self._task_manager = task_manager
        self._plan_manager = plan_manager or PlanManager()
        self._step_executor = step_executor
        self._file_storage = file_storage
        
        self._max_steps = max_steps
        self._max_wall_time = max_wall_time_seconds
        self._worker_sleep = worker_sleep_seconds
        
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
    
    @property
    def db(self) -> Database:
        """Get database (lazy init)."""
        if self._db is None:
            self._db = Database()
        return self._db
    
    @property
    def task_manager(self) -> TaskManager:
        """Get task manager (lazy init)."""
        if self._task_manager is None:
            self._task_manager = TaskManager(db=self.db)
        return self._task_manager
    
    @property
    def step_executor(self) -> StepExecutor:
        """Get step executor (lazy init)."""
        if self._step_executor is None:
            self._step_executor = StepExecutor(task_manager=self.task_manager)
        return self._step_executor
    
    @property
    def file_storage(self) -> FileStorage:
        """Get file storage (lazy init)."""
        if self._file_storage is None:
            self._file_storage = FileStorage()
        return self._file_storage
    
    # ==================== WORKER LOOP ====================
    
    def start_worker(self, blocking: bool = False) -> None:
        """
        Start worker loop.
        
        Args:
            blocking: If True, run in current thread
        """
        self._running = True
        
        if blocking:
            self._worker_loop()
        else:
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()
    
    def stop_worker(self) -> None:
        """Stop worker loop."""
        self._running = False
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
    
    def _worker_loop(self) -> None:
        """Main worker loop."""
        while self._running:
            try:
                task = self.task_manager.claim()
                
                if task is None:
                    time.sleep(self._worker_sleep)
                    continue
                
                try:
                    self.run_task(task)
                except ApprovalRequired:
                    # Task paused - this is normal flow
                    pass
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {str(e)}"
                    self.task_manager.fail(task.id, error_msg)
                    
            except Exception as e:
                print(f"Worker loop error: {e}")
                traceback.print_exc()
                time.sleep(self._worker_sleep)
    
    def process_one(self) -> Optional[Task]:
        """
        Process single task (for testing).
        
        Returns:
            Processed task or None if queue empty
        """
        task = self.task_manager.claim()
        if task is None:
            return None
        
        try:
            self.run_task(task)
            return self.task_manager.get_task(task.id)
        except ApprovalRequired:
            return self.task_manager.get_task(task.id)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            self.task_manager.fail(task.id, error_msg)
            return self.task_manager.get_task(task.id)
    
    # ==================== AGENT LOOP ====================
    
    def run_task(self, task: Task) -> None:
        """
        Run task through agent loop.
        
        Args:
            task: Task to execute (must be in RUNNING state)
        """
        # Build execution context
        context = self._build_context(task)
        
        # Build or restore plan
        if task.current_plan_id:
            plan = self._restore_plan(task.current_plan_id, task.id)
        else:
            plan = self._plan_manager.build_plan(
                task_id=task.id,
                task_type=task.task_type,
                input_text=task.input_text,
                input_data=task.input_data,
            )
            self._save_plan(plan)
        
        context.plan = plan
        
        # Update task with plan info
        self.task_manager.update_step(task.id, plan.plan_id, None)
        
        # Agent loop
        try:
            result = self._agent_loop(context)
            self.task_manager.succeed(task.id, result=result)
            
        except ApprovalRequired:
            self._save_plan(plan)
            raise
            
        except LimitExceeded as e:
            self.task_manager.fail(task.id, str(e))
            
        except Exception:
            self._save_plan(plan)
            raise
    
    def _agent_loop(self, context: ExecutionContext) -> dict:
        """
        Execute plan steps until complete.
        
        Returns:
            Final result dictionary
        """
        plan = context.plan
        
        while not plan.is_complete:
            # Check safety limits
            self._check_limits(context)
            
            # Heartbeat to extend lease
            self.task_manager.heartbeat(context.task_id)
            
            # Get next step
            step = plan.get_next_step()
            if step is None:
                if plan.has_failed:
                    raise ExecutionError("Plan has failed steps")
                break
            
            # Update task tracking
            self.task_manager.update_step(context.task_id, plan.plan_id, step.step_id)
            
            # Log step start
            self._log_step_event(context.task_id, step, "started")
            
            # Execute step
            try:
                self.step_executor.execute(step, context)
                self._log_step_event(context.task_id, step, "completed")
                
            except ApprovalRequired:
                self._log_step_event(context.task_id, step, "approval_required")
                self._save_plan(plan)
                raise
                
            except Exception as e:
                self._log_step_event(context.task_id, step, "failed", error=str(e))
                raise
        
        return self._build_result(context)
    
    def _check_limits(self, context: ExecutionContext) -> None:
        """Check safety limits."""
        if context.is_over_step_limit:
            raise LimitExceeded(
                f"Step limit exceeded: {context.steps_executed}/{context.max_steps}"
            )
        
        if context.is_over_time_limit:
            elapsed = (datetime.now(timezone.utc) - context.start_time).total_seconds()
            raise LimitExceeded(
                f"Time limit exceeded: {elapsed:.0f}s/{context.max_wall_time_seconds}s"
            )
    
    # ==================== CONTEXT & RESULT ====================
    
    def _build_context(self, task: Task) -> ExecutionContext:
        """Build execution context for task."""
        return ExecutionContext(
            task_id=task.id,
            user_id=task.user_id,
            plan=None,
            input_text=task.input_text,
            input_data=task.input_data,
            max_steps=self._max_steps,
            max_wall_time_seconds=self._max_wall_time,
            start_time=datetime.now(timezone.utc),
        )
    
    def _build_result(self, context: ExecutionContext) -> dict:
        """Build final result from completed plan."""
        plan = context.plan
        
        step_results = {}
        for step in plan.steps:
            if step.status == StepStatus.COMPLETED and step.result:
                step_results[step.step_id] = step.result
        
        last_step = plan.steps[-1] if plan.steps else None
        primary_output = last_step.result if last_step else None
        
        return {
            "success": True,
            "steps_executed": context.steps_executed,
            "primary_output": primary_output,
            "step_results": step_results,
        }
    
    # ==================== PLAN PERSISTENCE ====================
    
    def _save_plan(self, plan: Plan) -> None:
        """Save plan to storage."""
        plan_data = plan.to_dict()
        self.file_storage.save_json(
            plan_data,
            "snapshots",
            f"plan_{plan.plan_id}.json",
        )
        
        # Also save steps to database
        for i, step in enumerate(plan.steps):
            existing = self.db.fetch_one(
                "SELECT id FROM task_steps WHERE task_id = ? AND plan_id = ? AND step_id = ?",
                (plan.task_id, plan.plan_id, step.step_id)
            )
            
            if existing:
                self.db.execute(
                    """UPDATE task_steps 
                       SET status = ?, result = ?, error = ?, snapshot_ref = ?
                       WHERE task_id = ? AND plan_id = ? AND step_id = ?""",
                    (
                        step.status.value,
                        to_json(step.result) if step.result else None,
                        step.error,
                        step.snapshot_ref,
                        plan.task_id,
                        plan.plan_id,
                        step.step_id,
                    )
                )
            else:
                self.db.execute(
                    """INSERT INTO task_steps 
                       (task_id, plan_id, step_id, step_index, action, action_data, status, result, error, snapshot_ref)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        plan.task_id,
                        plan.plan_id,
                        step.step_id,
                        i,
                        step.action.value,
                        to_json(step.action_data),
                        step.status.value,
                        to_json(step.result) if step.result else None,
                        step.error,
                        step.snapshot_ref,
                    )
                )
    
    def _restore_plan(self, plan_id: str, task_id: int) -> Plan:
        """Restore plan from storage."""
        # Try file storage first
        try:
            from .models import StepAction
            
            # Build path to check
            plan_data = None
            
            # Search in snapshots
            files = self.file_storage.list_files("snapshots")
            for f in files:
                if f.name == f"plan_{plan_id}.json":
                    plan_data = self.file_storage.load_json({
                        "ref_id": f.parent.name,
                        "storage_type": "snapshots",
                        "filename": f.name,
                    })
                    break
            
            if plan_data:
                return Plan.from_dict(plan_data)
        except Exception:
            pass
        
        # Fallback: rebuild from database
        rows = self.db.fetch_all(
            """SELECT * FROM task_steps 
               WHERE task_id = ? AND plan_id = ?
               ORDER BY step_index""",
            (task_id, plan_id)
        )
        
        if not rows:
            raise ValueError(f"Plan {plan_id} not found")
        
        steps = []
        for row in rows:
            from .models import StepAction
            steps.append(Step(
                step_id=row["step_id"],
                action=StepAction(row["action"]),
                action_data=from_json(row["action_data"]) or {},
                status=StepStatus(row["status"]),
                result=from_json(row["result"]) if row["result"] else None,
                error=row["error"],
                snapshot_ref=row["snapshot_ref"],
            ))
        
        return Plan(
            plan_id=plan_id,
            task_id=task_id,
            steps=steps,
        )
    
    # ==================== LOGGING ====================
    
    def _log_step_event(
        self,
        task_id: int,
        step: Step,
        event_type: str,
        error: Optional[str] = None,
    ) -> None:
        """Log step execution event."""
        self.task_manager._log_event(
            task_id=task_id,
            event_type=f"step_{event_type}",
            event_data={
                "step_id": step.step_id,
                "action": step.action.value,
                "error": error,
            },
            step_id=step.step_id,
        )
    
    # ==================== APPROVAL HANDLING ====================
    
    def handle_approval(
        self,
        task_id: int,
        approved: bool,
        edited_content: Optional[str] = None,
    ) -> Task:
        """
        Handle user approval response.
        
        Args:
            task_id: Task ID
            approved: Whether user approved
            edited_content: Optional edited content
            
        Returns:
            Updated task
        """
        task = self.task_manager.get_task(task_id)
        if task is None or task.status != TaskStatus.PAUSED:
            raise ValueError(f"Task {task_id} is not paused for approval")
        
        if approved:
            self.task_manager.resume(task_id)
        else:
            self.task_manager.cancel(task_id, reason="user_rejected")
        
        return self.task_manager.get_task(task_id)
