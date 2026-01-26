"""
Tests for Layer 2: Task Kernel

Run with: pytest -q
"""
import pytest
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.storage import Database
from app.kernel import TaskManager, Task, TaskStatus, PauseReason, TaskEvent, TaskLimitError


class TestTaskManager:
    """Tests for TaskManager class."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create fresh database for each test."""
        db_path = tmp_path / "test.sqlite3"
        return Database(db_path)
    
    @pytest.fixture
    def tm(self, db):
        """Create TaskManager with test database."""
        return TaskManager(db=db)
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user and return ID."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123456789, "testuser")
        )
    
    # ==================== ENQUEUE TESTS ====================
    
    def test_enqueue_creates_task(self, tm, user_id):
        """Test enqueue creates a task in queued state."""
        task = tm.enqueue(
            user_id=user_id,
            task_type="smm",
            input_text="Write a post about AI",
        )
        
        assert task is not None
        assert task.id > 0
        assert task.user_id == user_id
        assert task.task_type == "smm"
        assert task.input_text == "Write a post about AI"
        assert task.status == TaskStatus.QUEUED
        assert task.attempts == 0
    
    def test_enqueue_with_input_data(self, tm, user_id):
        """Test enqueue with structured input data."""
        task = tm.enqueue(
            user_id=user_id,
            input_text="Test",
            input_data={"channel": "@test", "tags": ["ai", "tech"]},
        )
        
        assert task.input_data == {"channel": "@test", "tags": ["ai", "tech"]}
    
    def test_enqueue_logs_event(self, tm, user_id):
        """Test enqueue creates audit event."""
        task = tm.enqueue(user_id=user_id, input_text="Test")
        
        events = tm.get_task_events(task.id)
        
        assert len(events) == 1
        assert events[0].event_type == "enqueued"
    
    def test_enqueue_custom_max_attempts(self, tm, user_id):
        """Test enqueue with custom max_attempts."""
        task = tm.enqueue(user_id=user_id, input_text="Test", max_attempts=5)
        
        assert task.max_attempts == 5
    
    # ==================== CLAIM TESTS ====================
    
    def test_claim_returns_oldest_task(self, tm, user_id):
        """Test claim returns tasks in FIFO order."""
        task1 = tm.enqueue(user_id=user_id, input_text="First")
        task2 = tm.enqueue(user_id=user_id, input_text="Second")
        
        claimed = tm.claim(worker_id="worker-1")
        
        assert claimed.id == task1.id
    
    def test_claim_sets_running_status(self, tm, user_id):
        """Test claim sets task to running."""
        tm.enqueue(user_id=user_id, input_text="Test")
        
        claimed = tm.claim(worker_id="worker-1")
        
        assert claimed.status == TaskStatus.RUNNING
        assert claimed.locked_by == "worker-1"
        assert claimed.locked_at is not None
        assert claimed.lease_expires_at is not None
    
    def test_claim_increments_attempts(self, tm, user_id):
        """Test claim increments attempt counter."""
        task = tm.enqueue(user_id=user_id, input_text="Test")
        assert task.attempts == 0
        
        claimed = tm.claim()
        
        assert claimed.attempts == 1
    
    def test_claim_empty_queue_returns_none(self, tm, user_id):
        """Test claim returns None when queue is empty."""
        claimed = tm.claim()
        
        assert claimed is None
    
    def test_claim_skips_already_claimed(self, tm, user_id):
        """Test claim doesn't return already claimed tasks."""
        task1 = tm.enqueue(user_id=user_id, input_text="First")
        task2 = tm.enqueue(user_id=user_id, input_text="Second")
        
        claimed1 = tm.claim(worker_id="worker-1")
        claimed2 = tm.claim(worker_id="worker-2")
        claimed3 = tm.claim(worker_id="worker-3")
        
        assert claimed1.id == task1.id
        assert claimed2.id == task2.id
        assert claimed3 is None
    
    def test_claim_logs_event(self, tm, user_id):
        """Test claim creates audit event."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim(worker_id="test-worker")
        
        events = tm.get_task_events(claimed.id)
        event_types = [e.event_type for e in events]
        
        assert "claimed" in event_types
    
    # ==================== HEARTBEAT TESTS ====================
    
    def test_heartbeat_extends_lease(self, tm, user_id):
        """Test heartbeat extends lease expiration."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim(worker_id="worker-1")
        original_lease = claimed.lease_expires_at
        
        time.sleep(0.05)  # Small delay
        success = tm.heartbeat(claimed.id, worker_id="worker-1")
        
        assert success is True
        
        updated = tm.get_task(claimed.id)
        assert updated.lease_expires_at > original_lease
    
    def test_heartbeat_wrong_worker_fails(self, tm, user_id):
        """Test heartbeat fails for wrong worker."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim(worker_id="worker-1")
        
        success = tm.heartbeat(claimed.id, worker_id="wrong-worker")
        
        assert success is False
    
    # ==================== PAUSE/RESUME TESTS ====================
    
    def test_pause_sets_status_and_reason(self, tm, user_id):
        """Test pause sets paused status and reason."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim()
        
        paused = tm.pause(claimed.id, PauseReason.APPROVAL)
        
        assert paused.status == TaskStatus.PAUSED
        assert paused.pause_reason == PauseReason.APPROVAL
        assert paused.locked_by is None  # Lock released
    
    def test_pause_logs_event(self, tm, user_id):
        """Test pause creates audit event."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim()
        tm.pause(claimed.id, PauseReason.APPROVAL)
        
        events = tm.get_task_events(claimed.id)
        event_types = [e.event_type for e in events]
        
        assert "paused" in event_types
    
    def test_resume_returns_to_queue(self, tm, user_id):
        """Test resume returns task to queued state."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim()
        tm.pause(claimed.id, PauseReason.APPROVAL)
        
        resumed = tm.resume(claimed.id)
        
        assert resumed.status == TaskStatus.QUEUED
        assert resumed.pause_reason is None
    
    def test_resume_allows_reclaim(self, tm, user_id):
        """Test resumed task can be claimed again."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim()
        tm.pause(claimed.id, PauseReason.APPROVAL)
        tm.resume(claimed.id)
        
        reclaimed = tm.claim()
        
        assert reclaimed is not None
        assert reclaimed.status == TaskStatus.RUNNING
    
    # ==================== SUCCEED TESTS ====================
    
    def test_succeed_sets_status_and_result(self, tm, user_id):
        """Test succeed sets succeeded status and result."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim()
        
        result_data = {"post_id": 12345, "views": 100}
        succeeded = tm.succeed(claimed.id, result=result_data)
        
        assert succeeded.status == TaskStatus.SUCCEEDED
        assert succeeded.result == result_data
        assert succeeded.completed_at is not None
        assert succeeded.locked_by is None
    
    def test_succeed_logs_event(self, tm, user_id):
        """Test succeed creates audit event."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim()
        tm.succeed(claimed.id, result="done")
        
        events = tm.get_task_events(claimed.id)
        event_types = [e.event_type for e in events]
        
        assert "succeeded" in event_types
    
    # ==================== FAIL TESTS ====================
    
    def test_fail_retries_if_attempts_remaining(self, tm, user_id):
        """Test fail returns to queue if retries available."""
        task = tm.enqueue(user_id=user_id, input_text="Test", max_attempts=3)
        
        claimed = tm.claim()
        assert claimed.attempts == 1
        
        failed = tm.fail(claimed.id, "Connection error")
        
        assert failed.status == TaskStatus.QUEUED
        assert failed.error == "Connection error"
    
    def test_fail_terminal_when_max_attempts_reached(self, tm, user_id):
        """Test fail is terminal when max attempts reached."""
        task = tm.enqueue(user_id=user_id, input_text="Test", max_attempts=2)
        
        # First attempt
        claimed1 = tm.claim()
        tm.fail(claimed1.id, "Error 1")
        
        # Second attempt
        claimed2 = tm.claim()
        failed = tm.fail(claimed2.id, "Error 2")
        
        assert failed.status == TaskStatus.FAILED
        assert failed.completed_at is not None
    
    def test_fail_logs_appropriate_event(self, tm, user_id):
        """Test fail logs retry_scheduled or failed event."""
        # Task with 1 max attempt
        tm.enqueue(user_id=user_id, input_text="Test", max_attempts=1)
        claimed = tm.claim()
        tm.fail(claimed.id, "Error")
        
        events = tm.get_task_events(claimed.id)
        event_types = [e.event_type for e in events]
        
        assert "failed" in event_types
    
    # ==================== CANCEL TESTS ====================
    
    def test_cancel_sets_cancelled_status(self, tm, user_id):
        """Test cancel sets cancelled status."""
        task = tm.enqueue(user_id=user_id, input_text="Test")
        
        cancelled = tm.cancel(task.id, reason="user_request")
        
        assert cancelled.status == TaskStatus.CANCELLED
        assert cancelled.error == "user_request"
    
    def test_cancel_does_not_affect_completed_task(self, tm, user_id):
        """Test cancel doesn't change already completed tasks."""
        tm.enqueue(user_id=user_id, input_text="Test")
        claimed = tm.claim()
        tm.succeed(claimed.id, result="done")
        
        result = tm.cancel(claimed.id)
        
        assert result.status == TaskStatus.SUCCEEDED
    
    def test_cancel_logs_event(self, tm, user_id):
        """Test cancel creates audit event."""
        task = tm.enqueue(user_id=user_id, input_text="Test")
        tm.cancel(task.id)
        
        events = tm.get_task_events(task.id)
        event_types = [e.event_type for e in events]
        
        assert "cancelled" in event_types
    
    # ==================== QUERY TESTS ====================
    
    def test_get_task_returns_none_for_invalid_id(self, tm):
        """Test get_task returns None for non-existent ID."""
        task = tm.get_task(99999)
        
        assert task is None
    
    def test_get_user_tasks_returns_all(self, tm, user_id):
        """Test get_user_tasks returns all user's tasks."""
        tm.enqueue(user_id=user_id, input_text="Task 1")
        tm.enqueue(user_id=user_id, input_text="Task 2")
        tm.enqueue(user_id=user_id, input_text="Task 3")
        
        tasks = tm.get_user_tasks(user_id)
        
        assert len(tasks) == 3
    
    def test_get_user_tasks_filters_by_status(self, tm, user_id):
        """Test get_user_tasks filters by status."""
        t1 = tm.enqueue(user_id=user_id, input_text="Task 1")
        t2 = tm.enqueue(user_id=user_id, input_text="Task 2")
        t3 = tm.enqueue(user_id=user_id, input_text="Task 3")
        
        tm.claim()
        tm.succeed(t1.id, result="done")
        
        queued = tm.get_user_tasks(user_id, status=TaskStatus.QUEUED)
        succeeded = tm.get_user_tasks(user_id, status=TaskStatus.SUCCEEDED)
        
        assert len(queued) == 2
        assert len(succeeded) == 1
    
    def test_get_queue_size(self, tm, user_id):
        """Test get_queue_size returns correct count."""
        tm.enqueue(user_id=user_id, input_text="Task 1")
        tm.enqueue(user_id=user_id, input_text="Task 2")
        
        assert tm.get_queue_size() == 2
        
        tm.claim()
        
        assert tm.get_queue_size() == 1


