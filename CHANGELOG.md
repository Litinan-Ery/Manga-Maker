# Changelog

## 0.2.2 - 2026-09-06

### Added

- Generate and adopt V5 Full manga pages with multiple panels, then edit Chinese lettering locally.
- Import locally authored storyboards, character/style bibles and prompt plans for reviewed production.
- Import Markdown sources with chapter suggestions that preserve source offsets and ignore headings inside fenced blocks.
- Export a complete book across chapters as PNG, PDF, CBZ and an editable project package.
- Save immutable production checkpoints, inspect bounded evidence and hand off long-running work without losing the complete page scope. A workbench panel and CLI expose recovery and separate generation, lettering and review progress.

### Changed

- Apply approved panel composition, camera framing, focus and reserved lettering space to prompts and rendered pages; select only the characters visible in each panel.
- Bound production batches to five pages, serialize manifest writers and query existing results before resuming interrupted generation.
- Document required covers and an artist-style question before production as planned features; these requirements are not implemented in this release.

### Fixed

- Clear full-page previews and confirmations when the source chapter set changes, including late results from the previous chapter set.
- Preserve approved page layouts and improve Chinese line breaking, text fitting and local lettering checks.
- Reject expired checkpoint writers, stale recovery revisions and changed source, image or layout dependencies instead of treating them as resumable or reviewed work.
- Pause registered production and offer local handoff when reported budgets or compaction failures require it. Real Codex context replacement remains unsupported by the default host.

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
