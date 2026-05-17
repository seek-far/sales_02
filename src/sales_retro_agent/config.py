from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in packaged envs
    load_dotenv = None


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    max_chunk_chars: int = 28000
    max_tokens: int = 12000
    temperature: float = 0.0


@dataclass(frozen=True)
class VolcAsrSettings:
    api_key: str
    app_key: str = ""
    access_key: str = ""
    resource_id: str = "volc.seedasr.sauc.duration"
    ws_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    language: str = "zh-CN"
    uid: str = "sales_retro_agent"


def load_settings() -> Settings:
    if load_dotenv:
        load_dotenv()

    return Settings(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        max_chunk_chars=int(os.getenv("SALES_RETRO_MAX_CHUNK_CHARS", "28000")),
        max_tokens=int(os.getenv("SALES_RETRO_MAX_TOKENS", "12000")),
        temperature=float(os.getenv("SALES_RETRO_TEMPERATURE", "0")),
    )


def load_volc_asr_settings() -> VolcAsrSettings:
    if load_dotenv:
        load_dotenv()

    api_key = os.getenv("VOLC_ASR_API_KEY", "").strip()
    app_key = os.getenv("VOLC_ASR_APP_KEY", "").strip()
    access_key = os.getenv("VOLC_ASR_ACCESS_KEY", "").strip()
    if not api_key and app_key.lower().startswith("api-key-"):
        api_key = app_key
        app_key = ""

    return VolcAsrSettings(
        api_key=api_key,
        app_key=app_key,
        access_key=access_key,
        resource_id=os.getenv("VOLC_ASR_RESOURCE_ID", "volc.seedasr.sauc.duration").strip()
        or "volc.seedasr.sauc.duration",
        ws_url=os.getenv(
            "VOLC_ASR_WS_URL",
            "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        ).strip()
        or "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
        language=os.getenv("VOLC_ASR_LANGUAGE", "zh-CN").strip() or "zh-CN",
        uid=os.getenv("VOLC_ASR_UID", "sales_retro_agent").strip() or "sales_retro_agent",
    )
