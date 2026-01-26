"""
Yadro v0 - Configuration Settings
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DatabaseSettings:
    """Database configuration."""
    path: Path = field(default_factory=lambda: Path("data/yadro.sqlite3"))
    wal_mode: bool = True
    busy_timeout_ms: int = 5000


@dataclass
class StorageSettings:
    """File storage configuration."""
    base_path: Path = field(default_factory=lambda: Path("data"))
    uploads_dir: str = "uploads"
    outputs_dir: str = "outputs"
    snapshots_dir: str = "snapshots"


@dataclass
class Settings:
    """Main settings container."""
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)


# Global settings instance
settings = Settings()
