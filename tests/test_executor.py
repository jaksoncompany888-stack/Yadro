"""
Tests for Layer 3: Executor

Run with: pytest -q
"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.storage import Database, FileStorage
from app.kernel import TaskManager, TaskStatus, PauseReason
from app.executor import (
    Executor, PlanManager, StepExecutor,
    Plan, Step, StepAction, StepStatus as ExecStepStatus, ExecutionContext,
    ApprovalRequired, LimitExceeded,
)


class TestPlanManager:
    """Tests for PlanManager."""
    
    @pytest.fixture
    def pm(self):
        """Create PlanManager."""
        return PlanManager()
    
    def test_build_plan_general(self, pm):
        """Test building general task plan."""
        plan = pm.build_plan(
            task_id=1,
            task_type="general",
            input_text="Test task",
        )
        
        assert plan.plan_id is not None
        assert plan.task_id == 1
        assert len(plan.steps) == 2
        assert plan.steps[0].action == StepAction.LLM_CALL
    
    def test_build_plan_smm(self, pm):
        """Test building SMM task plan."""
        plan = pm.build_plan(
            task_id=1,
            task_type="smm",
            input_text="Write about AI",
            input_data={"channel": "@test"},
        )
        
        assert len(plan.steps) == 4
        
        # Check step types
        actions = [s.action for s in plan.steps]
        assert StepAction.LLM_CALL in actions
        assert StepAction.APPROVAL in actions
        assert StepAction.TOOL_CALL in actions
    
    def test_build_plan_research(self, pm):
        """Test building research task plan."""
        plan = pm.build_plan(
            task_id=1,
            task_type="research",
            input_text="AI trends",
        )
        
        assert len(plan.steps) == 3
        assert plan.steps[0].action == StepAction.TOOL_CALL
    
    def test_build_plan_summary_with_url(self, pm):
        """Test building summary plan with URL."""
        plan = pm.build_plan(
            task_id=1,
            task_type="summary",
            input_data={"url": "https://example.com"},
        )
        
        assert len(plan.steps) == 2
        assert plan.steps[0].action == StepAction.TOOL_CALL
    
    def test_build_plan_summary_without_url(self, pm):
        """Test building summary plan without URL."""
        plan = pm.build_plan(
            task_id=1,
            task_type="summary",
            input_text="Some text to summarize",
        )
        
        assert len(plan.steps) == 1
        assert plan.steps[0].action == StepAction.LLM_CALL
    
    def test_build_plan_unknown_type_uses_general(self, pm):
        """Test unknown task type uses general template."""
        plan = pm.build_plan(
            task_id=1,
            task_type="unknown_type",
            input_text="Test",
        )
        
        assert len(plan.steps) == 2  # Same as general


class TestPlanModel:
    """Tests for Plan model."""
    
    def test_plan_create(self):
        """Test Plan.create generates ID."""
        plan = Plan.create(task_id=1)
        
        assert plan.plan_id is not None
        assert plan.task_id == 1
        assert plan.steps == []
    
    def test_plan_is_complete_empty(self):
        """Test is_complete for empty plan."""
        plan = Plan.create(task_id=1)
        
        assert plan.is_complete is True
    
    def test_plan_is_complete_all_done(self):
        """Test is_complete when all steps done."""
        plan = Plan.create(task_id=1, steps=[
            Step.create(StepAction.LLM_CALL),
            Step.create(StepAction.LLM_CALL),
        ])
        plan.steps[0].status = ExecStepStatus.COMPLETED
        plan.steps[1].status = ExecStepStatus.COMPLETED
        
        assert plan.is_complete is True
    
    def test_plan_is_complete_pending(self):
        """Test is_complete with pending steps."""
        plan = Plan.create(task_id=1, steps=[
            Step.create(StepAction.LLM_CALL),
        ])
        
        assert plan.is_complete is False
    
    def test_plan_has_failed(self):
        """Test has_failed detection."""
        plan = Plan.create(task_id=1, steps=[
            Step.create(StepAction.LLM_CALL),
        ])
        plan.steps[0].status = ExecStepStatus.FAILED
        
        assert plan.has_failed is True
    
    def test_plan_get_step(self):
        """Test get_step by ID."""
        step = Step.create(StepAction.LLM_CALL)
        plan = Plan.create(task_id=1, steps=[step])
        
        found = plan.get_step(step.step_id)
        
        assert found is step
    
    def test_plan_get_next_step_respects_dependencies(self):
        """Test get_next_step respects dependencies."""
        step1 = Step.create(StepAction.LLM_CALL)
        step2 = Step.create(StepAction.LLM_CALL, depends_on=[step1.step_id])
        plan = Plan.create(task_id=1, steps=[step1, step2])
        
        # First should be step1
        next_step = plan.get_next_step()
        assert next_step.step_id == step1.step_id
        
        # After completing step1, should be step2
        step1.status = ExecStepStatus.COMPLETED
        next_step = plan.get_next_step()
        assert next_step.step_id == step2.step_id
    
    def test_plan_to_dict_from_dict(self):
        """Test Plan serialization roundtrip."""
        plan = Plan.create(task_id=1, steps=[
            Step.create(StepAction.LLM_CALL, {"purpose": "test"}),
        ])
        plan.steps[0].status = ExecStepStatus.COMPLETED
        plan.steps[0].result = {"data": "test"}
        
        data = plan.to_dict()
        restored = Plan.from_dict(data)
        
        assert restored.plan_id == plan.plan_id
        assert len(restored.steps) == 1
        assert restored.steps[0].status == ExecStepStatus.COMPLETED
        assert restored.steps[0].result == {"data": "test"}


class TestStepExecutor:
    """Tests for StepExecutor."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def tm(self, db):
        """Create TaskManager."""
        return TaskManager(db=db)
    
    @pytest.fixture
    def se(self, tm):
        """Create StepExecutor."""
        return StepExecutor(task_manager=tm)
    
    @pytest.fixture
    def context(self):
        """Create basic context."""
        plan = Plan.create(task_id=1)
        return ExecutionContext(
            task_id=1,
            user_id=1,
            plan=plan,
            input_text="Test input",
        )
    
    def test_execute_llm_call(self, se, context):
        """Test executing LLM call step."""
        step = Step.create(
            StepAction.LLM_CALL,
            {"purpose": "analyze", "input_text": "Test"},
        )
        
        result = se.execute(step, context)
        
        assert step.status == ExecStepStatus.COMPLETED
        assert result is not None
        assert "response" in result
        assert context.steps_executed == 1
    
    def test_execute_tool_call(self, se, context):
        """Test executing tool call step."""
        step = Step.create(
            StepAction.TOOL_CALL,
            {"tool": "web_search", "query": "test"},
        )
        
        result = se.execute(step, context)
        
        assert step.status == ExecStepStatus.COMPLETED
        assert result["tool"] == "web_search"
    
    def test_execute_stores_result_in_context(self, se, context):
        """Test step result stored in context."""
        step = Step.create(StepAction.LLM_CALL, {"purpose": "test"})
        
        se.execute(step, context)
        
        stored = context.get_step_result(step.step_id)
        assert stored is not None
    
    def test_execute_approval_raises(self, se, context, db):
        """Test approval step raises ApprovalRequired."""
        # Create user for the task
        user_id = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "test")
        )
        
        # Create task
        from app.kernel import TaskManager
        tm = TaskManager(db=db)
        task = tm.enqueue(user_id=user_id, input_text="Test")
        task = tm.claim()
        
        context.task_id = task.id
        context.user_id = user_id
        
        step = Step.create(
            StepAction.APPROVAL,
            {"message": "Please approve"},
        )
        
        with pytest.raises(ApprovalRequired) as exc_info:
            se.execute(step, context)
        
        assert exc_info.value.step_id == step.step_id
    
    def test_execute_failed_step(self, se, context):
        """Test step failure handling."""
        step = Step.create(StepAction.LLM_CALL)
        
        # Mock a failure by using invalid action
        step.action = "invalid"
        
        with pytest.raises(ValueError):
            se.execute(step, context)
        
        # Step stays pending because error happens before execution starts
        assert step.status == ExecStepStatus.PENDING


