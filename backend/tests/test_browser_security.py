import struct
import zlib
from pathlib import Path

import pytest

from app.artifacts import PNG_SIGNATURE, ArtifactError, ArtifactStore
from app.browser_policy import BrowserPolicyError, validate_browser_url


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )


async def public_resolver(_: str, __: int) -> list[str]:
    return ["93.184.216.34"]


async def private_resolver(_: str, __: int) -> list[str]:
    return ["127.0.0.1"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "data:text/html,unsafe",
        "blob:https://example.com/id",
        "ws://example.com/socket",
        "javascript:alert(1)",
    ],
)
async def test_browser_rejects_non_http_navigation(url: str) -> None:
    with pytest.raises(BrowserPolicyError, match="unsafe_scheme"):
        await validate_browser_url(url, public_resolver)


async def test_browser_rejects_private_targets_for_every_request() -> None:
    with pytest.raises(BrowserPolicyError, match="unsafe_destination"):
        await validate_browser_url("https://internal.example/resource", private_resolver)


async def test_browser_accepts_validated_public_http_target() -> None:
    assert (
        await validate_browser_url("https://example.com/path", public_resolver)
        == "https://example.com/path"
    )


def test_artifact_store_rejects_traversal_and_non_images(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path), 1024)
    with pytest.raises(ArtifactError, match="invalid_artifact_id"):
        store.path_for_read("../../etc/passwd")
    with pytest.raises(ArtifactError, match="invalid_image_type"):
        store.put_png(b"<script>alert(1)</script>")


def test_artifact_lifecycle_uses_opaque_private_name(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path), 1024)
    artifact = store.put_png(png())
    assert len(artifact.artifact_id) == 48
    path = store.path_for_read(artifact.artifact_id)
    assert path.parent == tmp_path.resolve()
    assert path.stat().st_mode & 0o777 == 0o600
    assert store.delete(artifact.artifact_id)
    with pytest.raises(ArtifactError, match="artifact_not_found"):
        store.path_for_read(artifact.artifact_id)


def test_artifact_size_is_bounded(tmp_path: Path) -> None:
    store = ArtifactStore(str(tmp_path), len(png()) - 1)
    with pytest.raises(ArtifactError, match="artifact_too_large"):
        store.put_png(png())
