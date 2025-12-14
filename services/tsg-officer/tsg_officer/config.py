from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv


def _load_env() -> None:
    # Load .env if present (non-fatal if missing)
    load_dotenv(override=False)


LLMProvider = Literal["mock", "openai"]


@dataclass(frozen=True)
class Settings:
    llm_provider: LLMProvider = "mock"
    openai_model: str = "gpt-4o-mini"
    checkpoint_db: str = "./.tsg_checkpoints.sqlite"

    @staticmethod
    def from_env() -> "Settings":
        _load_env()
        llm_provider = os.getenv("TSG_LLM_PROVIDER", "mock").strip().lower()
        if llm_provider not in ("mock", "openai"):
            llm_provider = "mock"

        openai_model = os.getenv("TSG_OPENAI_MODEL", "gpt-4o-mini").strip()

        checkpoint_db = os.getenv("TSG_CHECKPOINT_DB", "./.tsg_checkpoints.sqlite").strip()
        if not checkpoint_db:
            checkpoint_db = "./.tsg_checkpoints.sqlite"

        # Ensure parent dir exists
        Path(checkpoint_db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

        return Settings(
            llm_provider=llm_provider,  # type: ignore[arg-type]
            openai_model=openai_model,
            checkpoint_db=checkpoint_db,
        )
