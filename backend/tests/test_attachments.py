"""Uploaded attachments: storage, the public route, and lifecycle.

The public route is the sharp edge here. Meta fetches attachments itself and
carries none of our credentials, so `/files/...` cannot be authenticated. What
stands between an upload and the world is the UUID in its path, which makes the
path-handling tests load-bearing rather than pedantic.
"""

from pathlib import Path

import pytest

from app.services.attachments import (
    AttachmentError,
    delete,
    kind_for,
    resolve,
    store,
)

PDF = b"%PDF-1.4\nfake pdf body\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


# -- type and size rules are Meta's, enforced before anything is written -----


def test_recognises_each_supported_kind():
    assert kind_for("guide.pdf").attachment_type == "file"
    assert kind_for("photo.PNG").attachment_type == "image"
    assert kind_for("clip.mov").attachment_type == "video"
    assert kind_for("voice.m4a").attachment_type == "audio"


def test_mp4_resolves_to_video():
    """Legal as both. Video is what a sender almost always means."""
    assert kind_for("clip.mp4").attachment_type == "video"


def test_an_unsupported_type_is_refused():
    with pytest.raises(AttachmentError, match="not supported"):
        kind_for("payload.exe")


def test_a_docx_is_refused_because_meta_only_takes_pdf(root):
    with pytest.raises(AttachmentError):
        store(root, "contract.docx", PDF)


def test_an_oversized_image_is_refused(root):
    """Images cap at 8MB, lower than the other three."""
    with pytest.raises(AttachmentError, match="8MB"):
        store(root, "huge.png", b"0" * (9 * 1024 * 1024))


def test_an_empty_file_is_refused(root):
    with pytest.raises(AttachmentError, match="empty"):
        store(root, "nothing.pdf", b"")


def test_a_rejected_upload_leaves_nothing_behind(root):
    with pytest.raises(AttachmentError):
        store(root, "payload.exe", PDF)
    assert not root.exists() or not any(root.iterdir())


# -- storage layout ----------------------------------------------------------


def test_stored_under_a_uuid_folder_with_a_random_suffix(root):
    stored = store(root, "Vibe Stack Setup Guide.pdf", PDF)

    folder, _, filename = stored.storage_key.partition("/")
    assert len(folder) == 36
    assert filename.startswith("vibe-stack-setup-guide-")
    assert filename.endswith(".pdf")
    assert (root / stored.storage_key).read_bytes() == PDF
    assert stored.attachment_type == "file"
    assert stored.size_bytes == len(PDF)


def test_the_same_filename_twice_does_not_collide(root):
    first = store(root, "guide.pdf", PDF)
    second = store(root, "guide.pdf", PDF)
    assert first.storage_key != second.storage_key


# -- the public route --------------------------------------------------------


def test_resolves_a_real_file(root):
    stored = store(root, "guide.pdf", PDF)
    folder, _, filename = stored.storage_key.partition("/")
    assert resolve(root, folder, filename) is not None


def test_refuses_a_traversal_attempt(root):
    """`/files/<uuid>/../../etc/passwd` must not escape the upload root."""
    stored = store(root, "guide.pdf", PDF)
    folder, _, _ = stored.storage_key.partition("/")

    assert resolve(root, folder, "../../etc/passwd") is None
    assert resolve(root, "..", "passwd") is None
    assert resolve(root, folder, "..%2fpasswd") is None


def test_refuses_a_folder_that_is_not_a_uuid(root):
    store(root, "guide.pdf", PDF)
    assert resolve(root, "etc", "passwd") is None


def test_a_missing_file_resolves_to_nothing(root):
    stored = store(root, "guide.pdf", PDF)
    folder, _, _ = stored.storage_key.partition("/")
    assert resolve(root, folder, "not-a-real-file-ABCDEFGHIJ.pdf") is None


# -- deletion ----------------------------------------------------------------


def test_delete_removes_the_file_and_its_folder(root):
    stored = store(root, "guide.pdf", PDF)

    assert delete(root, stored.storage_key) is True
    assert not (root / stored.storage_key).exists()
    assert not (root / stored.storage_key.split("/")[0]).exists()


def test_delete_tolerates_a_stale_key(root):
    """Called on every rule delete, including rules that never had a file."""
    assert delete(root, None) is False
    assert delete(root, "") is False
    assert delete(root, "not-a-key") is False


def test_delete_cannot_be_talked_into_leaving_the_upload_root(root, tmp_path):
    victim = tmp_path / "important"
    victim.mkdir()
    (victim / "keep.txt").write_text("do not delete me")

    assert delete(root, "../important/keep-ABCDEFGHIJ.txt") is False
    assert (victim / "keep.txt").exists()


def test_deleting_one_upload_leaves_the_others(root):
    keep = store(root, "keep.pdf", PDF)
    doomed = store(root, "doomed.png", PNG)

    delete(root, doomed.storage_key)

    assert (root / keep.storage_key).exists()
    assert not (root / doomed.storage_key).exists()


def test_generated_names_always_match_the_public_route_pattern(root):
    """Regression: the suffix used to come from token_urlsafe.

    That alphabet includes "-" and "_", which FILE_PATTERN rejects, so roughly a
    third of uploads stored fine and then 404'd on the very route Meta fetches
    from. Probabilistic, so this asserts over a batch rather than one file.
    """
    from app.services.attachments import FILE_PATTERN

    for index in range(200):
        stored = store(root, f"Guide {index}.pdf", PDF)
        _, _, filename = stored.storage_key.partition("/")
        assert FILE_PATTERN.match(filename), filename
