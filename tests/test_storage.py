"""
Tests for Layer 0: Storage

Run with: pytest -q
"""
import pytest
import tempfile
import json
from pathlib import Path
from datetime import datetime

from app.storage import Database, FileStorage, FileRef, to_json, from_json, now_iso


class TestDatabase:
    """Tests for Database class."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create fresh database for each test."""
        db_path = tmp_path / "test.sqlite3"
        return Database(db_path)
    
    def test_database_creation(self, db):
        """Test database is created and schema initialized."""
        # Check that tables exist
        tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [row["name"] for row in tables]
        
        assert "users" in table_names
        assert "tasks" in table_names
        assert "task_events" in table_names
        assert "task_steps" in table_names
        assert "schedules" in table_names
        assert "memory_items" in table_names
        assert "costs" in table_names
    
    def test_insert_and_fetch_user(self, db):
        """Test inserting and fetching a user."""
        user_id = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123456789, "testuser")
        )
        
        assert user_id == 1
        
        user = db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
        
        assert user is not None
        assert user["tg_id"] == 123456789
        assert user["username"] == "testuser"
    
    def test_fetch_value(self, db):
        """Test fetching single value."""
        db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (111, "user1")
        )
        db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (222, "user2")
        )
        
        count = db.fetch_value("SELECT COUNT(*) FROM users")
        
        assert count == 2
    
    def test_fetch_value_default(self, db):
        """Test fetch_value returns default when no result."""
        result = db.fetch_value(
            "SELECT id FROM users WHERE tg_id = ?",
            (999999,),
            default=-1
        )
        
        assert result == -1
    
    def test_fetch_all(self, db):
        """Test fetching multiple rows."""
        db.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (111, "a"))
        db.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (222, "b"))
        db.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (333, "c"))
        
        users = db.fetch_all("SELECT * FROM users ORDER BY tg_id")
        
        assert len(users) == 3
        assert users[0]["tg_id"] == 111
        assert users[2]["tg_id"] == 333
    
    def test_transaction_commit(self, db):
        """Test transaction commits on success."""
        with db.transaction():
            db.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (111, "a"))
            db.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (222, "b"))
        
        count = db.fetch_value("SELECT COUNT(*) FROM users")
        assert count == 2
    
    def test_transaction_rollback(self, db):
        """Test transaction rolls back on error."""
        try:
            with db.transaction():
                db.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", (111, "a"))
                raise ValueError("Simulated error")
        except ValueError:
            pass
        
        count = db.fetch_value("SELECT COUNT(*) FROM users")
        assert count == 0
    
    def test_foreign_key_constraint(self, db):
        """Test foreign key constraints are enforced."""
        with pytest.raises(Exception):  # sqlite3.IntegrityError
            db.execute(
                "INSERT INTO tasks (user_id, input_text) VALUES (?, ?)",
                (999, "test")  # user_id 999 doesn't exist
            )
    
    def test_task_creation(self, db):
        """Test creating a task."""
        # First create user
        user_id = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "testuser")
        )
        
        # Create task
        task_id = db.execute(
            """INSERT INTO tasks (user_id, task_type, input_text, status)
               VALUES (?, ?, ?, ?)""",
            (user_id, "smm", "Write a post", "queued")
        )
        
        task = db.fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        
        assert task["user_id"] == user_id
        assert task["task_type"] == "smm"
        assert task["status"] == "queued"
    
    def test_execute_many(self, db):
        """Test batch insert."""
        users_data = [
            (100, "user100"),
            (200, "user200"),
            (300, "user300"),
        ]
        
        db.execute_many(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            users_data
        )
        
        count = db.fetch_value("SELECT COUNT(*) FROM users")
        assert count == 3


