"""Periodic runner (port of ``run.sh``).

Modes (see ``main``):

- one-shot (default): process a single analysis time — explicit
  ``--t-analysis`` or the most recent eligible one — then exit. Suitable
  for cron / Kubernetes Jobs.
- ``--watch``: infinite poll loop like ``run.sh`` (skip + sleep when refs
  already exist, retry conversion on failure, sleep between polls).
"""

import argparse
import datetime
import os
import time

import isodate
from loguru import logger

from .. import storage
from ..settings import Settings, load_settings, refs_dir_for, require_utc
from .cli_args import add_settings_arguments, settings_from_args
from .index_refs import build_indexes_and_refs

ANALYSIS_INTERVAL_SECONDS = 3 * 3600
DEFAULT_LAG_HOURS = 2
DEFAULT_POLL_INTERVAL = 300
DEFAULT_ALREADY_DONE_SLEEP = 1200


def _parse_t_analysis(value: str | None) -> datetime.datetime | None:
    if value is None:
        return None
    t = isodate.parse_datetime(value)
    return require_utc(t)


def compute_analysis_time(
    now: datetime.datetime, lag_hours: float = DEFAULT_LAG_HOURS
) -> datetime.datetime:
    """Nearest past 3-hour interval, minus ``lag_hours`` (ports ``run.sh``).

    ``run.sh`` does ``now - 7200s`` then floors to a 10800s grid.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    adjusted = now - datetime.timedelta(hours=lag_hours)
    epoch = int(adjusted.timestamp())
    rounded = epoch // ANALYSIS_INTERVAL_SECONDS * ANALYSIS_INTERVAL_SECONDS
    return datetime.datetime.fromtimestamp(rounded, tz=datetime.timezone.utc)


def refs_exist(t_analysis: datetime.datetime, settings: Settings) -> bool:
    """Check whether refs already exist for an analysis time."""
    return os.path.isdir(refs_dir_for(t_analysis, settings))


def _run_conversion(t_analysis: datetime.datetime, settings: Settings) -> None:
    # Lazy import: keeps this module side-effect free and patchable in tests.
    from ..__main__ import cli as convert

    t_str = require_utc(t_analysis).isoformat()
    convert(["--t_analysis", t_str, "--suite-name", settings.suite_name])


def process_one(
    t_analysis: datetime.datetime,
    settings: Settings,
    max_retries: int | None = None,
    retry_interval: float = 60,
) -> str:
    """Process one analysis time; return ``"skipped"`` or ``"done"``.

    ``max_retries=None`` retries forever (like ``run.sh``).
    """
    t_analysis = require_utc(t_analysis)
    if refs_exist(t_analysis, settings):
        logger.info(f"Refs already exist for analysis time {t_analysis.isoformat()}")
        return "skipped"

    logger.info(f"Creating indexes/refs for analysis time {t_analysis.isoformat()}")
    build_indexes_and_refs(t_analysis, settings)

    attempts = 0
    while True:
        try:
            _run_conversion(t_analysis, settings)
        except Exception:
            attempts += 1
            if max_retries is not None and attempts > max_retries:
                raise
            logger.warning("Zarr conversion failed, retrying...")
            time.sleep(retry_interval)
            continue
        break

    logger.info(f"Zarr conversion successful for analysis time {t_analysis.isoformat()}")
    if settings.src_grib_temp_path and os.path.isdir(settings.src_grib_temp_path):
        storage.cleanup_temp(settings.src_grib_temp_path)
    return "done"


def poll_once(
    settings: Settings,
    now: datetime.datetime | None = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    already_done_sleep: float = DEFAULT_ALREADY_DONE_SLEEP,
    **process_kwargs,
) -> float:
    """Run a single watch iteration; return seconds to sleep next."""
    t_analysis = compute_analysis_time(
        now or datetime.datetime.now(datetime.timezone.utc)
    )
    if refs_exist(t_analysis, settings):
        logger.info(
            f"Refs already exist for analysis time {t_analysis.isoformat()} "
            f"({refs_dir_for(t_analysis, settings)}). "
            f"Sleeping for {already_done_sleep}s..."
        )
        return already_done_sleep
    process_one(t_analysis, settings, **process_kwargs)
    return poll_interval


def watch_loop(
    settings: Settings,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    already_done_sleep: float = DEFAULT_ALREADY_DONE_SLEEP,
    **process_kwargs,
) -> None:
    """Infinite poll loop (the ``run.sh`` ``while true``)."""
    while True:
        sleep_for = poll_once(
            settings,
            poll_interval=poll_interval,
            already_done_sleep=already_done_sleep,
            **process_kwargs,
        )
        logger.info(f"Sleeping for {sleep_for}s...")
        time.sleep(sleep_for)


def main(argv=None) -> None:
    """CLI: ``python -m zarr_creator.pipeline.runner [--t-analysis X | --watch]``."""
    parser = argparse.ArgumentParser(description="Run the NWP zarr conversion pipeline")
    parser.add_argument("--t-analysis", default=None)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--already-done-sleep", type=float, default=DEFAULT_ALREADY_DONE_SLEEP)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-interval", type=float, default=60)
    parser.add_argument("--log-level", default="INFO")
    add_settings_arguments(parser)
    args = parser.parse_args(argv)

    logger.remove()
    import sys

    logger.add(sys.stderr, level=args.log_level.upper())

    settings = settings_from_args(args)
    logger.info(f"SRC_GRIB_ROOT_URI: {settings.src_grib_root_uri}")
    logger.info(f"REFS_ROOT_PATH: {settings.refs_root_path}")
    logger.info(f"SRC_GRIB_TEMP_PATH: {settings.src_grib_temp_path or 'not set'}")
    logger.info(f"SUITE_NAME: {settings.suite_name}")

    if args.watch:
        watch_loop(
            settings,
            poll_interval=args.poll_interval,
            already_done_sleep=args.already_done_sleep,
            max_retries=args.max_retries,
            retry_interval=args.retry_interval,
        )
        return

    t_analysis = _parse_t_analysis(args.t_analysis) or compute_analysis_time(
        datetime.datetime.now(datetime.timezone.utc)
    )
    process_one(
        t_analysis,
        settings,
        max_retries=args.max_retries,
        retry_interval=args.retry_interval,
    )


if __name__ == "__main__":
    main()
