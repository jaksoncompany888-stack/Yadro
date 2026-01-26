"""
Tests for Layer 2b: Scheduler

Run with: pytest -q
"""
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.storage import Database
from app.kernel import TaskManager, TaskStatus
from app.scheduler import Scheduler, Schedule, ScheduleStatus


class TestScheduler:
    """Tests for Scheduler class."""
    
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
    def scheduler(self, db, tm):
        """Create Scheduler with test database."""
        return Scheduler(db=db, task_manager=tm)
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user and return ID."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123456789, "testuser")
        )
    
    # ==================== SCHEDULE_AT TESTS ====================
    
    def test_schedule_at_creates_schedule(self, scheduler, user_id):
        """Test schedule_at creates a pending schedule."""
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        
        schedule = scheduler.schedule_at(
            user_id=user_id,
            task_spec={"task_type": "smm", "input_text": "Test post"},
            run_at=run_at,
        )
        
        assert schedule is not None
        assert schedule.id > 0
        assert schedule.user_id == user_id
        assert schedule.status == ScheduleStatus.PENDING
        assert schedule.task_spec["task_type"] == "smm"
        assert schedule.is_recurring is False
    
    def test_schedule_at_sets_next_run(self, scheduler, user_id):
        """Test schedule_at sets next_run_at correctly."""
        run_at = datetime.now(timezone.utc) + timedelta(hours=2)
        
        schedule = scheduler.schedule_at(
            user_id=user_id,
            task_spec={"input_text": "Test"},
            run_at=run_at,
        )
        
        assert schedule.next_run_at is not None
        # Compare timestamps (allow small delta for test execution time)
        delta = abs((schedule.next_run_at - run_at).total_seconds())
        assert delta < 2
    
    # ==================== SCHEDULE_DELAY TESTS ====================
    
    def test_schedule_delay_creates_future_schedule(self, scheduler, user_id):
        """Test schedule_delay creates schedule in the future."""
        before = datetime.now(timezone.utc)
        
        schedule = scheduler.schedule_delay(
            user_id=user_id,
            task_spec={"input_text": "Delayed task"},
            delay_seconds=3600,  # 1 hour
        )
        
        assert schedule.next_run_at > before
        assert schedule.next_run_at < before + timedelta(hours=2)
    
    # ==================== SCHEDULE_CRON TESTS ====================
    
    def test_schedule_cron_creates_recurring(self, scheduler, user_id):
        """Test schedule_cron creates recurring schedule."""
        schedule = scheduler.schedule_cron(
            user_id=user_id,
            task_spec={"task_type": "daily_report"},
            cron="0 9 * * *",
        )
        
        assert schedule.cron == "0 9 * * *"
        assert schedule.is_recurring is True
        assert schedule.next_run_at is not None
    
    def test_schedule_cron_with_start_at(self, scheduler, user_id):
        """Test schedule_cron respects start_at."""
        start = datetime.now(timezone.utc) + timedelta(days=1)
        
        schedule = scheduler.schedule_cron(
            user_id=user_id,
            task_spec={"input_text": "Test"},
            cron="0 9 * * *",
            start_at=start,
        )
        
        assert schedule.next_run_at >= start
    
    # ==================== CANCEL TESTS ====================
    
    def test_cancel_sets_cancelled_status(self, scheduler, user_id):
        """Test cancel changes status to cancelled."""
        schedule = scheduler.schedule_delay(
            user_id=user_id,
            task_spec={"input_text": "Test"},
            delay_seconds=3600,
        )
        
        cancelled = scheduler.cancel(schedule.id)
        
        assert cancelled.status == ScheduleStatus.CANCELLED
    
    def test_cancel_removes_from_pending(self, scheduler, user_id):
        """Test cancelled schedule not in pending list."""
        schedule = scheduler.schedule_delay(
            user_id=user_id,
            task_spec={"input_text": "Test"},
            delay_seconds=3600,
        )
        scheduler.cancel(schedule.id)
        
        pending = scheduler.list_pending(user_id)
        
        assert len(pending) == 0
    
    # ==================== PAUSE/RESUME TESTS ====================
    
    def test_pause_sets_paused_status(self, scheduler, user_id):
        """Test pause changes status to paused."""
        schedule = scheduler.schedule_delay(
            user_id=user_id,
            task_spec={"input_text": "Test"},
            delay_seconds=3600,
        )
        
        paused = scheduler.pause(schedule.id)
        
        assert paused.status == ScheduleStatus.PAUSED
    
    def test_resume_restores_pending(self, scheduler, user_id):
        """Test resume restores pending status."""
        schedule = scheduler.schedule_delay(
            user_id=user_id,
            task_spec={"input_text": "Test"},
            delay_seconds=3600,
        )
        scheduler.pause(schedule.id)
        
        resumed = scheduler.resume(schedule.id)
        
        assert resumed.status == ScheduleStatus.PENDING
    
    # ==================== QUERY TESTS ====================
    
    def test_get_schedule_returns_none_for_invalid(self, scheduler):
        """Test get_schedule returns None for non-existent ID."""
        schedule = scheduler.get_schedule(99999)
        
        assert schedule is None
    
    def test_list_pending_returns_only_pending(self, scheduler, user_id):
        """Test list_pending filters by status."""
        s1 = scheduler.schedule_delay(user_id, {"input_text": "1"}, 3600)
        s2 = scheduler.schedule_delay(user_id, {"input_text": "2"}, 3600)
        s3 = scheduler.schedule_delay(user_id, {"input_text": "3"}, 3600)
        
        scheduler.cancel(s1.id)
        scheduler.pause(s2.id)
        
        pending = scheduler.list_pending(user_id)
        
        assert len(pending) == 1
        assert pending[0].id == s3.id
    
    def test_list_all_returns_all_statuses(self, scheduler, user_id):
        """Test list_all returns all schedules."""
        scheduler.schedule_delay(user_id, {"input_text": "1"}, 3600)
        s2 = scheduler.schedule_delay(user_id, {"input_text": "2"}, 3600)
        scheduler.cancel(s2.id)
        
        all_schedules = scheduler.list_all(user_id)
        
        assert len(all_schedules) == 2
    
    def test_get_due_schedules_finds_past_schedules(self, scheduler, user_id, db):
        """Test get_due_schedules returns schedules with past next_run_at."""
        # Insert schedule with past run time directly
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        
        db.execute(
            """INSERT INTO schedules 
               (user_id, task_spec, run_at, next_run_at, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                '{"task_type": "test"}',
                past_time.isoformat(),
                past_time.isoformat(),
                ScheduleStatus.PENDING.value,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        
        due = scheduler.get_due_schedules()
        
        assert len(due) == 1
    
    # ==================== PROCESS_DUE TESTS ====================
    
    def test_process_due_creates_task(self, scheduler, tm, user_id, db):
        """Test process_due creates task from schedule."""
        # Insert schedule with past run time
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        
        db.execute(
            """INSERT INTO schedules 
               (user_id, task_spec, run_at, next_run_at, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                '{"task_type": "scheduled_task", "input_text": "From schedule"}',
                past_time.isoformat(),
                past_time.isoformat(),
                ScheduleStatus.PENDING.value,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        
        count = scheduler.process_due()
        
        assert count == 1
        
        # Verify task created
        tasks = tm.get_user_tasks(user_id)
        assert len(tasks) == 1
        assert tasks[0].task_type == "scheduled_task"
        assert tasks[0].status == TaskStatus.QUEUED
    
    def test_process_due_marks_onetime_as_executed(self, scheduler, user_id, db):
        """Test process_due marks one-time schedule as executed."""
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        
        schedule_id = db.execute(
            """INSERT INTO schedules 
               (user_id, task_spec, run_at, next_run_at, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                '{"input_text": "Test"}',
                past_time.isoformat(),
                past_time.isoformat(),
                ScheduleStatus.PENDING.value,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        
        scheduler.process_due()
        
        schedule = scheduler.get_schedule(schedule_id)
        assert schedule.status == ScheduleStatus.EXECUTED
        assert schedule.run_count == 1
    
    def test_process_due_recurring_stays_pending(self, scheduler, user_id, db):
        """Test process_due keeps recurring schedule pending."""
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        
        schedule_id = db.execute(
            """INSERT INTO schedules 
               (user_id, task_spec, cron, next_run_at, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                '{"input_text": "Recurring"}',
                "0 9 * * *",
                past_time.isoformat(),
                ScheduleStatus.PENDING.value,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            )
        )
        
        scheduler.process_due()
        
        schedule = scheduler.get_schedule(schedule_id)
        assert schedule.status == ScheduleStatus.PENDING
        assert schedule.run_count == 1
        assert schedule.next_run_at > datetime.now(timezone.utc)


class TestCronParsing:
    """Tests for cron expression parsing."""
    
    @pytest.fixture
    def scheduler(self, tmp_path):
        """Create Scheduler."""
        db = Database(tmp_path / "test.sqlite3")
        return Scheduler(db=db)
    
    def test_every_minute(self, scheduler):
        """Test '* * * * *' returns +1 minute."""
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        
        next_time = scheduler._get_next_cron_time("* * * * *", now)
        
        expected = now + timedelta(minutes=1)
        assert next_time == expected
    
    def test_hourly_at_30(self, scheduler):
        """Test '30 * * * *' returns next :30."""
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        
        next_time = scheduler._get_next_cron_time("30 * * * *", now)
        
        assert next_time.minute == 30
    
    def test_daily_at_9(self, scheduler):
        """Test '0 9 * * *' returns next 9:00."""
        now = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
        
        next_time = scheduler._get_next_cron_time("0 9 * * *", now)
        
        assert next_time.hour == 9
        assert next_time.minute == 0
    
    def test_daily_wraps_to_next_day(self, scheduler):
        """Test daily cron wraps to next day if time passed."""
        now = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
        
        next_time = scheduler._get_next_cron_time("0 9 * * *", now)
        
        assert next_time.hour == 9
        assert next_time > now
    
    def test_invalid_cron_returns_fallback(self, scheduler):
        """Test invalid cron returns fallback (1 hour)."""
        now = datetime.now(timezone.utc)
        
        next_time = scheduler._get_next_cron_time("invalid", now)
        
        assert next_time > now
        assert next_time < now + timedelta(hours=2)


class TestScheduleModel:
    """Tests for Schedule model."""
    
    def test_is_recurring_with_cron(self):
        """Test is_recurring is True when cron is set."""
        schedule = Schedule(
            id=1,
            user_id=1,
            task_spec={},
            cron="0 9 * * *",
        )
        
        assert schedule.is_recurring is True
    
    def test_is_recurring_without_cron(self):
        """Test is_recurring is False when cron is None."""
        schedule = Schedule(
            id=1,
            user_id=1,
            task_spec={},
            run_at=datetime.now(timezone.utc),
        )
        
        assert schedule.is_recurring is False
    
    def test_to_dict(self):
        """Test Schedule.to_dict serialization."""
        now = datetime.now(timezone.utc)
        schedule = Schedule(
            id=1,
            user_id=1,
            task_spec={"input_text": "Test"},
            run_at=now,
            status=ScheduleStatus.PENDING,
        )
        
        data = schedule.to_dict()
        
        assert data["id"] == 1
        assert data["status"] == "pending"
        assert data["task_spec"]["input_text"] == "Test"
