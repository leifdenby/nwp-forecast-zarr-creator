"""Shared CLI helpers for pipeline stages.

Every option maps 1:1 to an environment variable; precedence is
explicit flag > env > built-in default (see ``zarr_creator.settings``).
"""

import argparse

from ..settings import Settings, load_settings


def add_settings_arguments(parser: argparse.ArgumentParser) -> None:
    """Add settings-override flags mirroring every env var."""
    parser.add_argument("--src-grib-root-uri", default=None)
    parser.add_argument("--refs-root-path", default=None)
    parser.add_argument("--member-id", default=None)
    parser.add_argument("--max-hour", type=int, default=None)
    parser.add_argument("--suite-name", default=None)
    parser.add_argument("--src-grib-temp-path", default=None)
    parser.add_argument("--dst-zarr-output-path", default=None)
    parser.add_argument("--source-profile", default=None)
    parser.add_argument("--dest-profile", default=None)
    parser.add_argument("--src-anon", action="store_true", default=False)


def settings_from_args(
    args: argparse.Namespace, base: Settings | None = None
) -> Settings:
    """Apply explicit CLI flags over env-loaded settings (in place)."""
    settings = base or load_settings()
    if args.src_grib_root_uri is not None:
        settings.src_grib_root_uri = args.src_grib_root_uri
    if args.refs_root_path is not None:
        settings.refs_root_path = args.refs_root_path
    if args.member_id is not None:
        settings.member_id = args.member_id
    if args.max_hour is not None:
        settings.max_hour = args.max_hour
    if args.suite_name is not None:
        settings.suite_name = args.suite_name
    if args.src_grib_temp_path is not None:
        # Empty string disables staging (same as unset env var).
        settings.src_grib_temp_path = args.src_grib_temp_path or None
    if args.dst_zarr_output_path is not None:
        settings.dst_zarr_output_path = args.dst_zarr_output_path
    if args.source_profile is not None:
        settings.src_aws_profile = args.source_profile
    if args.dest_profile is not None:
        settings.dst_aws_profile = args.dest_profile
    if args.src_anon:
        settings.src_anon = True
    return settings
