# Watch mode: process unfinished cycles within a retention window

## Context
In Oct 2026, staging produced zarr output only for the first cycle in 4 days. `watch.log` showed that each cycle's missing-file count stayed frozen for its whole polling window, and a container restart fixed it. So the root cause was a stale view of the `/mnt/harmonie-data-from-pds` mount inside the long-running container, and that is handled separately.

It also exposed a weakness in the watcher. `--watch` only ever looks at the single latest cycle T (`compute_analysis_time(now)` = `floor(now − 2h)` to the 3h grid), so T only gets a chance during `[T+2h, T+5h)`. Any cycle missed in that window, whether from late data, a stale mount, downtime or a restart, is never processed.

Goal: on every poll, check which cycles within a **retention period (default 1 week)** have not been processed yet. Log them, and process the newest one that is complete and not yet marked `.done`.

## Scope
This is for watch mode only. The lookback lives in `poll_once`, which only `watch_loop` calls. One-shot mode (`--t-analysis X`, or the default `latest`) is unchanged: `main` still calls `process_one` for that single analysis time and exits.

## Changes

### `zarr_creator/settings.py`
- Add `DEFAULT_RETENTION = datetime.timedelta(days=7)`.
- Add `recent_analysis_times(now, retention, lag_hours=DEFAULT_LAG_HOURS) -> list[datetime]`. It starts at `compute_analysis_time(now, lag_hours)` and steps back by `ANALYSIS_INTERVAL_SECONDS` while still within `now - retention`, returning the times newest first.

### `zarr_creator/storage.py`
- Add `any_missing(urls, profile, anon) -> bool`. It stops at the first missing URL and checks the URLs in reverse order (highest forecast hour first), because late hours are the ones most likely to be missing.
  - Reason: a poll covers about 56 cycles, and `find_missing` checks all 74 files of each one. Stopping early means an incomplete cycle costs about one existence check.

### `zarr_creator/pipeline/runner.py`
- Change the signature to `poll_once(settings, now=None, poll_interval, already_done_sleep, retention=DEFAULT_RETENTION, **process_kwargs)`. On each poll:
  1. Compute `recent_analysis_times(now, retention)` and split it into done (`refs_done`, a cheap local marker check) and not yet processed.
  2. Log one summary line per poll, for example `"N/M cycles in last 7d done; not yet processed: 2026-10-05T00Z, 2026-10-04T21Z, ..."`. Only list cycle times, with no missing-file counts, so the short-circuit still works.
  3. Go through the not-yet-processed cycles, newest first:
     - Skip a cycle (with a debug log) if `storage.any_missing(...)` is true. Build the URLs the same way `build_indexes_and_refs` does, with `expected_grib_filenames` and `storage.join`.
     - Otherwise call `process_one(t, ...)`, keeping the existing `FileNotFoundError` catch: log a warning and continue with the next cycle.
     - After a success, **return 0** so the next poll starts again from the newest cycle. A new cycle then takes priority over the backlog, and the backlog still drains without waiting 5 minutes per item.
  4. If no cycle was processed, return `already_done_sleep` when the latest cycle is done, and `poll_interval` otherwise. This is the current sleep behaviour.
- In `process_one`'s conversion retry, change `logger.warning("Zarr conversion failed, retrying...")` to `logger.exception(...)` so the traceback is logged. The retry behaviour stays the same.
- In `main`, add `--retention-days` (float, default 7). It only has an effect with `--watch`, like `--poll-interval`, and its help text should say so. Pass it through `watch_loop` to `poll_once` as `timedelta(days=...)`. Update the `--watch` help text to: "...processes any unfinished analysis time from the last RETENTION_DAYS".

### Docs
- `README.md` (watch-mode description) and `CHANGELOG.md`: describe the lookback and `--retention-days`.

## Things to watch out for
- The `.done` markers live in `REFS_ROOT_PATH` (`/app/refs` in the container). If that directory is not on a persistent volume, a restart reconverts up to a week of cycles. `to_zarr(mode="w")` overwrites, so the output stays correct, but the work is wasted.
- A cycle whose conversion fails every time still blocks the loop, because retries are unlimited by default. That is unchanged; `--max-retries` lets it give up.
- The lookback does not help against a stale source mount. If the container can't see the files, it can't see them for older cycles either.

## Tests
- `recent_analysis_times` returns times newest first in 3-hour steps, 56–57 entries for 7 days, with the boundary handled correctly.
- `poll_once`, with a fixed `now` and monkeypatched `process_one` and `storage.any_missing`:
  - when the latest cycle is incomplete but T−3h is complete, it processes T−3h and returns 0,
  - when all cycles are done, it returns `already_done_sleep`,
  - cycles older than the retention window are never considered,
  - when two cycles are both complete, the newer one is processed first.
- One-shot `main` (no `--watch`) still processes exactly one analysis time. Extend `test_main_one_shot_resolves_t_analysis` for this.
- Update the existing `poll_once` tests in `tests/test_runner.py`.
- `storage.any_missing` stops at the first missing file (test on local `tmp_path` files).

## Verification
- `uv run pytest`
- Manual check: point `--src-grib-root-uri` at a local directory with fixture GRIBs for an analysis time a few cycles back, run `uv run python -m zarr_creator run --watch`, and check that the older cycle gets picked up and the summary line is logged.
