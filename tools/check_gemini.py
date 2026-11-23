"""Probe Gemini models through the configured OpenAI-compatible gateway.

Run from the project root with the application's Python environment:
    python tools/check_gemini.py
    python tools/check_gemini.py --model all

The probe sends a tiny request and never prints credentials or generated text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.config import get_settings
from core.models import safe_error_reason


MODELS = {
    "flash": "GEMINI_3_8_FLASH",
    "pro": "GEMINI_3_1_PRO",
    "argon": "GEMINI_4_ARGON",
}


def probe(label: str, model: str, api_key: str, base_url: str, timeout: int) -> dict:
    """Send one small request and return a sanitized result."""
    from openai import OpenAI

    try:
        with OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0) as client:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "只回复：连通测试成功"}],
                max_tokens=16,
            )
        return {"profile": label, "model": model, "status": 200,
                "finish_reason": response.choices[0].finish_reason if response.choices else None,
                "response_received": bool(response.choices and response.choices[0].message.content)}
    except Exception as exc:
        response = getattr(exc, "response", None)
        error = safe_error_reason(exc)
        request_id = getattr(exc, "request_id", None)
        if not request_id and error:
            match = re.search(r"request id:\s*([A-Za-z0-9_-]+)", error, re.IGNORECASE)
            request_id = match.group(1) if match else None
        return {"profile": label, "model": model,
                "status": getattr(exc, "status_code", None),
                "request_id": request_id,
                "error": error or type(exc).__name__,
                "http_status": getattr(response, "status_code", None)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=(*MODELS, "all"), default="flash",
                        help="选择要测试的 Gemini 配置，默认 flash")
    args = parser.parse_args()

    settings = get_settings()
    key = getattr(settings, "GEMINI_API_KEY", "")
    base_url = getattr(settings, "GEMINI_PROXY_URL", "")
    if not key.strip() or not base_url.strip():
        print(json.dumps({"status": "not_configured", "message": "请检查 Gemini 密钥和代理地址配置"},
                         ensure_ascii=False))
        return 2

    selected = MODELS if args.model == "all" else {args.model: MODELS[args.model]}
    failed = False
    for label, setting_name in selected.items():
        model = getattr(settings, setting_name, "")
        if not model.strip():
            print(json.dumps({"profile": label, "status": "skipped", "reason": "not_configured"},
                             ensure_ascii=False))
            failed |= args.model != "all"
            continue
        result = probe(label, model, key, base_url, settings.REQUEST_TIMEOUT or 120)
        if result["status"] != 200:
            failed = True
        print(json.dumps({"host": urlsplit(base_url).hostname or "-", **result}, ensure_ascii=False),
              flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
