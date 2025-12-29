from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from tsg_officer.tools.llm import MockLLMClient
from tsg_officer.tools.rules import YamlRuleRepository


def test_checklist_report_schema_valid():
    root = Path(__file__).resolve().parent.parent
    schema_path = root / "tsg_officer" / "schemas" / "json" / "checklist_report.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    rules_repo = YamlRuleRepository(root / "data" / "rules" / "sample_rules.yaml")
    rules = [r.to_dict() for r in rules_repo.list_rules("building_permit")]

    llm = MockLLMClient()
    report = llm.generate_checklist_report(
        case_id="case-123",
        application_type="building_permit",
        rules=rules,
        submission_text="APN 123-456-789. Scope: Replace HVAC. Provide training logs.",
    ).model_dump()

    jsonschema.validate(report, schema)
