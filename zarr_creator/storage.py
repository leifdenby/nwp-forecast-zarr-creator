"""fsspec-based storage abstraction.

Single place where URLs become filesystems. All callers pass opaque URLs
(``s3://bucket/prefix/...`` or local paths) and let fsspec dispatch —
no ``is_s3`` branching outside this module.

S3 authentication is profile-only: pass ``profile=<name>`` and endpoint,
keys, and region resolve from ``~/.aws/config`` + ``~/.aws/credentials``
via botocore/s3fs.
"""

import os
import shutil

import fsspec
from loguru import logger


def resolve_fs(url: str, profile: str | None = None, anon: bool = False):
    """Resolve ``(filesystem, path)`` for any URL via fsspec.

    ``profile``/``anon`` are only forwarded for ``s3://`` URLs. ``anon``
    enables unsigned reads of public buckets (CI fixture consumption).
    """
    if url.startswith("s3://"):
        kwargs: dict = {"anon": anon}
        if profile is not None:
            kwargs["profile"] = profile
        return fsspec.url_to_fs(url, **kwargs)
    return fsspec.url_to_fs(url)


def exists(url: str, profile: str | None = None, anon: bool = False) -> bool:
    """Check existence of a single URL."""
    fs, path = resolve_fs(url, profile, anon)
    return fs.exists(path)


def find_missing(
    urls: list[str], profile: str | None = None, anon: bool = False
) -> list[str]:
    """Return the subset of ``urls`` that do not exist."""
    if not urls:
        return []
    # Re-resolve per URL so mixed-protocol lists still work.
    missing = []
    for url in urls:
        fs_u, path_u = resolve_fs(url, profile, anon)
        if not fs_u.exists(path_u):
            missing.append(url)
    return missing


def join(root_uri: str, *parts: str) -> str:
    """Join names onto a root URI (works for ``s3://`` and local paths)."""
    root_uri = root_uri.rstrip("/")
    return root_uri + "/" + "/".join(p.strip("/") for p in parts)


def download_to_temp(
    src_urls: list[str],
    tmpdir: str,
    profile: str | None = None,
    anon: bool = False,
) -> str:
    """Download ``src_urls`` into ``tmpdir`` (flat layout), return ``tmpdir``.

    Skips files that already exist locally (mirrors ``rsync`` /
    ``download_harmonie_data.sh`` behavior). On partial failure, files
    downloaded in this call are deleted and the error re-raised.
    """
    os.makedirs(tmpdir, exist_ok=True)
    downloaded_this_attempt: list[str] = []
    failed: list[str] = []
    for url in src_urls:
        fs, src_path = resolve_fs(url, profile, anon)
        dst = os.path.join(tmpdir, os.path.basename(src_path.rstrip("/")))
        if os.path.exists(dst):
            logger.info(f"Skipping existing: {dst}")
            continue
        logger.info(f"Downloading: {url}")
        try:
            fs.get_file(src_path, dst)
            downloaded_this_attempt.append(dst)
        except Exception:
            logger.error(f"Failed: {url}")
            failed.append(url)
    if failed:
        for dst in downloaded_this_attempt:
            try:
                os.remove(dst)
            except OSError:
                pass
        raise RuntimeError(f"Failed to download {len(failed)} file(s): {failed}")
    return tmpdir


def upload_tree(
    local_dir: str,
    dest_root_uri: str,
    profile: str | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Upload all files under ``local_dir`` (flat) to ``dest_root_uri``."""
    fs, dest_path = resolve_fs(dest_root_uri, profile)
    uploaded = []
    protocol = fs.protocol
    if isinstance(protocol, (tuple, list)):
        protocols = set(protocol)
    else:
        protocols = {protocol}
    is_local = bool(protocols & {"file", "local"})
    if is_local:
        os.makedirs(dest_path, exist_ok=True)
    for name in sorted(os.listdir(local_dir)):
        src = os.path.join(local_dir, name)
        if not os.path.isfile(src):
            continue
        if is_local:
            dst = os.path.join(dest_path, name)
            if not overwrite and os.path.exists(dst):
                raise FileExistsError(f"Destination already exists: {dst}")
            shutil.copy2(src, dst)
            uploaded.append(dst)
        else:
            dst_path = dest_path.rstrip("/") + "/" + name
            if not overwrite and fs.exists(dst_path):
                raise FileExistsError(f"Destination already exists: {dest_root_uri}/{name}")
            fs.put_file(src, dst_path)
            uploaded.append(f"{dest_root_uri.rstrip('/')}/{name}")
    return uploaded


def cleanup_temp(path: str) -> None:
    """Remove a staging directory (replaces ``rm -rf`` in ``run.sh``)."""
    if path and os.path.isdir(path):
        logger.info(f"Deleting temporary storage {path}")
        shutil.rmtree(path)
