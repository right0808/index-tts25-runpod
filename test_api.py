#!/usr/bin/env python3
"""Run the verified IndexTTS 2.5 API test with this project's defaults.

Usage:
    python test_api.py

Any default can still be overridden, for example:
    python test_api.py --text "新的测试文本" --output /tmp/result.wav
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts import test_load_balancer_api


PROJECT_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

DEFAULT_ENDPOINT_ID = "44s1tva2fqnvku"
DEFAULT_ENV_FILE = WORKSPACE_ROOT / ".env"
DEFAULT_SPEAKER_AUDIO = WORKSPACE_ROOT / "fxd.m4a"
DEFAULT_EMOTION_AUDIO = WORKSPACE_ROOT / "emotion1.m4a"
DEFAULT_OUTPUT_AUDIO = PROJECT_ROOT / "index-tts25-test-output.wav"
DEFAULT_TEXT = (
    "大概两到三天就可以送到哈，收到以后您可以品鉴一下哦如果觉得好喝也可以找我，"
    "您保留好我的微信有任何问题就随时找我哦"
)

DEFAULT_ARGUMENTS = {
    "--endpoint-id": DEFAULT_ENDPOINT_ID,
    "--env-file": str(DEFAULT_ENV_FILE),
    "--speaker": str(DEFAULT_SPEAKER_AUDIO),
    "--emotion": str(DEFAULT_EMOTION_AUDIO),
    "--output": str(DEFAULT_OUTPUT_AUDIO),
    "--text": DEFAULT_TEXT,
    "--language": "zh",
    "--emotion-alpha": "0.8",
    "--speed": "1.0",
    "--cold-start-timeout": "1800",
    "--request-timeout": "1800",
    "--health-request-timeout": "45",
    "--poll-interval": "15",
}


def _has_option(arguments: list[str], option: str) -> bool:
    return any(argument == option or argument.startswith(f"{option}=") for argument in arguments)


def arguments_with_defaults(arguments: list[str]) -> list[str]:
    result = list(arguments)
    for option, value in DEFAULT_ARGUMENTS.items():
        if not _has_option(result, option):
            result.extend((option, value))
    return result


def main() -> int:
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__)
        print("Default endpoint:", DEFAULT_ENDPOINT_ID)
        print("Default environment:", DEFAULT_ENV_FILE)
        print("Default speaker audio:", DEFAULT_SPEAKER_AUDIO)
        print("Default emotion audio:", DEFAULT_EMOTION_AUDIO)
        print("Default output:", DEFAULT_OUTPUT_AUDIO)
        print("\nAll options from scripts/test_load_balancer_api.py can be used to override these defaults.")
        return 0

    # Reuse the fully logged test implementation while keeping this root-level
    # entry point's defaults explicit and easy to edit.
    sys.argv = [sys.argv[0], *arguments_with_defaults(sys.argv[1:])]
    return test_load_balancer_api.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        test_load_balancer_api.log(
            "failed",
            "API test failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