class TestTaskModel:
    """Tests for Task model."""
    
    def test_task_status_is_terminal(self):
        """Test is_terminal property."""
        assert TaskStatus.SUCCEEDED.is_terminal is True
        assert TaskStatus.FAILED.is_terminal is True
        assert TaskStatus.CANCELLED.is_terminal is True
        assert TaskStatus.RUNNING.is_terminal is False
        assert TaskStatus.QUEUED.is_terminal is False
    
    def test_task_status_is_active(self):
        """Test is_active property."""
        assert TaskStatus.QUEUED.is_active is True
        assert TaskStatus.RUNNING.is_active is True
        assert TaskStatus.PAUSED.is_active is True
        assert TaskStatus.SUCCEEDED.is_active is False
        assert TaskStatus.FAILED.is_active is False
    
    def test_task_to_dict(self, tmp_path):
        """Test Task.to_dict serialization."""
        db = Database(tmp_path / "test.sqlite3")
        tm = TaskManager(db=db)
        user_id = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "test")
        )
        
        task = tm.enqueue(user_id=user_id, input_text="Test")
        task_dict = task.to_dict()
        
        assert task_dict["id"] == task.id
        assert task_dict["status"] == "queued"
        assert task_dict["input_text"] == "Test"


class TestStateMachine:
    """Tests for state machine transitions."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create fresh database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def tm(self, db):
        """Create TaskManager."""
        return TaskManager(db=db)
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "test")
        )
    
    def test_full_success_flow(self, tm, user_id):
        """Test: created → queued → running → succeeded"""
        # Enqueue (created → queued)
        task = tm.enqueue(user_id=user_id, input_text="Test")
        assert task.status == TaskStatus.QUEUED
        
        # Claim (queued → running)
        task = tm.claim()
        assert task.status == TaskStatus.RUNNING
        
        # Succeed (running → succeeded)
        task = tm.succeed(task.id, result="done")
        assert task.status == TaskStatus.SUCCEEDED
    
    def test_pause_resume_flow(self, tm, user_id):
        """Test: running → paused → queued → running"""
        task = tm.enqueue(user_id=user_id, input_text="Test")
        task = tm.claim()
        
        # Pause
        task = tm.pause(task.id, PauseReason.APPROVAL)
        assert task.status == TaskStatus.PAUSED
        
        # Resume
        task = tm.resume(task.id)
        assert task.status == TaskStatus.QUEUED
        
        # Claim again
        task = tm.claim()
        assert task.status == TaskStatus.RUNNING
    
    def test_retry_flow(self, tm, user_id):
        """Test: running → queued (retry) → running → failed"""
        task = tm.enqueue(user_id=user_id, input_text="Test", max_attempts=2)
        
        # First attempt fails
        task = tm.claim()
        task = tm.fail(task.id, "Error")
        assert task.status == TaskStatus.QUEUED
        
        # Second attempt fails
        task = tm.claim()
        task = tm.fail(task.id, "Error again")
        assert task.status == TaskStatus.FAILED
    
    def test_cancel_from_queued(self, tm, user_id):
        """Test: queued → cancelled"""
        task = tm.enqueue(user_id=user_id, input_text="Test")
        task = tm.cancel(task.id)
        
        assert task.status == TaskStatus.CANCELLED
    
    def test_cancel_from_running(self, tm, user_id):
        """Test: running → cancelled"""
        task = tm.enqueue(user_id=user_id, input_text="Test")
        task = tm.claim()
        task = tm.cancel(task.id)
        
        assert task.status == TaskStatus.CANCELLED


class TestTaskManagerSecurity:
    """Tests for TaskManager security features."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create fresh database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "test")
        )
    
    def test_max_queued_per_user(self, db, user_id):
        """Test queued tasks limit per user."""
        tm = TaskManager(db=db, max_queued_per_user=3)
        
        # Create 3 tasks (at limit)
        for i in range(3):
            tm.enqueue(user_id=user_id, input_text=f"Task {i}")
        
        # 4th should fail
        with pytest.raises(TaskLimitError) as exc_info:
            tm.enqueue(user_id=user_id, input_text="Too many")
        
        assert "queued tasks" in str(exc_info.value).lower()
    
    def test_max_active_per_user(self, db, user_id):
        """Test active tasks limit per user."""
        tm = TaskManager(db=db, max_active_per_user=3, max_queued_per_user=10)
        
        # Create 2 queued + 1 running = 3 active
        tm.enqueue(user_id=user_id, input_text="Task 1")
        tm.enqueue(user_id=user_id, input_text="Task 2")
        task3 = tm.enqueue(user_id=user_id, input_text="Task 3")
        tm.claim()  # One becomes running
        
        # 4th should fail (3 active already)
        with pytest.raises(TaskLimitError) as exc_info:
            tm.enqueue(user_id=user_id, input_text="Too many")
        
        assert "active tasks" in str(exc_info.value).lower()
    
    def test_max_tasks_per_hour(self, db, user_id):
        """Test tasks per hour limit."""
        tm = TaskManager(
            db=db, 
            max_tasks_per_hour=3,
            max_queued_per_user=100,
            max_active_per_user=100,
        )
        
        # Create 3 tasks and complete them
        for i in range(3):
            task = tm.enqueue(user_id=user_id, input_text=f"Task {i}")
            tm.claim()
            tm.succeed(task.id, result="done")
        
        # 4th should fail (3 per hour limit)
        with pytest.raises(TaskLimitError) as exc_info:
            tm.enqueue(user_id=user_id, input_text="Too many")
        
        assert "per hour" in str(exc_info.value).lower()
    
    def test_skip_limits_bypasses_checks(self, db, user_id):
        """Test skip_limits parameter."""
        tm = TaskManager(db=db, max_queued_per_user=1)
        
        # First task fills the limit
        tm.enqueue(user_id=user_id, input_text="Task 1")
        
        # Second should fail normally
        with pytest.raises(TaskLimitError):
            tm.enqueue(user_id=user_id, input_text="Task 2")
        
        # But works with skip_limits
        task = tm.enqueue(user_id=user_id, input_text="Task 2", skip_limits=True)
        assert task is not None
    
    def test_get_user_limits_status(self, db, user_id):
        """Test getting user limits status."""
        tm = TaskManager(db=db, max_queued_per_user=10, max_active_per_user=5)
        
        # Create some tasks
        tm.enqueue(user_id=user_id, input_text="Task 1")
        tm.enqueue(user_id=user_id, input_text="Task 2")
        tm.claim()  # One becomes running
        
        status = tm.get_user_limits_status(user_id)
        
        assert status["queued"]["used"] == 1  # One queued
        assert status["queued"]["limit"] == 10
        assert status["active"]["used"] == 2  # 1 queued + 1 running
        assert status["active"]["limit"] == 5
        assert status["per_hour"]["used"] == 2
    
    def test_limits_separate_per_user(self, db):
        """Test limits are enforced per user."""
        user1 = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (111, "user1")
        )
        user2 = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (222, "user2")
        )
        
        tm = TaskManager(db=db, max_queued_per_user=2)
        
        # User1 fills their limit
        tm.enqueue(user_id=user1, input_text="U1 Task 1")
        tm.enqueue(user_id=user1, input_text="U1 Task 2")
        
        # User1 can't add more
        with pytest.raises(TaskLimitError):
            tm.enqueue(user_id=user1, input_text="U1 Task 3")
        
        # But User2 can still add
        task = tm.enqueue(user_id=user2, input_text="U2 Task 1")
        assert task is not None
