from __future__ import annotations

import json
from pathlib import Path

from tsg_officer.schemas.models import (
    ChecklistItemModel,
    ChecklistReportModel,
    FlowchartModel,
    ApplicationTypeModel,
)

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "tsg_officer" / "schemas" / "json"


def write_schema(name: str, schema: dict) -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    path = SCHEMA_DIR / f"{name}.schema.json"
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


def main() -> None:
    write_schema("checklist_item", ChecklistItemModel.model_json_schema())
    write_schema("checklist_report", ChecklistReportModel.model_json_schema())
    write_schema("flowchart", FlowchartModel.model_json_schema())
    write_schema("application_type", ApplicationTypeModel.model_json_schema())


if __name__ == "__main__":
    main()
