from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence

from tsg_officer.schemas.models import (
    ApplicationTypeModel,
    ChecklistReportModel,
    ChecklistItemModel,
    FlowchartModel,
)
from tsg_officer.state.models import now_iso


class LLMClient(Protocol):
    """Small interface so you can swap providers (OpenAI, Azure, internal LLM, etc.)."""

    def classify_application_type(self, user_text: str) -> ApplicationTypeModel:
        ...

    def generate_checklist_report(
        self,
        *,
        case_id: str,
        application_type: str,
        rules: List[Dict[str, Any]],
        submission_text: str,
    ) -> ChecklistReportModel:
        ...

    def generate_flowchart(self, *, process_description: str) -> FlowchartModel:
        ...


@dataclass
class MockLLMClient:
    """Deterministic placeholder that makes the scaffold runnable without external APIs."""

    def classify_application_type(self, user_text: str) -> ApplicationTypeModel:
        text = (user_text or "").lower()
        if any(k in text for k in ["building", "permit", "plan check", "apn", "bsn"]):
            app = "building_permit"
            conf = 0.6
            rationale = "Detected building/permit keywords."
        else:
            app = "tsg_general"
            conf = 0.55
            rationale = "No strong signal; defaulting to general TSG workflow."
        return ApplicationTypeModel(application_type=app, confidence=conf, rationale=rationale)

    def generate_checklist_report(
        self,
        *,
        case_id: str,
        application_type: str,
        rules: List[Dict[str, Any]],
        submission_text: str,
    ) -> ChecklistReportModel:
        text = (submission_text or "").lower()

        checklist: List[ChecklistItemModel] = []
        blocking_issues: List[str] = []
        followups: List[str] = []

        for rule in rules:
            rule_id = rule.get("rule_id", "")
            title = rule.get("title", "")
            desc = rule.get("description", "")
            severity = rule.get("severity", "INFO")
            keywords = rule.get("keywords", []) or []
            keywords_l = [str(k).lower() for k in keywords]

            hits = [k for k in keywords_l if k and k in text]
            if hits:
                status = "PASS"
                confidence = 0.7
                evidence = [{"source": "submission", "excerpt": f"Found keyword(s): {', '.join(hits[:3])}"}]
                missing: List[str] = []
                rationale = "Basic keyword match (mock). Replace with real evidence extraction."
            else:
                status = "UNKNOWN" if severity in ("BLOCKER", "WARN") else "NA"
                confidence = 0.3 if status == "UNKNOWN" else 0.8
                evidence = []
                missing = ["Provide evidence/statement addressing this requirement."] if status == "UNKNOWN" else []
                rationale = "No evidence detected in submission text (mock)."

            item = ChecklistItemModel(
                rule_id=rule_id,
                title=title,
                description=desc,
                status=status,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                confidence=confidence,
                evidence=evidence,
                missing=missing,
                rationale=rationale,
            )
            checklist.append(item)

            if severity == "BLOCKER" and status == "FAIL":
                blocking_issues.append(f"{rule_id}: {title}")
            if status == "UNKNOWN":
                followups.append(f"Please provide info/evidence for: {title}")

        # overall recommendation
        if any(i.severity == "BLOCKER" and i.status == "FAIL" for i in checklist):
            overall = "REJECT"
        elif any(i.status == "UNKNOWN" for i in checklist):
            overall = "NEED_INFO"
        else:
            overall = "APPROVE"

        summary = (
            f"Mock evaluation for {application_type}. Replace MockLLM with real LLM + retrieval for production."
        )

        return ChecklistReportModel(
            schema_version="1.0",
            case_id=case_id,
            application_type=application_type,
            overall_recommendation=overall,  # type: ignore[arg-type]
            summary=summary,
            checklist=checklist,
            blocking_issues=blocking_issues,
            followup_questions=followups,
            generated_at=now_iso(),
        )

    def generate_flowchart(self, *, process_description: str) -> FlowchartModel:
        # A minimal heuristic to turn lines into nodes
        steps = [s.strip() for s in re.split(r"\n|\r|\.|;", process_description or "") if s.strip()]
        if not steps:
            mermaid = "flowchart TD\n  A[Describe the process] --> B[Add steps]\n"
            questions = ["Please describe the process in 3-8 steps (one per line)."]
        else:
            mermaid_lines = ["flowchart TD"]
            for idx, s in enumerate(steps[:10]):
                node = chr(ord('A') + idx)
                mermaid_lines.append(f"  {node}[{s}]")
                if idx > 0:
                    prev = chr(ord('A') + idx - 1)
                    mermaid_lines.append(f"  {prev} --> {node}")
            mermaid = "\n".join(mermaid_lines) + "\n"
            questions = []

        return FlowchartModel(
            mermaid=mermaid,
            title="Process Flow (draft)",
            assumptions=["Auto-generated from text; confirm accuracy."],
            questions=questions,
        )


