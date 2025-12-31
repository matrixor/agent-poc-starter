from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv


def _load_env() -> None:
    # Load .env if present (non-fatal if missing)
    load_dotenv(override=False)


LLMProvider = Literal["mock", "openai", "chubbgpt"]


@dataclass(frozen=True)
class Settings:
    llm_provider: LLMProvider = "mock"
    openai_model: str = "gpt-4o-mini"

    # ChubbGPT (API management gateway proxy) settings.
    # These are only required when TSG_LLM_PROVIDER=chubbgpt.
    #
    # Required:
    #   - TSG_CHUBBGPT_PROXY_URL
    #   - TSG_CHUBBGPT_APP_ID
    #   - TSG_CHUBBGPT_APP_KEY
    #   - TSG_CHUBBGPT_RESOURCE
    #
    # Optional:
    #   - TSG_CHUBBGPT_AUTH_URL (token endpoint)
    #   - TSG_CHUBBGPT_API_VERSION
    #   - TSG_CHUBBGPT_MODEL
    chubbgpt_proxy_url: str = ""
    chubbgpt_auth_url: str = "https://studiogateway.chubb.com/enterprise.operations.authorization?Identity=AAD"
    chubbgpt_api_version: str = "1"
    chubbgpt_app_id: str = ""
    chubbgpt_app_key: str = ""
    chubbgpt_resource: str = ""
    chubbgpt_model: str = ""
    checkpoint_db: str = "./.tsg_checkpoints.sqlite"
    # Optional override for the rules YAML file.
    # If unset, the app defaults to data/rules/rules.v1.yaml inside the package.
    rules_path: str = ""

    @staticmethod
    def from_env() -> "Settings":
        _load_env()
        llm_provider = os.getenv("TSG_LLM_PROVIDER", "mock").strip().lower()
        if llm_provider not in ("mock", "openai", "chubbgpt"):
            llm_provider = "mock"

        openai_model = os.getenv("TSG_OPENAI_MODEL", "gpt-4o-mini").strip()

        # ChubbGPT provider settings (only used when llm_provider=chubbgpt)
        chubbgpt_proxy_url = os.getenv("TSG_CHUBBGPT_PROXY_URL", "").strip()
        chubbgpt_auth_url = os.getenv(
            "TSG_CHUBBGPT_AUTH_URL",
            "https://studiogateway.chubb.com/enterprise.operations.authorization?Identity=AAD",
        ).strip()
        chubbgpt_api_version = os.getenv("TSG_CHUBBGPT_API_VERSION", "1").strip() or "1"
        chubbgpt_app_id = os.getenv("TSG_CHUBBGPT_APP_ID", "").strip()
        chubbgpt_app_key = os.getenv("TSG_CHUBBGPT_APP_KEY", "").strip()
        chubbgpt_resource = os.getenv("TSG_CHUBBGPT_RESOURCE", "").strip()
        # If a dedicated model name is not supplied for ChubbGPT, fall back to the OpenAI model.
        chubbgpt_model = os.getenv("TSG_CHUBBGPT_MODEL", "").strip() or openai_model

        checkpoint_db = os.getenv("TSG_CHECKPOINT_DB", "./.tsg_checkpoints.sqlite").strip()
        if not checkpoint_db:
            checkpoint_db = "./.tsg_checkpoints.sqlite"

        rules_path = os.getenv("TSG_RULES_PATH", "").strip()

        # Ensure parent dir exists
        Path(checkpoint_db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

        return Settings(
            llm_provider=llm_provider,  # type: ignore[arg-type]
            openai_model=openai_model,
            chubbgpt_proxy_url=chubbgpt_proxy_url,
            chubbgpt_auth_url=chubbgpt_auth_url,
            chubbgpt_api_version=chubbgpt_api_version,
            chubbgpt_app_id=chubbgpt_app_id,
            chubbgpt_app_key=chubbgpt_app_key,
            chubbgpt_resource=chubbgpt_resource,
            chubbgpt_model=chubbgpt_model,
            checkpoint_db=checkpoint_db,
            rules_path=rules_path,
        )