class TestExecutor:
    """Tests for main Executor."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def fs(self, tmp_path):
        """Create file storage."""
        return FileStorage(tmp_path / "data")
    
    @pytest.fixture
    def tm(self, db):
        """Create TaskManager."""
        return TaskManager(db=db)
    
    @pytest.fixture
    def executor(self, db, tm, fs):
        """Create Executor."""
        return Executor(
            db=db,
            task_manager=tm,
            file_storage=fs,
        )
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123456789, "testuser")
        )
    
    def test_process_one_empty_queue(self, executor):
        """Test process_one returns None for empty queue."""
        result = executor.process_one()
        
        assert result is None
    
    def test_process_one_executes_task(self, executor, tm, user_id):
        """Test process_one executes a task."""
        task = tm.enqueue(
            user_id=user_id,
            task_type="general",
            input_text="Test task",
        )
        
        result = executor.process_one()
        
        assert result is not None
        assert result.status == TaskStatus.SUCCEEDED
        assert result.result is not None
        assert result.result["success"] is True
    
    def test_process_one_smm_pauses_for_approval(self, executor, tm, user_id):
        """Test SMM task pauses at approval step."""
        task = tm.enqueue(
            user_id=user_id,
            task_type="smm",
            input_text="Write post",
            input_data={"channel": "@test"},
        )
        
        result = executor.process_one()
        
        assert result.status == TaskStatus.PAUSED
        assert result.pause_reason == PauseReason.APPROVAL
    
    def test_handle_approval_approved(self, executor, tm, user_id):
        """Test handling approval - approved."""
        task = tm.enqueue(
            user_id=user_id,
            task_type="smm",
            input_text="Write post",
            input_data={"channel": "@test"},
        )
        executor.process_one()  # Pauses at approval
        
        result = executor.handle_approval(task.id, approved=True)
        
        assert result.status == TaskStatus.QUEUED
    
    def test_handle_approval_rejected(self, executor, tm, user_id):
        """Test handling approval - rejected."""
        task = tm.enqueue(
            user_id=user_id,
            task_type="smm",
            input_text="Write post",
            input_data={"channel": "@test"},
        )
        executor.process_one()  # Pauses at approval
        
        result = executor.handle_approval(task.id, approved=False)
        
        assert result.status == TaskStatus.CANCELLED
    
    def test_events_logged(self, executor, tm, user_id):
        """Test execution events are logged."""
        task = tm.enqueue(
            user_id=user_id,
            task_type="general",
            input_text="Test",
        )
        
        executor.process_one()
        
        events = tm.get_task_events(task.id)
        event_types = [e.event_type for e in events]
        
        assert "step_started" in event_types
        assert "step_completed" in event_types


class TestExecutionContext:
    """Tests for ExecutionContext."""
    
    def test_is_over_step_limit(self):
        """Test step limit detection."""
        ctx = ExecutionContext(
            task_id=1,
            user_id=1,
            max_steps=5,
            steps_executed=5,
        )
        
        assert ctx.is_over_step_limit is True
        
        ctx.steps_executed = 4
        assert ctx.is_over_step_limit is False
    
    def test_is_over_time_limit(self):
        """Test time limit detection."""
        ctx = ExecutionContext(
            task_id=1,
            user_id=1,
            max_wall_time_seconds=1,
            start_time=datetime.now(timezone.utc) - timedelta(seconds=2),
        )
        
        assert ctx.is_over_time_limit is True
    
    def test_is_over_time_limit_no_start(self):
        """Test time limit with no start time."""
        ctx = ExecutionContext(
            task_id=1,
            user_id=1,
            start_time=None,
        )
        
        assert ctx.is_over_time_limit is False
    
    def test_add_get_step_result(self):
        """Test storing and retrieving step results."""
        ctx = ExecutionContext(task_id=1, user_id=1)
        
        ctx.add_step_result("step1", {"data": "test"})
        
        result = ctx.get_step_result("step1")
        assert result == {"data": "test"}
        
        # Non-existent step
        assert ctx.get_step_result("nonexistent") is None


class TestStepModel:
    """Tests for Step model."""
    
    def test_step_create(self):
        """Test Step.create generates ID."""
        step = Step.create(StepAction.LLM_CALL)
        
        assert step.step_id is not None
        assert len(step.step_id) == 8
        assert step.action == StepAction.LLM_CALL
        assert step.status == ExecStepStatus.PENDING
    
    def test_step_to_dict_from_dict(self):
        """Test Step serialization roundtrip."""
        step = Step.create(
            StepAction.TOOL_CALL,
            {"tool": "web_search"},
            depends_on=["step1"],
        )
        step.status = ExecStepStatus.COMPLETED
        step.result = {"data": "test"}
        
        data = step.to_dict()
        restored = Step.from_dict(data)
        
        assert restored.step_id == step.step_id
        assert restored.action == StepAction.TOOL_CALL
        assert restored.action_data == {"tool": "web_search"}
        assert restored.depends_on == ["step1"]
        assert restored.status == ExecStepStatus.COMPLETED
        assert restored.result == {"data": "test"}
