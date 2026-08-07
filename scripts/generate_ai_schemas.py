"""Generate or verify the committed versioned AI JSON Schemas."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel

from app.ai.schema_models import AssessmentResult, CvContent

SCHEMA_DIRECTORY = ROOT / "app" / "ai" / "schemas" / "v1"
SCHEMAS: dict[str, type[BaseModel]] = {
    "assessment-result.json": AssessmentResult,
    "cv-content.json": CvContent,
}


def serialized_schema(model: type[BaseModel]) -> str:
    """Return one deterministic JSON Schema document."""
    schema = model.model_json_schema(mode="validation")
    return (
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def generate(*, check: bool) -> bool:
    """Write schemas, or return whether all committed schemas are current."""
    current = True
    if not check:
        SCHEMA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMAS.items():
        path = SCHEMA_DIRECTORY / filename
        expected = serialized_schema(model)
        if check:
            if (
                not path.is_file()
                or path.read_text(encoding="utf-8") != expected
            ):
                print(f"AI schema drift: {path.relative_to(ROOT)}")
                current = False
        else:
            path.write_text(expected, encoding="utf-8")
            print(f"Wrote {path.relative_to(ROOT)}")
    return current


def main() -> int:
    """Run the schema generator command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when committed schemas have drifted",
    )
    arguments = parser.parse_args()
    return 0 if generate(check=arguments.check) else 1


if __name__ == "__main__":
    raise SystemExit(main())
