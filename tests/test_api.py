"""
Tests for API Layer
"""

import pytest
import json
import hmac
import hashlib
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.deps import validate_telegram_init_data, get_current_user, get_db
from app.api.models import PostStatus, Platform


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_user():
    """Mock authenticated user."""
    return {
        "id": 1,
        "tg_id": 123456,
        "username": "testuser",
        "settings": {},
    }


@pytest.fixture
def mock_db_instance():
    """Mock database instance."""
    db = MagicMock()
    return db


@pytest.fixture
def app(mock_user, mock_db_instance):
    """Create test app with mocked dependencies."""
    application = create_app()

    # Override auth dependency
    application.dependency_overrides[get_current_user] = lambda: mock_user
    application.dependency_overrides[get_db] = lambda: mock_db_instance

    return application


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def valid_init_data():
    """Generate valid Telegram init data for testing."""
    return "query_id=test&user=%7B%22id%22%3A123456%7D&auth_date=9999999999&hash=test"


# =============================================================================
# Auth Tests
# =============================================================================

class TestTelegramAuth:
    """Tests for Telegram Mini App authentication."""

    def test_validate_init_data_missing(self):
        """Should return None for missing data."""
        result = validate_telegram_init_data("")
        assert result is None

    def test_validate_init_data_no_hash(self):
        """Should return None for data without hash."""
        result = validate_telegram_init_data("user={}&auth_date=123")
        assert result is None

    @patch("app.api.deps.BOT_TOKEN", "test:token")
    def test_validate_init_data_expired(self):
        """Should return None for expired auth."""
        old_date = int((datetime.now() - timedelta(days=2)).timestamp())
        init_data = f"user=%7B%22id%22%3A123%7D&auth_date={old_date}&hash=invalid"
        result = validate_telegram_init_data(init_data)
        assert result is None


# =============================================================================
# Health Check
# =============================================================================

class TestHealthCheck:
    """Tests for health endpoint."""

    def test_health(self, client):
        """Health check should return ok."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_api_info(self, client):
        """API info should return endpoints."""
        response = client.get("/api")
        assert response.status_code == 200
        data = response.json()
        assert "posts" in data["endpoints"]
        assert "calendar" in data["endpoints"]


# =============================================================================
# Posts API Tests (with mocked auth)
# =============================================================================

class TestPostsAPI:
    """Tests for posts endpoints."""

    def test_create_post(self, client, mock_db_instance):
        """Should create a new post."""
        mock_db_instance.fetch_one.return_value = {
            "id": 1,
            "user_id": 1,
            "text": "Test post",
            "topic": "test",
            "channel_id": "@test",
            "publish_at": None,
            "status": "draft",
            "metadata": '{"platforms": ["telegram"], "channel_ids": {"telegram": "@test"}, "media": []}',
            "created_at": "2025-01-26T12:00:00",
            "updated_at": "2025-01-26T12:00:00",
        }

        response = client.post(
            "/api/posts",
            json={
                "text": "Test post",
                "topic": "test",
                "platforms": ["telegram"],
                "channel_ids": {"telegram": "@test"},
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["text"] == "Test post"
        assert data["status"] == "draft"

    def test_list_posts(self, client, mock_db_instance):
        """Should list user's posts."""
        mock_db_instance.fetch_value.return_value = 2
        mock_db_instance.fetch_all.return_value = [
            {
                "id": 1,
                "user_id": 1,
                "text": "Post 1",
                "topic": None,
                "channel_id": "@test",
                "publish_at": None,
                "status": "draft",
                "metadata": '{"platforms": ["telegram"], "channel_ids": {}, "media": []}',
                "created_at": "2025-01-26T12:00:00",
                "updated_at": "2025-01-26T12:00:00",
            },
            {
                "id": 2,
                "user_id": 1,
                "text": "Post 2",
                "topic": None,
                "channel_id": "@test",
                "publish_at": "2025-01-27T10:00:00",
                "status": "scheduled",
                "metadata": '{"platforms": ["telegram"], "channel_ids": {}, "media": []}',
                "created_at": "2025-01-26T12:00:00",
                "updated_at": "2025-01-26T12:00:00",
            },
        ]

        response = client.get("/api/posts")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_update_post(self, client, mock_db_instance):
        """Should update a post."""
        mock_db_instance.fetch_one.side_effect = [
            {
                "id": 1,
                "user_id": 1,
                "text": "Original",
                "topic": None,
                "channel_id": "@test",
                "publish_at": None,
                "status": "draft",
                "metadata": '{}',
                "created_at": "2025-01-26T12:00:00",
                "updated_at": "2025-01-26T12:00:00",
            },
            {
                "id": 1,
                "user_id": 1,
                "text": "Updated",
                "topic": None,
                "channel_id": "@test",
                "publish_at": None,
                "status": "draft",
                "metadata": '{"platforms": ["telegram"]}',
                "created_at": "2025-01-26T12:00:00",
                "updated_at": "2025-01-26T12:00:00",
            },
        ]

        response = client.patch(
            "/api/posts/1",
            json={"text": "Updated"},
        )

        assert response.status_code == 200

    def test_delete_post(self, client, mock_db_instance):
        """Should delete a post."""
        mock_db_instance.fetch_one.return_value = {"id": 1, "user_id": 1, "status": "draft"}

        response = client.delete("/api/posts/1")

        assert response.status_code == 200
        assert response.json()["success"] is True


# =============================================================================
# Calendar API Tests
# =============================================================================

class TestCalendarAPI:
    """Tests for calendar endpoints."""

    def test_get_calendar(self, client, mock_db_instance):
        """Should return calendar view."""
        mock_db_instance.fetch_all.return_value = []

        response = client.get("/api/calendar?days=7")

        assert response.status_code == 200
        data = response.json()
        assert "days" in data
        assert len(data["days"]) == 8  # 7 days + 1

    def test_get_week(self, client, mock_db_instance):
        """Should return week view."""
        mock_db_instance.fetch_all.return_value = []

        response = client.get("/api/calendar/week")

        assert response.status_code == 200
        data = response.json()
        assert len(data["days"]) == 7

    def test_get_slots(self, client, mock_db_instance):
        """Should return available time slots."""
        mock_db_instance.fetch_all.return_value = []

        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        response = client.get(f"/api/calendar/slots?date={tomorrow}")

        assert response.status_code == 200
        data = response.json()
        assert "slots" in data
        assert len(data["slots"]) > 0


# =============================================================================
# Models Tests
# =============================================================================

class TestModels:
    """Tests for Pydantic models."""

    def test_post_status_enum(self):
        """PostStatus enum should have correct values."""
        assert PostStatus.DRAFT.value == "draft"
        assert PostStatus.SCHEDULED.value == "scheduled"
        assert PostStatus.PUBLISHED.value == "published"

    def test_platform_enum(self):
        """Platform enum should have correct values."""
        assert Platform.TELEGRAM.value == "telegram"
        assert Platform.VK.value == "vk"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