class TestFileStorage:
    """Tests for FileStorage class."""
    
    @pytest.fixture
    def storage(self, tmp_path):
        """Create fresh file storage for each test."""
        return FileStorage(tmp_path)
    
    def test_save_and_load_bytes(self, storage):
        """Test saving and loading raw bytes."""
        data = b"Hello, World!"
        
        ref = storage.save(data, "uploads", "test.txt")
        
        assert ref.ref_id is not None
        assert ref.storage_type == "uploads"
        assert ref.filename == "test.txt"
        assert ref.size_bytes == len(data)
        assert ref.checksum is not None
        
        loaded = storage.load(ref)
        assert loaded == data
    
    def test_save_and_load_text(self, storage):
        """Test saving and loading text."""
        text = "Привет, мир!"  # Test unicode
        
        ref = storage.save_text(text, "outputs", "hello.txt")
        
        loaded = storage.load_text(ref)
        assert loaded == text
    
    def test_save_and_load_json(self, storage):
        """Test saving and loading JSON."""
        obj = {
            "name": "test",
            "values": [1, 2, 3],
            "nested": {"a": "b"},
        }
        
        ref = storage.save_json(obj, "snapshots", "data.json")
        
        loaded = storage.load_json(ref)
        assert loaded == obj
    
    def test_file_exists(self, storage):
        """Test checking file existence."""
        ref = storage.save(b"test", "uploads", "exists.txt")
        
        assert storage.exists(ref) is True
        
        fake_ref = FileRef(
            ref_id="nonexistent",
            storage_type="uploads",
            filename="fake.txt"
        )
        assert storage.exists(fake_ref) is False
    
    def test_file_delete(self, storage):
        """Test deleting file."""
        ref = storage.save(b"test", "uploads", "delete_me.txt")
        
        assert storage.exists(ref) is True
        
        deleted = storage.delete(ref)
        assert deleted is True
        assert storage.exists(ref) is False
        
        # Delete again returns False
        deleted_again = storage.delete(ref)
        assert deleted_again is False
    
    def test_get_path(self, storage):
        """Test getting file path."""
        ref = storage.save(b"test", "uploads", "pathtest.txt")
        
        path = storage.get_path(ref)
        
        assert path.exists()
        assert path.is_file()
        assert "uploads" in str(path)
    
    def test_load_from_dict(self, storage):
        """Test loading file using dict instead of FileRef."""
        ref = storage.save(b"test data", "outputs", "dicttest.txt")
        
        ref_dict = ref.to_dict()
        loaded = storage.load(ref_dict)
        
        assert loaded == b"test data"
    
    def test_invalid_storage_type(self, storage):
        """Test error on invalid storage type."""
        with pytest.raises(ValueError, match="Invalid storage_type"):
            storage.save(b"test", "invalid_type", "test.txt")
    
    def test_file_not_found(self, storage):
        """Test error when file not found."""
        fake_ref = FileRef(
            ref_id="nonexistent",
            storage_type="uploads",
            filename="fake.txt"
        )
        
        with pytest.raises(FileNotFoundError):
            storage.load(fake_ref)
    
    def test_list_files(self, storage):
        """Test listing files."""
        storage.save(b"1", "uploads", "file1.txt")
        storage.save(b"2", "uploads", "file2.txt")
        storage.save(b"3", "outputs", "file3.txt")
        
        upload_files = storage.list_files("uploads")
        assert len(upload_files) == 2
        
        output_files = storage.list_files("outputs")
        assert len(output_files) == 1
    
    def test_checksum_computed(self, storage):
        """Test checksum is computed correctly."""
        data = b"checksum test"
        ref = storage.save(data, "uploads", "checksum.txt")
        
        assert ref.checksum is not None
        assert len(ref.checksum) == 64  # SHA-256 hex
        
        # Same data = same checksum
        ref2 = storage.save(data, "uploads", "checksum2.txt")
        assert ref.checksum == ref2.checksum
    
    def test_metadata_stored(self, storage):
        """Test metadata is stored in ref."""
        metadata = {"source": "test", "version": 1}
        
        ref = storage.save(
            b"test",
            "uploads",
            "meta.txt",
            metadata=metadata
        )
        
        assert ref.metadata == metadata


class TestJsonHelpers:
    """Tests for JSON helper functions."""
    
    def test_to_json_basic(self):
        """Test basic JSON serialization."""
        obj = {"key": "value", "num": 42}
        result = to_json(obj)
        
        assert json.loads(result) == obj
    
    def test_to_json_datetime(self):
        """Test datetime serialization."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        obj = {"timestamp": dt}
        
        result = to_json(obj)
        parsed = json.loads(result)
        
        assert "2024-01-15" in parsed["timestamp"]
    
    def test_to_json_unicode(self):
        """Test unicode handling."""
        obj = {"text": "Привет 🌍"}
        result = to_json(obj)
        
        # Should not escape unicode
        assert "Привет" in result
        assert "🌍" in result
    
    def test_from_json_basic(self):
        """Test basic JSON parsing."""
        s = '{"key": "value"}'
        result = from_json(s)
        
        assert result == {"key": "value"}
    
    def test_from_json_none(self):
        """Test None input."""
        assert from_json(None) is None
    
    def test_from_json_empty(self):
        """Test empty string input."""
        assert from_json("") is None
    
    def test_now_iso_format(self):
        """Test ISO timestamp format."""
        result = now_iso()
        
        # Should be valid ISO format with Z suffix
        assert "T" in result
        assert result.endswith("Z")
        
        # Should be parseable
        # Remove Z and microseconds for parsing
        dt_str = result.rstrip("Z")
        datetime.fromisoformat(dt_str)


class TestSchemaIntegrity:
    """Tests for database schema."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create fresh database."""
        return Database(tmp_path / "test.sqlite3")
    
    def test_all_tables_exist(self, db):
        """Verify all required tables exist."""
        required_tables = [
            "users",
            "tasks", 
            "task_events",
            "task_steps",
            "schedules",
            "memory_items",
            "costs",
        ]
        
        tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = [row["name"] for row in tables]
        
        for table in required_tables:
            assert table in table_names, f"Table {table} missing"
    
    def test_fts_table_exists(self, db):
        """Verify FTS virtual table exists."""
        tables = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        table_names = [row["name"] for row in tables]
        
        assert "memory_fts" in table_names
    
    def test_view_exists(self, db):
        """Verify view exists."""
        views = db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )
        view_names = [row["name"] for row in views]
        
        assert "tasks_with_user" in view_names
    
    def test_status_constraint(self, db):
        """Test task status constraint."""
        user_id = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "test")
        )
        
        # Valid status
        task_id = db.execute(
            "INSERT INTO tasks (user_id, status) VALUES (?, ?)",
            (user_id, "queued")
        )
        assert task_id > 0
        
        # Invalid status should fail
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO tasks (user_id, status) VALUES (?, ?)",
                (user_id, "invalid_status")
            )
    
    def test_memory_type_constraint(self, db):
        """Test memory_items type constraint."""
        user_id = db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "test")
        )
        
        # Valid types
        for mem_type in ["fact", "decision", "context", "task", "feedback"]:
            db.execute(
                "INSERT INTO memory_items (user_id, memory_type, content) VALUES (?, ?, ?)",
                (user_id, mem_type, "test content")
            )
        
        # Invalid type should fail
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO memory_items (user_id, memory_type, content) VALUES (?, ?, ?)",
                (user_id, "invalid_type", "test")
            )