class OpenAIChatLLMClient:
    """Example LLM backend using LangChain's ChatOpenAI.

    Install extras:
        pip install -e ".[openai]"

    Environment:
        OPENAI_API_KEY=...
        TSG_OPENAI_MODEL=...
    """

    def __init__(self, *, model: str):
        try:
            from langchain_openai import ChatOpenAI  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError(
                "langchain-openai is not installed. Run: pip install -e '.[openai]' "
            ) from e

        self._ChatOpenAI = ChatOpenAI
        self.model_name = model

    def _model(self, temperature: float = 0.0):
        return self._ChatOpenAI(model=self.model_name, temperature=temperature, streaming=False)

    def classify_application_type(self, user_text: str) -> ApplicationTypeModel:
        model = self._model(temperature=0.0).with_structured_output(ApplicationTypeModel)  # type: ignore[attr-defined]
        messages = [
            {
                "role": "system",
                "content": "You classify TSG application type. Return only the structured object.",
            },
            {"role": "user", "content": user_text},
        ]
        return model.invoke(messages)  # type: ignore[return-value]

    def generate_checklist_report(
        self,
        *,
        case_id: str,
        application_type: str,
        rules: List[Dict[str, Any]],
        submission_text: str,
    ) -> ChecklistReportModel:
        model = self._model(temperature=0.0).with_structured_output(ChecklistReportModel)  # type: ignore[attr-defined]
        prompt = (
            "Evaluate the submission against the rules and produce a checklist report.\n\n"
            f"case_id: {case_id}\n"
            f"application_type: {application_type}\n\n"
            "Rules (JSON):\n"
            f"{json.dumps(rules, ensure_ascii=False, indent=2)}\n\n"
            "Submission text:\n"
            f"{submission_text}\n\n"
            "Rules for your output:\n"
            "- If evidence is missing, use status UNKNOWN and fill missing[].\n"
            "- PASS/FAIL should include evidence excerpts.\n"
        )
        messages = [
            {
                "role": "system",
                "content": "You are a compliance officer. Return ONLY structured output.",
            },
            {"role": "user", "content": prompt},
        ]
        return model.invoke(messages)  # type: ignore[return-value]

    def generate_flowchart(self, *, process_description: str) -> FlowchartModel:
        model = self._model(temperature=0.2).with_structured_output(FlowchartModel)  # type: ignore[attr-defined]
        prompt = (
            "Generate Mermaid flowchart TD code from the described process.\n"
            "Return structured flowchart object with mermaid, assumptions, questions."\
        )
        messages = [
            {"role": "system", "content": "Return ONLY structured output."},
            {"role": "user", "content": prompt + "\n\n" + process_description},
        ]
        return model.invoke(messages)  # type: ignore[return-value]


# -----------------------------------------------------------------------------
# OpenAIResponsesLLMClient
#
# This alternate implementation uses the OpenAI "responses" API directly. It
# requests reasoning summaries for each call and stores the most recent
# summary on the instance.  The resulting structured JSON is parsed using
# Python's json module and returned as a Pydantic model.  If the OpenAI
# dependency is unavailable or network access is not permitted, this class
# will raise at import/initialization time.

