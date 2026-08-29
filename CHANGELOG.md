# Changelog

## 0.2.1 - 2026-08-29

### Added

- NovelAI Diffusion V5 Full capability contract, provider mapping, connection checks, usage-limit verification, and real ZIP image-response handling.
- Storyboard 1.1 page classification and approval policy: standard pages require 3-6 panels, while cover, splash, and special pages allow 1-6 panels.
- A reproducible 12-page Sandkings acceptance workflow with audited PNG, PDF, CBZ, contact-sheet, and manifest outputs.

### Changed

- Unified project text-model settings in the local credential panel and refreshed dependent workbenches without discarding unsaved storyboard edits.
- Clarified that Opus/V5 eligibility checks are not a NovelAI billing guarantee.

### Fixed

- Invalidate frozen generation approvals when a NovelAI credential is rotated or deleted, with serialized final credential reads to prevent account switching races.
- Reject stale NovelAI contracts before a connection test can mark them valid.
- Keep NovelAI verification state accurate across vault lock, unlock, profile changes, and unsaved model selections.
- Enforce page policy on generation, manual revision, approval, layout, page drafting, and bible generation while retaining Storyboard 1.0 as read-only history.
