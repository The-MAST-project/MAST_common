# Filer frame-transfer tests

Integration tests for the acquisition frame-transfer lifecycle (the
RAM-disk -> shared `Filer`, issue
[The-MAST-project/MAST_unit.2024-12-12#18](https://github.com/The-MAST-project/MAST_unit.2024-12-12/issues/18)).

They drive the **real `Filer`** against temp directories and real background threads --
**no hardware, no astrometry.net, no Mongo** -- so they run anywhere. `filer.py` is
self-contained (its only platform import is `win32api` on Windows), imported directly off
`sys.path`; the suite skips cleanly if `pywin32` is unavailable.

## What they cover

The write-safety contract (`Filer.atomic_path`: write to `<name>.part`, atomic rename on
close) and the mover: a reader never sees a partial final, the temp is cleaned on error,
`*.part` sources are skipped, the destination is published atomically, folder moves skip
in-flight parts, plus `clean_ram_tmp`. They also cover the `TransferTracker` layer:
per-move audit logging, per-tag reconciliation, `snapshot()` counts, `wait_for`/`wait_for_tag`
notifications, and that a file's write + move share one lifecycle record.

## Running

```
pytest src/common/tests/test_frame_transfer.py -v
```

Needs only `pytest` (and `pywin32` on Windows). No fixtures to download.
