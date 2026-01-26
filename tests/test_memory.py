"""
Tests for Layer 6: Memory Service

Run with: pytest -q
"""
import pytest
from datetime import datetime, timezone

from app.storage import Database
from app.memory import (
    MemoryService, MemoryItem, MemoryType,
    SearchResult, MemoryContext,
)


class TestMemoryItem:
    """Tests for MemoryItem model."""
    
    def test_create_memory_item(self):
        """Test creating a memory item."""
        item = MemoryItem(
            user_id=1,
            memory_type=MemoryType.FACT,
            content="User likes Python",
            importance=0.9,
        )
        
        assert item.user_id == 1
        assert item.memory_type == MemoryType.FACT
        assert item.content == "User likes Python"
        assert item.importance == 0.9
    
    def test_to_dict(self):
        """Test serialization."""
        item = MemoryItem(
            id=1,
            user_id=1,
            memory_type=MemoryType.DECISION,
            content="Chose option A",
        )
        
        data = item.to_dict()
        
        assert data["id"] == 1
        assert data["memory_type"] == "decision"
        assert data["content"] == "Chose option A"


class TestMemoryContext:
    """Tests for MemoryContext."""
    
    def test_empty_context(self):
        """Test empty context."""
        ctx = MemoryContext()
        
        assert ctx.is_empty() is True
        assert ctx.to_prompt() == ""
    
    def test_context_with_facts(self):
        """Test context with facts."""
        ctx = MemoryContext(
            user_facts=[
                MemoryItem(user_id=1, content="User is a developer"),
                MemoryItem(user_id=1, content="User prefers Python"),
            ]
        )
        
        assert ctx.is_empty() is False
        prompt = ctx.to_prompt()
        assert "User facts:" in prompt
        assert "developer" in prompt
        assert "Python" in prompt
    
    def test_context_to_prompt_full(self):
        """Test full context to prompt."""
        ctx = MemoryContext(
            user_facts=[MemoryItem(user_id=1, content="Fact 1")],
            recent_decisions=[MemoryItem(user_id=1, content="Decision 1")],
            relevant_context=[MemoryItem(user_id=1, content="Context 1")],
            task_history=[MemoryItem(user_id=1, content="Task 1")],
        )
        
        prompt = ctx.to_prompt()
        
        assert "User facts:" in prompt
        assert "Recent decisions:" in prompt
        assert "Relevant context:" in prompt
        assert "Recent tasks:" in prompt


