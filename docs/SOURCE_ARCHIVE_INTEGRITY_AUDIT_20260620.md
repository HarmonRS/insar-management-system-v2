# Source Archive Integrity Audit

Date: 2026-06-20

This document defines the LT-1 / Sentinel-1 source archive integrity audit flow. It is separate from normal source asset inventory scanning.

## Boundary

Normal asset inventory scan remains lightweight:

- Recursively discovers local `SOURCE_PRODUCT_DIRS`.
- Extracts LT-1 XML or Sentinel-1 `manifest.safe` metadata from archives.
- Builds metadata documents, preview caches, radar scene records, and orbit bindings.
- Uses `file_path + size_bytes + mtime_epoch + parser_version + parse_status` to skip unchanged archives.

Archive integrity audit is an explicit background task:

- Task type: `AUDIT_SOURCE_ARCHIVE_INTEGRITY`.
- API: `POST /assets/inventory/archive-integrity-audit`.
- Default formats: `LT1_ARCHIVE`, `S1_ZIP`.
- Default families: caller supplied; UI uses LT-1 and Sentinel-1.
- It can be started from the asset inventory panel or data monitor panel.

This prevents every metadata scan from fully reading multi-GB archives.

## Validation Method

ZIP archives use Python `zipfile.ZipFile.testzip()` and safe member path validation.

TAR, TGZ, and TAR.GZ archives use Python `tarfile.open(..., "r:*")` and full member iteration. For gzip-compressed tar streams, reaching EOF through `tarfile` validates the gzip stream CRC/truncation state. TAR members are also checked for unsafe paths, links, devices, and other unsupported special member types.

The audit records:

- `archive_integrity_status`: `NOT_CHECKED`, `OK`, `FAILED`, `UNSUPPORTED`
- `archive_integrity_method`
- `archive_integrity_checked_at`
- `archive_integrity_error`
- `archive_integrity_version`
- `archive_integrity_member_count`

## Incremental Semantics

An archive is skipped when all conditions hold:

- `force=false`
- `size_bytes` and `mtime_epoch` still match the filesystem
- `archive_integrity_version` equals the current audit version
- previous status is `OK`, `FAILED`, or `UNSUPPORTED`

If the archive changes or the audit version changes, the audit runs again. When normal metadata scanning reparses a changed source archive, it resets the integrity fields to `NOT_CHECKED`.

## Issue Handling

Audit failures create an open `asset_inventory_issues` row:

- `inventory_type=source_product`
- `asset_ref_id=<source_product_assets.id>`
- `issue_code=source_archive_integrity_failed`
- `severity=error`

When a later audit passes or marks the archive unsupported, the previous open integrity issue for that source asset is resolved.

## Operational Notes

The audit is intentionally I/O heavy. A 1.22 GB LT-1 `tar.gz` test archive with 8 members completed a full stream audit in about 9.8 seconds on the current server. Full-pool audits should be run manually, not on backend startup.

The original source archive remains the source of record and must not be deleted after materialization.
