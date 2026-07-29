import os
import secrets
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ArtifactError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredArtifact:
    artifact_id: str
    size: int
    media_type: str


class ArtifactStore:
    def __init__(self, root: str, max_bytes: int) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes

    def _path(self, artifact_id: str) -> Path:
        if len(artifact_id) != 48 or not artifact_id.isalnum():
            raise ArtifactError("invalid_artifact_id")
        candidate = (self.root / f"{artifact_id}.png").resolve()
        if candidate.parent != self.root:
            raise ArtifactError("invalid_artifact_path")
        return candidate

    def put_png(self, content: bytes) -> StoredArtifact:
        if not _valid_png(content):
            raise ArtifactError("invalid_image_type")
        if len(content) > self.max_bytes:
            raise ArtifactError("artifact_too_large")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        artifact_id = secrets.token_hex(24)
        path = self._path(artifact_id)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return StoredArtifact(artifact_id, len(content), "image/png")

    def path_for_read(self, artifact_id: str) -> Path:
        path = self._path(artifact_id)
        if not path.is_file() or path.is_symlink():
            raise ArtifactError("artifact_not_found")
        return path

    def delete(self, artifact_id: str) -> bool:
        path = self._path(artifact_id)
        if path.is_symlink():
            raise ArtifactError("invalid_artifact_path")
        if not path.exists():
            return False
        path.unlink()
        return True

    def ids(self) -> set[str]:
        if not self.root.exists():
            return set()
        return {
            path.stem
            for path in self.root.glob("*.png")
            if path.is_file() and not path.is_symlink() and len(path.stem) == 48
        }


def _valid_png(content: bytes) -> bool:
    if not content.startswith(PNG_SIGNATURE) or len(content) < 33:
        return False
    offset = len(PNG_SIGNATURE)
    saw_header = False
    saw_end = False
    while offset + 12 <= len(content):
        length = struct.unpack(">I", content[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if length > 10_000_000 or chunk_end > len(content):
            return False
        chunk_type = content[offset + 4 : offset + 8]
        data = content[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", content[offset + 8 + length : chunk_end])[0]
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            return False
        if chunk_type == b"IHDR":
            if saw_header or length != 13:
                return False
            width, height = struct.unpack(">II", data[:8])
            if width == 0 or height == 0:
                return False
            saw_header = True
        if chunk_type == b"IEND":
            saw_end = length == 0
            break
        offset = chunk_end
    return saw_header and saw_end
