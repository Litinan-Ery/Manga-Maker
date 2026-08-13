# Frontend feature boundary

Each v0.3 feature exposes only its `index.ts`/`public.ts`. A feature may import its own files, `shared/ui`, and `generated/api`; it must not import another feature's internal component, state, or client. Cross-feature order and workflows belong in `app/`.

The root `App.tsx`, root components, and `api.ts` are the v0.2 compatibility seam. They remain operational during incremental migration, but v0.3 DTOs/endpoints must be added to the owning feature client. `api.ts` is removed only after every legacy component has migrated and the MM-024 frontend fixtures still pass.

Fixture clients implement the same public client interface as HTTP clients and return frozen canonical contract shapes. They are test/dev harnesses only: they never fall back to the legacy API or pretend a backend write occurred.
