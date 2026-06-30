# Source And Orbit Asset Scan Operations

Last updated: 2026-06-26

This document records the current operational contract for LT-1 and Sentinel-1 source-product and precise-orbit asset scans.

## Source Product Scan

Source-product scans are incremental, but they are not implemented as a strict two-phase "discover everything first, parse later" pipeline. The current implementation streams candidates:

1. Enumerate one candidate archive or SAFE path.
2. Read file stat information.
3. Check the existing `source_product_assets` row by normalized path.
4. Skip unchanged cached rows before parsing metadata.
5. Submit only changed or new rows to the source metadata parser.

The log field `skipped=N` means unchanged candidates were filtered before metadata extraction. The log field `changed/new=N` means candidates that missed the cache check and were submitted for metadata parsing.

An unchanged source asset can be skipped only when the cached row is active, belongs to the same root, has the current parser version, has an allowed parse status, and has matching file size and mtime.

## Source Metadata Parser Isolation

Source archive metadata parsing runs in a process-backed worker pool. This is intentional: a single corrupt or slow archive can block a Python thread indefinitely, while a child process can be terminated and replaced.

Current defaults:

```env
ASSET_SCAN_PARSE_WORKERS=16
ASSET_SCAN_PARSE_INFLIGHT=64
ASSET_SCAN_PARSE_TIMEOUT_SECONDS=600
```

Runtime guardrails:

- `ASSET_SCAN_PARSE_WORKERS` is capped at 32 inside the service.
- `ASSET_SCAN_PARSE_INFLIGHT` is capped at `workers * 4`.
- Each source file parse has a per-file timeout. On timeout, the worker process is terminated, the file is recorded as a parse issue, and the scan continues.
- Unexpected parser process exit is handled as a per-file parse failure, then the worker slot is restarted.
- Stale results from replaced worker generations are ignored.

On the current production server, `16/64` is the baseline configuration. During the 2026-06-26 scan audit, this used about 12% total CPU, kept more than 100 GB memory available, and drove the D: source archive volume at roughly 1.3 GB/s reads. If future scans remain stable and disk queue length stays reasonable, `24/96` can be tested, but `16/64` is the current default.

## Orbit Asset Scan

Precise-orbit scans are incremental. Unchanged orbit files are skipped before metadata parsing when the cached row is active, belongs to the same root, has native format `TXT` or `EOF`, has the current parser version, has parse status `OK`, and has matching file size and mtime.

LT-1 orbit scans also synchronize the configured production TXT orbit pool when applicable. Sentinel-1 EOF files remain registered as orbit assets and are used for scene binding.

## Orbit Binding After Scan

Asset inventory scan requests default to `bind_orbits=true`. The frontend asset registration buttons also pass `bind_orbits: true`.

After a source scan or an orbit scan finishes, the backend runs scene-to-orbit binding. This is deliberately symmetric:

- If source products are scanned first and precise orbits are scanned later, the later orbit scan binds existing scenes.
- If precise orbits are scanned first and source products are scanned later, the later source scan binds the new scenes.

The binding step is currently a full re-evaluation of active LT-1/Sentinel-1 source scenes with source-product references. The scan itself is incremental; the binding pass is not yet limited to changed scenes or changed orbit windows.

## Operational Notes

If a scan must be stopped before retrying with new concurrency settings, stop the running worker processes and mark the active `system_jobs`, `system_tasks`, and `asset_inventory_states` rows as terminal failed states. This prevents the job queue from recovering and re-claiming the old job.

The main application worker is expected to support more than one queued job at a time so long-running preview generation does not block independent production jobs. Current main-server baseline:

```env
JOB_WORKER_CONCURRENCY=2
JOB_WORKER_ALLOWED_TYPES=
```

Do not start ad-hoc one-off workers during formal testing. Change the configured worker concurrency, stop stale processes, and let the operator restart the application normally.

Recommended health checks during a source scan:

- Task log should show `workers=16` and `pending=64` / `active_or_queued=64` after the parser pool fills.
- `completed=X/Y` should keep increasing.
- Worker warnings such as `parse worker exited unexpectedly` or parse timeout should remain rare and file-specific.
- D: disk read throughput and queue length are better indicators than total CPU on the current server.
