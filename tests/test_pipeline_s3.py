"""S3 end-to-end test against the frozen public fixture bucket.

Runs the full pipeline (index -> refs -> zarr) for the fixture's trimmed
analysis time. Gated behind ``RUN_S3_E2E=1`` plus fixture coordinates::

    RUN_S3_E2E=1     FIXTURE_SRC_URI=s3://<bucket>/<suite>/<analysis>/ml \\
        FIXTURE_T_ANALYSIS=2025-03-02T00:00:00Z FIXTURE_MAX_HOUR=2 \\
        FIXTURE_SUITE_NAME=dini \\
        uv run pytest -m integration

Reads are unsigned (``SRC_ANON=1``); output goes to a local temp dir.
The conversion ``--suite-name`` follows ``FIXTURE_SUITE_NAME``.
"""

import datetime
import os

import isodate
import pytest
import xarray as xr

pytestmark = pytest.mark.integration

REQUIRED = ("FIXTURE_SRC_URI", "FIXTURE_T_ANALYSIS", "FIXTURE_MAX_HOUR")


def _enabled():
    return os.environ.get("RUN_S3_E2E", "").lower() in {"1", "true", "yes"}


def _config():
    if not _enabled():
        pytest.skip("Set RUN_S3_E2E=1 to run the S3-backed e2e test")
    missing = [v for v in REQUIRED if not os.environ.get(v)]
    if missing:
        pytest.skip(f"Missing fixture env vars: {missing}")
    return (
        os.environ["FIXTURE_SRC_URI"],
        isodate.parse_datetime(os.environ["FIXTURE_T_ANALYSIS"]),
        int(os.environ["FIXTURE_MAX_HOUR"]),
        os.environ.get("FIXTURE_SUITE_NAME", "dini"),
    )


def test_s3_fixture_end_to_end(tmp_path):
    from zarr_creator.pipeline.index_refs import build_indexes_and_refs
    from zarr_creator.settings import Settings, refs_dir_for, require_utc

    src_uri, t_analysis, max_hour, suite_name = _config()
    t_analysis = require_utc(t_analysis)

    settings = Settings(
        src_grib_root_uri=src_uri,
        refs_root_path=str(tmp_path / "refs"),
        member_id="CONTROL__dmi",
        max_hour=max_hour,
        suite_name=suite_name,
        src_grib_temp_path=str(tmp_path / "stage"),
        dst_zarr_output_path=f"file://{tmp_path}/out/{{dataset_id}}.zarr",
        src_aws_profile=None,
        dst_aws_profile=None,
        src_anon=True,
    )
    refs_dir = build_indexes_and_refs(t_analysis, settings)
    assert refs_dir == refs_dir_for(t_analysis, settings)
    assert os.path.isdir(refs_dir)
    assert [f for f in os.listdir(refs_dir) if f.endswith(".json")] != []

    from zarr_creator.__main__ import cli as convert

    convert(
        [
            "--t_analysis",
            t_analysis.isoformat(),
            "--suite-name",
            suite_name,
            "--refs-root-path",
            str(tmp_path / "refs"),
            "--dst-zarr-output-path",
            f"file://{tmp_path}/out/{{dataset_id}}.zarr",
        ]
    )
    for part in ("single_levels", "pressure_levels", "height_levels"):
        ds = xr.open_zarr(f"{tmp_path}/out/{part}.zarr")
        assert len(ds.data_vars) > 0
        assert ds.sizes["time"] == max_hour + 1
        assert not all(bool(ds[v].isnull().all()) for v in ds.data_vars)
