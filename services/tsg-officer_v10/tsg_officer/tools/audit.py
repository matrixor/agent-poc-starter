from __future__ import annotations

from typing import Any, Dict

from tsg_officer.state.models import AuditEvent, now_iso


def make_event(event: str, details: Dict[str, Any] | None = None) -> AuditEvent:
    return {
        "ts": now_iso(),
        "event": event,
        "details": details or {},
    }