class TestMemoryService:
    """Tests for MemoryService."""
    
    @pytest.fixture
    def db(self, tmp_path):
        """Create fresh database."""
        return Database(tmp_path / "test.sqlite3")
    
    @pytest.fixture
    def service(self, db):
        """Create memory service."""
        return MemoryService(db=db)
    
    @pytest.fixture
    def user_id(self, db):
        """Create test user."""
        return db.execute(
            "INSERT INTO users (tg_id, username) VALUES (?, ?)",
            (123, "testuser")
        )
    
    def test_store_memory(self, service, user_id):
        """Test storing a memory."""
        item = service.store(
            user_id=user_id,
            content="User prefers morning meetings",
            memory_type=MemoryType.FACT,
        )
        
        assert item.id is not None
        assert item.content == "User prefers morning meetings"
        assert item.memory_type == MemoryType.FACT
    
    def test_store_fact(self, service, user_id):
        """Test storing a fact."""
        item = service.store_fact(user_id, "User's name is Alice")
        
        assert item.memory_type == MemoryType.FACT
        assert item.importance >= 0.9  # Facts are high importance
    
    def test_store_decision(self, service, user_id):
        """Test storing a decision."""
        item = service.store_decision(
            user_id=user_id,
            content="Chose dark theme",
        )
        
        assert item.memory_type == MemoryType.DECISION
    
    def test_store_task_summary(self, service, db, user_id):
        """Test storing a task summary."""
        # Create a real task first
        task_id = db.execute(
            "INSERT INTO tasks (user_id, status) VALUES (?, ?)",
            (user_id, "succeeded")
        )
        
        item = service.store_task_summary(
            user_id=user_id,
            task_id=task_id,
            summary="Completed report on AI trends",
        )
        
        assert item.memory_type == MemoryType.TASK
        assert item.source_task_id == task_id
    
    def test_get_memory(self, service, user_id):
        """Test retrieving a memory."""
        stored = service.store(user_id, "Test content")
        
        retrieved = service.get(stored.id)
        
        assert retrieved is not None
        assert retrieved.content == "Test content"
    
    def test_get_nonexistent(self, service):
        """Test getting non-existent memory."""
        result = service.get(99999)
        assert result is None
    
    def test_get_by_type(self, service, user_id):
        """Test getting memories by type."""
        service.store_fact(user_id, "Fact 1")
        service.store_fact(user_id, "Fact 2")
        service.store_decision(user_id, "Decision 1")
        
        facts = service.get_by_type(user_id, MemoryType.FACT)
        decisions = service.get_by_type(user_id, MemoryType.DECISION)
        
        assert len(facts) == 2
        assert len(decisions) == 1
    
    def test_get_facts(self, service, user_id):
        """Test getting all facts."""
        service.store_fact(user_id, "Fact 1")
        service.store_fact(user_id, "Fact 2")
        service.store_decision(user_id, "Not a fact")
        
        facts = service.get_facts(user_id)
        
        assert len(facts) == 2
        assert all(f.memory_type == MemoryType.FACT for f in facts)
    
    def test_get_recent(self, service, user_id):
        """Test getting recent memories."""
        service.store(user_id, "Memory 1")
        service.store(user_id, "Memory 2")
        service.store(user_id, "Memory 3")
        
        recent = service.get_recent(user_id, limit=2)
        
        assert len(recent) == 2
    
    def test_search_fts(self, service, user_id):
        """Test full-text search."""
        service.store(user_id, "Python is a programming language")
        service.store(user_id, "JavaScript is also popular")
        service.store(user_id, "Python has great libraries")
        
        results = service.search(user_id, "Python")
        
        assert len(results) == 2
        assert all("Python" in r.item.content for r in results)
    
    def test_search_simple(self, service, user_id):
        """Test simple search returning items."""
        service.store(user_id, "Machine learning models")
        service.store(user_id, "Deep learning neural networks")
        
        items = service.search_simple(user_id, "learning")
        
        assert len(items) == 2
        assert all(isinstance(i, MemoryItem) for i in items)
    
    def test_search_by_type(self, service, user_id):
        """Test search filtered by type."""
        service.store_fact(user_id, "User knows Python")
        service.store_decision(user_id, "Chose Python for project")
        
        results = service.search(
            user_id, "Python",
            memory_types=[MemoryType.FACT]
        )
        
        assert len(results) == 1
        assert results[0].item.memory_type == MemoryType.FACT
    
    def test_delete_memory(self, service, user_id):
        """Test deleting a memory."""
        item = service.store(user_id, "To be deleted")
        
        service.delete(item.id)
        
        assert service.get(item.id) is None
    
    def test_delete_by_user(self, service, user_id):
        """Test deleting all user memories."""
        service.store(user_id, "Memory 1")
        service.store(user_id, "Memory 2")
        service.store(user_id, "Memory 3")
        
        deleted = service.delete_by_user(user_id)
        
        assert deleted == 3
        assert service.get_recent(user_id) == []
    
    def test_build_context(self, service, db, user_id):
        """Test building memory context."""
        # Create a real task
        task_id = db.execute(
            "INSERT INTO tasks (user_id, status) VALUES (?, ?)",
            (user_id, "succeeded")
        )
        
        service.store_fact(user_id, "User is a developer")
        service.store_decision(user_id, "Prefers TypeScript")
        service.store_task_summary(user_id, task_id, "Completed API integration")
        
        context = service.build_context(user_id)
        
        assert not context.is_empty()
        assert len(context.user_facts) >= 1
    
    def test_build_context_with_query(self, service, user_id):
        """Test building context with search query."""
        service.store(user_id, "Python web development")
        service.store(user_id, "JavaScript frontend")
        service.store_fact(user_id, "Senior developer")
        
        context = service.build_context(user_id, query="Python")
        
        prompt = context.to_prompt()
        assert "Python" in prompt or "Senior" in prompt
    
    def test_get_stats(self, service, user_id):
        """Test getting memory statistics."""
        service.store_fact(user_id, "Fact 1")
        service.store_fact(user_id, "Fact 2")
        service.store_decision(user_id, "Decision 1")
        
        stats = service.get_stats(user_id)
        
        assert stats["total"] == 3
        assert stats["by_type"]["fact"] == 2
        assert stats["by_type"]["decision"] == 1
    
    def test_memory_limit_cleanup(self, service, db, user_id):
        """Test automatic cleanup when limit reached."""
        # Set low limit for testing
        service.MAX_MEMORIES_PER_USER = 5
        
        # Store 5 memories
        for i in range(5):
            service.store(user_id, f"Memory {i}", importance=0.1)
        
        # Store one more - should trigger cleanup
        service.store(user_id, "New memory", importance=0.9)
        
        stats = service.get_stats(user_id)
        assert stats["total"] <= 5
    
    def test_accessed_at_updated(self, service, user_id):
        """Test that accessed_at is updated on retrieval."""
        item = service.store(user_id, "Test memory")
        original_accessed = item.accessed_at
        
        # Wait a tiny bit and access
        import time
        time.sleep(0.01)
        
        retrieved = service.get(item.id)
        
        # accessed_at should be updated
        assert retrieved.accessed_at >= original_accessed


class TestSearchResult:
    """Tests for SearchResult."""
    
    def test_search_result_to_dict(self):
        """Test SearchResult serialization."""
        item = MemoryItem(
            id=1,
            user_id=1,
            content="Test",
        )
        result = SearchResult(item=item, score=0.95, snippet="...Test...")
        
        data = result.to_dict()
        
        assert data["score"] == 0.95
        assert data["snippet"] == "...Test..."
        assert data["item"]["content"] == "Test"
