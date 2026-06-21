"""Guard the Volc ASR init request stays configured for our transcript pipeline.

- show_utterances + enable_speaker_info are required by the planned dedup fix
  (accumulate by definite utterances) and by speaker labelling — losing them
  silently regresses both. enable_itn/enable_punc shape the text the coach sees.
"""

from __future__ import annotations

from sales_retro_agent.asr_volc import build_init_payload
from sales_retro_agent.config import VolcAsrSettings


def _settings() -> VolcAsrSettings:
    return VolcAsrSettings(api_key="k", resource_id="volc.seedasr.sauc.duration")


def test_init_payload_requests_utterances_and_speaker_info():
    request = build_init_payload(_settings())["request"]
    assert request["show_utterances"] is True
    assert request["enable_speaker_info"] is True
    # ssd_version="200" is required for speaker clustering to actually engage on the
    # streaming endpoint; without it the engine returns a single speaker ("0").
    assert request["ssd_version"] == "200"
    assert request["enable_itn"] is True
    assert request["enable_punc"] is True
    assert request["model_name"] == "bigmodel"
