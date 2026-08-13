# v0.3 Canonical Schemas

These Draft 2020-12 JSON Schemas are generated from the frozen Pydantic contracts with:

```bash
uv run python -m scripts.export_v03_schemas
```

The checked-in files are the cross-language contract source consumed by frontend clients, migration readers, and fixtures. CI compares them byte-for-byte with the Pydantic export; hand edits are not accepted.

## Compatibility policy

- Every top-level document has a literal `schema_version`, stable UUID, monotonic `version`, and one or more SHA-256 fields.
- Additive optional fields may remain in the same schema version only when every existing consumer fixture still passes.
- Required-field, enum, meaning, normalization, or hash-input changes create a new schema version. The old schema remains until all consumer fixtures and v0.2 migration fixtures move.
- Parsers reject unknown schema versions and unknown fields. There is no silent downgrade, flat-prompt fallback, or best-effort provider mapping.
- Secrets, authorization headers, complete source chapters, and local absolute paths are outside these contracts. Contract objects use stable IDs and project-relative artifact references instead.

## Canonical JSON and hashes

Hash input is UTF-8 canonical JSON: object keys use UTF-16 code-unit order, arrays retain declared order, strings contain valid Unicode, finite numbers use ECMAScript shortest representation, and no insignificant whitespace is emitted. Integer inputs that cannot be represented exactly as IEEE-754 are rejected. SHA-256 is lowercase hexadecimal.

`contracts/fixtures/v0.3/canonical-hash.json` is the consumer vector. Python and TypeScript must produce the exact `canonical_json` and `sha256` values before a schema or hash rule can change.
