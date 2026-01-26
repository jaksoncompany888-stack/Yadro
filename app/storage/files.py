"""
Yadro v0 - File Storage Module

Manages file storage for uploads, outputs, and snapshots.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Union


@dataclass
class FileRef:
    """
    Reference to a stored file.
    
    Used to track files across the system without hardcoding paths.
    """
    ref_id: str
    storage_type: str  # uploads, outputs, snapshots
    filename: str
    checksum: Optional[str] = None
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileRef":
        """Create from dictionary."""
        return cls(**data)


class FileStorage:
    """
    File storage manager.
    
    Directory structure:
        data/
        ├── uploads/    # User uploaded files
        ├── outputs/    # Generated files for user
        └── snapshots/  # Internal snapshots for rollback
    """
    
    def __init__(self, base_path: Optional[Union[str, Path]] = None):
        """
        Initialize file storage.
        
        Args:
            base_path: Base directory for storage. If None, uses default from settings.
        """
        if base_path is None:
            from ..config.settings import settings
            base_path = settings.storage.base_path
        
        self._base_path = Path(base_path)
        
        # Ensure directories exist
        for subdir in ["uploads", "outputs", "snapshots"]:
            (self._base_path / subdir).mkdir(parents=True, exist_ok=True)
    
    def _get_dir(self, storage_type: str) -> Path:
        """Get directory for storage type."""
        valid_types = ("uploads", "outputs", "snapshots")
        if storage_type not in valid_types:
            raise ValueError(f"Invalid storage_type: {storage_type}. Must be one of {valid_types}")
        return self._base_path / storage_type
    
    def _generate_ref_id(self) -> str:
        """Generate unique reference ID."""
        return str(uuid.uuid4())
    
    def _compute_checksum(self, data: bytes) -> str:
        """Compute SHA-256 checksum."""
        return hashlib.sha256(data).hexdigest()
    
    def _get_file_path(self, ref: FileRef) -> Path:
        """
        Get full file path for a reference.
        
        Uses sharding: {storage_type}/{ref_id[:2]}/{ref_id}/{filename}
        """
        shard = ref.ref_id[:2]
        return self._get_dir(ref.storage_type) / shard / ref.ref_id / ref.filename
    
    def save(
        self,
        data: bytes,
        storage_type: str,
        filename: str,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FileRef:
        """
        Save file to storage.
        
        Args:
            data: File content as bytes
            storage_type: Where to store (uploads/outputs/snapshots)
            filename: Original filename
            mime_type: MIME type
            metadata: Additional metadata
            
        Returns:
            FileRef pointing to saved file
        """
        ref_id = self._generate_ref_id()
        checksum = self._compute_checksum(data)
        
        ref = FileRef(
            ref_id=ref_id,
            storage_type=storage_type,
            filename=filename,
            checksum=checksum,
            size_bytes=len(data),
            mime_type=mime_type,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata,
        )
        
        # Create directory and save file
        file_path = self._get_file_path(ref)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        
        return ref
    
    def load(self, ref: Union[FileRef, Dict[str, Any]]) -> bytes:
        """
        Load file from storage.
        
        Args:
            ref: FileRef or dict with ref data
            
        Returns:
            File content as bytes
            
        Raises:
            FileNotFoundError: If file doesn't exist
        """
        if isinstance(ref, dict):
            ref = FileRef.from_dict(ref)
        
        file_path = self._get_file_path(ref)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        return file_path.read_bytes()
    
    def save_text(
        self,
        text: str,
        storage_type: str,
        filename: str,
        encoding: str = "utf-8",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FileRef:
        """Save text file."""
        return self.save(
            data=text.encode(encoding),
            storage_type=storage_type,
            filename=filename,
            mime_type="text/plain",
            metadata=metadata,
        )
    
    def load_text(
        self,
        ref: Union[FileRef, Dict[str, Any]],
        encoding: str = "utf-8",
    ) -> str:
        """Load text file."""
        return self.load(ref).decode(encoding)
    
    def save_json(
        self,
        obj: Any,
        storage_type: str,
        filename: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> FileRef:
        """Save JSON file."""
        text = json.dumps(obj, ensure_ascii=False, indent=2)
        return self.save(
            data=text.encode("utf-8"),
            storage_type=storage_type,
            filename=filename,
            mime_type="application/json",
            metadata=metadata,
        )
    
    def load_json(self, ref: Union[FileRef, Dict[str, Any]]) -> Any:
        """Load JSON file."""
        text = self.load_text(ref)
        return json.loads(text)
    
    def exists(self, ref: Union[FileRef, Dict[str, Any]]) -> bool:
        """Check if file exists."""
        if isinstance(ref, dict):
            ref = FileRef.from_dict(ref)
        return self._get_file_path(ref).exists()
    
    def delete(self, ref: Union[FileRef, Dict[str, Any]]) -> bool:
        """
        Delete file from storage.
        
        Args:
            ref: FileRef or dict
            
        Returns:
            True if deleted, False if not found
        """
        if isinstance(ref, dict):
            ref = FileRef.from_dict(ref)
        
        file_path = self._get_file_path(ref)
        
        if not file_path.exists():
            return False
        
        file_path.unlink()
        
        # Clean up empty directories
        try:
            file_path.parent.rmdir()  # ref_id dir
            file_path.parent.parent.rmdir()  # shard dir
        except OSError:
            pass  # Not empty
        
        return True
    
    def get_path(self, ref: Union[FileRef, Dict[str, Any]]) -> Path:
        """Get absolute path to file."""
        if isinstance(ref, dict):
            ref = FileRef.from_dict(ref)
        return self._get_file_path(ref).absolute()
    
    def list_files(self, storage_type: str) -> List[Path]:
        """List all files in storage type."""
        storage_dir = self._get_dir(storage_type)
        files = []
        for path in storage_dir.rglob("*"):
            if path.is_file() and path.name != ".gitkeep":
                files.append(path)
        return files
