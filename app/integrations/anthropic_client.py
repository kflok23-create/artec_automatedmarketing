"""ONE MODEL — a single Anthropic model handles learn, ideate, copy, and toolbox routing.

Prompts live in prompts/*.md and use $variable placeholders (string.Template), so JSON
examples with braces survive substitution untouched.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from string import Template
from typing import Any

import anthropic

from app.settings import Settings

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(RuntimeError):
    pass


def render_prompt(prompt_file: str, variables: dict[str, Any]) -> str:
    path = PROMPTS_DIR / prompt_file
    if not path.exists():
        raise LLMError(f"prompt file missing: {path}")
    text = path.read_text(encoding="utf-8")
    safe = {k: (json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else str(v)) for k, v in variables.items()}
    return Template(text).safe_substitute(safe)


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in a model reply — fenced or bare."""
    m = _JSON_BLOCK.search(text)
    candidate = m.group(1).strip() if m else text.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Fall back to the first {...} or [...] span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError("model reply contained no parseable JSON")


class LLM:
    def __init__(self, settings: Settings):
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._model = settings.ANTHROPIC_MODEL

    def complete_text(self, prompt_file: str, variables: dict[str, Any], max_tokens: int = 2000) -> str:
        prompt = render_prompt(prompt_file, variables)
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in msg.content if block.type == "text")

    def complete_json(self, prompt_file: str, variables: dict[str, Any], max_tokens: int = 4000) -> Any:
        return extract_json(self.complete_text(prompt_file, variables, max_tokens=max_tokens))

    def ping(self) -> bool:
        msg = self._client.messages.create(
            model=self._model, max_tokens=8, messages=[{"role": "user", "content": "Reply with OK"}]
        )
        return bool(msg.content)
