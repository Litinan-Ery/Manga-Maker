from __future__ import annotations

from pathlib import Path

from backend.app.contracts.v03 import rendered_schemas, schema_directory


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    destination = schema_directory(root)
    destination.mkdir(parents=True, exist_ok=True)
    for filename, content in rendered_schemas().items():
        (destination / filename).write_bytes(content)


if __name__ == "__main__":  # pragma: no cover - exercised through the module command
    main()
