"""Tests for zarr_creator.storage (local + memory filesystems, no network)."""

import os

import pytest

from zarr_creator import storage


def _write(path, content="data"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_resolve_local_and_memory():
    fs, path = storage.resolve_fs("/tmp/some/file")
    assert path == "/tmp/some/file"
    fs_m, path_m = storage.resolve_fs("memory://bucket/file")
    assert path_m == "/bucket/file"


def test_exists_and_find_missing_local(tmp_path):
    a = str(tmp_path / "a")
    _write(a)
    assert storage.exists(a)
    assert not storage.exists(str(tmp_path / "nope"))
    missing = storage.find_missing([a, str(tmp_path / "nope")])
    assert missing == [str(tmp_path / "nope")]
    assert storage.find_missing([]) == []


def test_exists_memory():
    fs, _ = storage.resolve_fs("memory://test-exists/")
    fs.pipe("memory://test-exists/f", b"x")
    assert storage.exists("memory://test-exists/f")
    assert not storage.exists("memory://test-exists/missing")
    assert storage.find_missing(
        ["memory://test-exists/f", "memory://test-exists/missing"]
    ) == ["memory://test-exists/missing"]


def test_download_memory_to_temp(tmp_path):
    fs, _ = storage.resolve_fs("memory://test-dl/")
    fs.pipe("memory://test-dl/fc2025030200+000CONTROL__dmi_sf", b"grib-bytes")
    staged = storage.download_to_temp(
        ["memory://test-dl/fc2025030206+000CONTROL__dmi_sf".replace("0206", "0200")],
        str(tmp_path / "stage"),
    )
    dst = os.path.join(staged, "fc2025030200+000CONTROL__dmi_sf")
    assert os.path.exists(dst)
    # second call skips existing
    storage.download_to_temp(
        ["memory://test-dl/fc2025030200+000CONTROL__dmi_sf"],
        str(tmp_path / "stage"),
    )


def test_download_partial_failure_cleans_up(tmp_path):
    fs, _ = storage.resolve_fs("memory://test-partial/")
    fs.pipe("memory://test-partial/good", b"x")
    with pytest.raises(RuntimeError, match="Failed to download 1"):
        storage.download_to_temp(
            ["memory://test-partial/good", "memory://test-partial/bad"],
            str(tmp_path / "stage"),
        )
    assert os.listdir(str(tmp_path / "stage")) == []


def test_upload_tree_local(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "b.txt").write_text("b")
    (src / "a.txt").write_text("a")
    uploaded = storage.upload_tree(str(src), str(tmp_path / "dst"))
    assert uploaded == [str(tmp_path / "dst" / "a.txt"), str(tmp_path / "dst" / "b.txt")]
    with pytest.raises(FileExistsError):
        storage.upload_tree(str(src), str(tmp_path / "dst"))


def test_upload_tree_memory(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    uploaded = storage.upload_tree(str(src), "memory://test-upload/dest")
    assert uploaded == ["memory://test-upload/dest/a.txt"]
    assert storage.exists("memory://test-upload/dest/a.txt")


def test_cleanup_temp(tmp_path):
    d = tmp_path / "stage"
    d.mkdir()
    (d / "f").write_text("x")
    storage.cleanup_temp(str(d))
    assert not d.exists()
    storage.cleanup_temp(str(tmp_path / "does-not-exist"))  # no error


def test_join():
    assert storage.join("s3://b/prefix", "a", "b") == "s3://b/prefix/a/b"
    assert storage.join("/mnt/root/", "f") == "/mnt/root/f"
