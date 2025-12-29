from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import yaml


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    description: str
    severity: str = "INFO"           # BLOCKER | WARN | INFO
    applies_to: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    question: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "applies_to": self.applies_to or [],
            "keywords": self.keywords or [],
            "question": self.question or "",
        }


class RuleRepository(Protocol):
    def list_rules(self, application_type: str) -> List[Rule]:
        ...


class YamlRuleRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def list_rules(self, application_type: str) -> List[Rule]:
        if not self.path.exists():
            return []
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        rules_raw = data.get("rules", [])
        rules: List[Rule] = []
        for r in rules_raw:
            applies = r.get("applies_to") or []
            if applies and application_type not in applies:
                continue
            rules.append(
                Rule(
                    rule_id=r.get("rule_id", ""),
                    title=r.get("title", ""),
                    description=r.get("description", ""),
                    severity=r.get("severity", "INFO"),
                    applies_to=applies,
                    keywords=r.get("keywords") or [],
                    question=r.get("question") or None,
                )
            )
        return rules
