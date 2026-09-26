"""Tests for zarr_creator.pipeline.runner."""

import datetime
import os

import pytest

from zarr_creator.pipeline import runner
from zarr_creator.settings import Settings


def _utc(*args):
    return datetime.datetime(*args, tzinfo=datetime.timezone.utc)


def _settings(**overrides):
    base = dict(
        src_grib_root_uri="/nonexistent",
        refs_root_path="/tmp/refs",
        member_id="CONTROL__dmi",
        max_hour=36,
        suite_name="dini",
        src_grib_temp_path=None,
        dst_zarr_output_path="file:///tmp/out/{dataset_id}.zarr",
        src_aws_profile=None,
        dst_aws_profile=None,
    )
    base.update(overrides)
    return Settings(**base)


def test_compute_analysis_time_boundaries():
    # 05:00 UTC, lag 2h -> adjusted 03:00 -> floor 03:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 5)) == _utc(2025, 3, 2, 3)
    # exactly on interval: 08:00 - 2h = 06:00 -> 06:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 8)) == _utc(2025, 3, 2, 6)
    # just after interval: 08:01 - 2h = 06:01 -> 06:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 8, 1)) == _utc(2025, 3, 2, 6)
    # midnight wrap: 01:00 - 2h = 23:00 prev day -> 21:00
    assert runner.compute_analysis_time(_utc(2025, 3, 2, 1)) == _utc(2025, 3, 1, 21)
    # naive datetimes treated as UTC
    assert runner.compute_analysis_time(datetime.datetime(2025, 3, 2, 5)) == _utc(
        2025, 3, 2, 3
    )


def test_refs_exist(tmp_path):
    settings = _settings(refs_root_path=str(tmp_path))
    t = _utc(2025, 3, 2, 6)
    assert not runner.refs_exist(t, settings)
    os.makedirs(tmp_path / "CONTROL__dmi" / "2025-03-02T0600Z.jsons")
    assert runner.refs_exist(t, settings)


def test_process_one_skips_when_refs_exist(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path))
    t = _utc(2025, 3, 2, 6)
    os.makedirs(tmp_path / "CONTROL__dmi" / "2025-03-02T0600Z.jsons")
    called = []
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: called.append(a))
    assert runner.process_one(t, settings) == "skipped"
    assert called == []


def test_process_one_success_cleans_temp(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    settings = _settings(
        refs_root_path=str(tmp_path / "refs"), src_grib_temp_path=str(stage)
    )
    t = _utc(2025, 3, 2, 6)
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: "refs")
    conversions = []
    monkeypatch.setattr(runner, "_run_conversion", lambda *a: conversions.append(a))
    assert runner.process_one(t, settings) == "done"
    assert len(conversions) == 1
    assert not stage.exists()


def test_process_one_retries_then_succeeds(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    t = _utc(2025, 3, 2, 6)
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: "refs")
    attempts = []

    def flaky(*a):
        attempts.append(a)
        if len(attempts) < 3:
            raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_run_conversion", flaky)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    assert runner.process_one(t, settings, retry_interval=0) == "done"
    assert len(attempts) == 3


def test_process_one_max_retries_exceeded(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    t = _utc(2025, 3, 2, 6)
    monkeypatch.setattr(runner, "build_indexes_and_refs", lambda *a: "refs")
    monkeypatch.setattr(
        runner, "_run_conversion", lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        runner.process_one(t, settings, max_retries=1, retry_interval=0)


def test_poll_once_existing_refs_sleeps_long(tmp_path):
    settings = _settings(refs_root_path=str(tmp_path))
    now = _utc(2025, 3, 2, 8, 30)  # -> analysis 06:00
    os.makedirs(tmp_path / "CONTROL__dmi" / "2025-03-02T0600Z.jsons")
    assert runner.poll_once(settings, now=now) == runner.DEFAULT_ALREADY_DONE_SLEEP


def test_poll_once_processes_and_sleeps_poll(tmp_path, monkeypatch):
    settings = _settings(refs_root_path=str(tmp_path / "refs"))
    now = _utc(2025, 3, 2, 8, 30)
    seen = []
    monkeypatch.setattr(
        runner, "process_one", lambda t, s, **k: seen.append(t) or "done"
    )
    assert runner.poll_once(settings, now=now) == runner.DEFAULT_POLL_INTERVAL
    assert seen == [_utc(2025, 3, 2, 6)]
