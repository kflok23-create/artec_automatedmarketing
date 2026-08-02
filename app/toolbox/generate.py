"""GENERATE — DORMANT since v3 (Rule 3: do not generate what we can photograph).

The bank holds 479 real assets including 109 assembled-product photos; bank-first became
BANK-ONLY for anything depicting the product, so the selector no longer routes here and
`config.generate_enabled` is false. The module stays on disk, tested and reversible — the
operator can overturn the decision with one config flip, not a code dig. If re-enabled,
every guard below still applies, plus v3 Rule 5 (exactly one LoRA per call, asserted at
the call site) and the Rule 0 text guard inside Fal.run.

Original contract: fal-ai/qwen-image-2512/lora with the two artec-blocks LoRAs. Stills only.

Trigger rules (the collision is real: "artec block" is a substring of
"artec blocks assembled"):
- trigger presence is verified by exact phrase with word boundaries, never `in`
- exactly ONE entry in `loras` per request — one LoRA, one trigger, one request
- the trigger is injected into every prompt for that LoRA

`has_nsfw_concepts` is checked on every response: flagged renders are discarded, retried
once with a revised prompt, then parked. A flagged image is never published.
"""

from __future__ import annotations

import re
from typing import Any


class GenerateError(RuntimeError):
    pass


class NSFWFlaggedError(GenerateError):
    """Both attempts came back flagged — the post parks, nothing publishes."""


ASPECT_TO_IMAGE_SIZE = {
    "vertical": "portrait_16_9",
    "square": "square_hd",
    "landscape": "landscape_16_9",
}

SUBJECT_TO_LORA = {
    "assembled_blocks": "assembled",
    "loose_blocks": "unassembled",
}


def trigger_present(prompt: str, trigger: str) -> bool:
    """Exact phrase with word boundaries. `'artec block' in prompt` would return True for
    every assembled prompt — that naive check is the silent-routing bug this prevents."""
    return re.search(rf"(?<!\w){re.escape(trigger)}(?!\w)", prompt) is not None


def build_generate_request(
    prompt: str, subject: str, loras_cfg: dict[str, Any], aspect: str, endpoint: str
) -> tuple[str, dict]:
    lora_key = SUBJECT_TO_LORA.get(subject)
    if lora_key is None:
        raise GenerateError(
            f"GENERATE only covers subjects {sorted(SUBJECT_TO_LORA)}; got {subject!r}"
        )
    lora = loras_cfg[lora_key]
    trigger = lora["trigger"]
    if not trigger_present(prompt, trigger):
        prompt = f"{trigger}, {prompt}"
    image_size = ASPECT_TO_IMAGE_SIZE.get(aspect)
    if image_size is None:
        raise GenerateError(f"unknown aspect {aspect!r}; expected one of {sorted(ASPECT_TO_IMAGE_SIZE)}")
    arguments = {
        "prompt": prompt,
        "num_inference_steps": 28,
        "guidance_scale": 4,
        "image_size": image_size,
        "num_images": 1,
        "output_format": "png",
        "acceleration": "regular",
        "enable_safety_checker": True,
        # Exactly ONE entry: merging both LoRAs puts both triggers in scope and the model
        # cannot tell which concept is invoked.
        "loras": [{"path": lora["path"], "scale": lora["scale"]}],
    }
    # v3 Rule 5 — asserted AT THE CALL SITE, in addition to the trigger-collision guard.
    if len(arguments["loras"]) != 1:
        raise GenerateError(f"exactly one LoRA per call; got {len(arguments['loras'])}")
    return endpoint, arguments


def _nsfw_flagged(result: dict) -> bool:
    flags = result.get("has_nsfw_concepts") or []
    return any(bool(f) for f in flags)


def generate_image(
    fal, prompt: str, subject: str, loras_cfg: dict[str, Any], aspect: str, endpoint: str
) -> str:
    """Run GENERATE, honouring the NSFW retry-once-then-park rule. Returns a local path."""
    endpoint, args = build_generate_request(prompt, subject, loras_cfg, aspect, endpoint)
    result = fal.run(endpoint, args)
    if _nsfw_flagged(result):
        revised = dict(args)
        revised["prompt"] = (
            "safe family-friendly product photography, educational toy, " + args["prompt"]
        )
        result = fal.run(endpoint, revised)
        if _nsfw_flagged(result):
            raise NSFWFlaggedError("render flagged by safety checker twice — parking the post")
    images = result.get("images") or []
    if not images:
        raise GenerateError("fal returned no images")
    return fal.fetch(images[0]["url"], ".png")


def probe_request(lora: dict[str, Any], endpoint: str) -> tuple[str, dict]:
    """doctor's live base-model-compatibility probe: 4 steps, 256×256, exact trigger.
    A LoRA trained on a different base may load without erroring and contribute nothing —
    fal errors mentioning shape/key/state-dict mismatches are surfaced RED by doctor."""
    return endpoint, {
        "prompt": lora["trigger"],
        "num_inference_steps": 4,
        "guidance_scale": 4,
        "image_size": {"width": 256, "height": 256},
        "num_images": 1,
        "output_format": "jpeg",
        "acceleration": "regular",
        "enable_safety_checker": True,
        "loras": [{"path": lora["path"], "scale": lora["scale"]}],
    }