class OpenAIResponsesLLMClient:
    """LLM backend using the new OpenAI responses API with reasoning support.

    The responses API can return both a structured answer and a reasoning
    summary in a single call.  Each method below constructs a prompt that
    instructs the model to output only JSON.  The last reasoning summary is
    captured in `last_reasoning_summary` for inspection by the caller.

    Environment:
        OPENAI_API_KEY=... (required)
        TSG_OPENAI_MODEL=... (optional)
    """

    def __init__(self, *, model: str):
        try:
            # Lazy import to allow missing dependency when provider is not openai
            from openai import OpenAI  # type: ignore
        except Exception as e:  # pragma: no cover
            raise ImportError(
                "openai python package is not installed. Install it via `pip install openai`."
            ) from e

        # Instantiate the client once; the API key is read from the environment
        self._client = OpenAI()
        self.model_name = model
        # store the most recent reasoning summary for introspection
        self.last_reasoning_summary: Optional[str] = None

    def _extract_reasoning_summary(self, output_items: Any) -> str | None:
        """
        Extract reasoning summaries from the OpenAI responses API output items.

        The responses API returns a list of output items (either dicts or
        structured objects).  When reasoning summaries are requested via the
        `reasoning` parameter, there will be an item of type "reasoning" with
        a `summary` list.  We concatenate all summary_text entries into a
        single string.
        """
        if not output_items:
            return None

        def get(obj: Any, key: str, default: Any = None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        summaries: List[str] = []
        for item in output_items:
            if get(item, "type") != "reasoning":
                continue
            summary_list = get(item, "summary", []) or []
            for part in summary_list:
                part_type = get(part, "type")
                if part_type in ("summary_text", "reasoning_summary_text"):
                    text = get(part, "text", "")
                    if text:
                        summaries.append(text)
        if summaries:
            return "\n\n".join(summaries).strip()
        return None

    def classify_application_type(self, user_text: str) -> ApplicationTypeModel:
        # Compose a simple JSON schema for the expected output.  The model
        # should reply with a JSON object containing application_type,
        # confidence (0.0-1.0), and rationale.
        instructions = (
            "You are a classifier for TSG submissions. "
            "Categorize the user_text into one of a small set of application types. "
            "Return a JSON object with fields: application_type (string), confidence (float between 0 and 1), "
            "and rationale (short explanation). Do not return any prose outside the JSON object."
        )
        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_text or ""},
        ]
        # Use the chat completions API instead of the deprecated responses API.
        # The messages parameter must be passed as `messages`, and the API returns
        # a list of choices.  Each choice contains a message with a `content` field.
        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        # No reasoning support via this API; clear the last reasoning summary.
        self.last_reasoning_summary = None
        # Parse JSON from the assistant's message content.
        try:
            output_text = resp.choices[0].message.content  # type: ignore[attr-defined]
            data = json.loads(output_text)
        except Exception as e:
            raise ValueError(f"Failed to parse classification JSON: {output_text}") from e
        return ApplicationTypeModel(**data)

    def generate_checklist_report(
        self,
        *,
        case_id: str,
        application_type: str,
        rules: List[Dict[str, Any]],
        submission_text: str,
    ) -> ChecklistReportModel:
        # Build prompt.  Provide the rules and submission text and ask for a
        # checklist report JSON matching the ChecklistReportModel schema.
        prompt = (
            "You are an AI compliance officer. Evaluate the submission_text against the provided rules and "
            "produce a checklist report as a JSON object. The JSON should conform to the following fields: "
            "schema_version (string), case_id (string), application_type (string), overall_recommendation "
            "(one of APPROVE, CONDITIONAL_APPROVE, REJECT, NEED_INFO), summary (string), checklist (array of "
            "items each with rule_id, title, description, status, severity, confidence, evidence (list), "
            "missing (list), rationale), blocking_issues (array), followup_questions (array), generated_at (ISO timestamp). "
            "Rules are provided as a JSON list. The submission_text may be long. Only return the JSON object."
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "case_id": case_id,
                "application_type": application_type,
                "rules": rules,
                "submission_text": submission_text,
            }, ensure_ascii=False)},
        ]
        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        # Clear reasoning summary; the chat completions API does not return it.
        self.last_reasoning_summary = None
        try:
            output_text = resp.choices[0].message.content  # type: ignore[attr-defined]
            data = json.loads(output_text)
        except Exception as e:
            raise ValueError(f"Failed to parse checklist report JSON: {output_text}") from e

        # Post‑process the raw JSON to coerce values into the expected schema.  The LLM
        # sometimes returns severities like "HIGH" or statuses like "NEED_INFO".  We map
        # these into the allowed literals for ChecklistReportModel.  We also ensure
        # evidence items are dictionaries with at least a 'source' and 'excerpt'.
        checklist = data.get("checklist", []) or []
        for item in checklist:
            # Normalize severity
            sev = str(item.get("severity", "")).upper()
            if sev not in ("BLOCKER", "WARN", "INFO"):
                # Map common severity synonyms to allowed values
                mapping = {"HIGH": "BLOCKER", "MEDIUM": "WARN", "LOW": "INFO"}
                item["severity"] = mapping.get(sev, "INFO")
            # Normalize status
            status = str(item.get("status", "")).upper()
            if status not in ("PASS", "FAIL", "NA", "UNKNOWN"):
                # If model says NEED_INFO, treat as UNKNOWN (i.e. missing info)
                if status in ("NEED_INFO", "PENDING", "IN_PROGRESS"):
                    item["status"] = "UNKNOWN"
                else:
                    item["status"] = "UNKNOWN"
            # Normalize evidence: convert strings into dicts
            evidence_list = item.get("evidence", []) or []
            new_evidence = []
            for ev in evidence_list:
                if isinstance(ev, dict):
                    new_evidence.append(ev)
                elif isinstance(ev, str):
                    # Use 'submission' as default source
                    new_evidence.append({"source": "submission", "excerpt": ev})
            item["evidence"] = new_evidence

            # Normalize confidence: ensure it is a float.  The LLM may output
            # confidence as a string like "HIGH", "MEDIUM", "LOW" or a numeric
            # string.  Convert common terms to a numeric approximation and
            # coerce numeric strings to floats.  Default to 0.5 if unparseable.
            conf = item.get("confidence")
            if isinstance(conf, str):
                conf_upper = conf.strip().upper()
                # Map textual confidences to approximate numeric values
                conf_map = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}
                if conf_upper in conf_map:
                    item["confidence"] = conf_map[conf_upper]
                else:
                    # Try to parse as float
                    try:
                        item["confidence"] = float(conf)
                    except Exception:
                        item["confidence"] = 0.5
            # Ensure confidence is a float even if provided as int or other type
            elif not isinstance(conf, (int, float)):
                item["confidence"] = 0.5
        data["checklist"] = checklist

        # Normalize blocking_issues: ensure each item is a string.  The LLM
        # may return dictionaries for blocking issues with fields like
        # {"rule_id": ..., "issue": ...}.  Convert these into a readable string.
        blocking_issues = data.get("blocking_issues", []) or []
        new_blocking: List[str] = []
        for bi in blocking_issues:
            if isinstance(bi, dict):
                # Build a string representation from common keys
                rule_id = bi.get("rule_id") or bi.get("id")
                issue_msg = bi.get("issue") or bi.get("message") or bi.get("title")
                if rule_id and issue_msg:
                    new_blocking.append(f"{rule_id}: {issue_msg}")
                else:
                    # Fallback: dump as JSON string
                    try:
                        new_blocking.append(json.dumps(bi, ensure_ascii=False))
                    except Exception:
                        new_blocking.append(str(bi))
            else:
                new_blocking.append(str(bi))
        data["blocking_issues"] = new_blocking

        # Normalize followup_questions: ensure each item is a string.
        # The LLM may return objects like {"rule_id": ..., "question": ..., "justification": ...}.
        followups_raw = data.get("followup_questions", [])
        new_followups: List[str] = []
        if isinstance(followups_raw, list):
            for fq in followups_raw:
                if isinstance(fq, dict):
                    rule_id = fq.get("rule_id") or fq.get("id")
                    question = fq.get("question") or fq.get("q") or fq.get("text")
                    justification = fq.get("justification") or fq.get("rationale") or fq.get("reason")

                    rule_id_s = str(rule_id).strip() if rule_id is not None else ""
                    q_s = str(question).strip() if question is not None else ""

                    if rule_id_s and q_s:
                        s = f"{rule_id_s}: {q_s}"
                    elif q_s:
                        s = q_s
                    else:
                        try:
                            s = json.dumps(fq, ensure_ascii=False)
                        except Exception:
                            s = str(fq)

                    j_s = str(justification).strip() if justification is not None else ""
                    if j_s:
                        s = f"{s} — {j_s}"

                    new_followups.append(s)
                else:
                    new_followups.append(str(fq))
        elif followups_raw is None:
            new_followups = []
        else:
            new_followups = [str(followups_raw)]

        # Drop empties + de-duplicate while preserving order.
        seen: set[str] = set()
        followups_out: List[str] = []
        for s in new_followups:
            s2 = (s or "").strip()
            if not s2 or s2 in seen:
                continue
            seen.add(s2)
            followups_out.append(s2)
        data["followup_questions"] = followups_out

        return ChecklistReportModel(**data)

    def generate_flowchart(self, *, process_description: str) -> FlowchartModel:
        prompt = (
            "You generate Mermaid flowchart code in the TD layout from a process description. "
            "Return a JSON object with fields: mermaid (the code starting with 'flowchart TD'), "
            "title (string), assumptions (array of strings), questions (array of strings)."
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": process_description or ""},
        ]
        resp = self._client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        self.last_reasoning_summary = None
        try:
            output_text = resp.choices[0].message.content  # type: ignore[attr-defined]
            data = json.loads(output_text)
        except Exception as e:
            raise ValueError(f"Failed to parse flowchart JSON: {output_text}") from e
        return FlowchartModel(**data)
