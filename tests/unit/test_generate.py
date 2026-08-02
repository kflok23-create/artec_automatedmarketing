"""Acceptance 2, 2d, 2g — LoRA triggers, one-LoRA rule, NSFW retry-then-park."""

import pytest

from app.config import OPERATOR_CONSTANTS
from app.toolbox.generate import (
    NSFWFlaggedError,
    build_generate_request,
    generate_image,
    trigger_present,
)

LORAS = OPERATOR_CONSTANTS["loras"]
ENDPOINT = OPERATOR_CONSTANTS["image_endpoints"]["lora"]


def test_trigger_word_boundary_not_substring():
    # 2g: an assembled prompt must NOT satisfy the unassembled trigger check.
    assembled_prompt = "artec blocks assembled into a crane, studio light"
    assert trigger_present(assembled_prompt, "artec blocks assembled")
    assert not trigger_present(assembled_prompt, "artec block")
    assert trigger_present("a single artec block on a desk", "artec block")


def test_trigger_injected_when_absent():
    _, args = build_generate_request("a crane on a desk", "assembled_blocks", LORAS, "square", ENDPOINT)
    assert trigger_present(args["prompt"], "artec blocks assembled")


def test_exactly_one_lora_with_correct_weights_per_subject():
    _, args_a = build_generate_request("x", "assembled_blocks", LORAS, "square", ENDPOINT)
    assert len(args_a["loras"]) == 1
    assert args_a["loras"][0]["path"] == LORAS["assembled"]["path"]

    _, args_u = build_generate_request("x", "loose_blocks", LORAS, "vertical", ENDPOINT)
    assert len(args_u["loras"]) == 1
    assert args_u["loras"][0]["path"] == LORAS["unassembled"]["path"]
    assert args_u["image_size"] == "portrait_16_9"


def test_generate_request_locked_schema():
    endpoint, args = build_generate_request("x", "assembled_blocks", LORAS, "landscape", ENDPOINT)
    assert endpoint == "fal-ai/qwen-image-2512/lora"
    assert args["num_inference_steps"] == 28
    assert args["guidance_scale"] == 4
    assert args["enable_safety_checker"] is True
    assert args["image_size"] == "landscape_16_9"


class _NSFWFal:
    def __init__(self, flag_sequence):
        self.flags = list(flag_sequence)
        self.calls = 0

    def run(self, endpoint, arguments, timeout_s=600):
        flagged = self.flags[min(self.calls, len(self.flags) - 1)]
        self.calls += 1
        return {"images": [{"url": "u"}], "has_nsfw_concepts": [flagged]}

    def fetch(self, url, suffix):
        return "local.png"


def test_nsfw_flagged_retries_once_then_parks():
    # 2d: flagged → retry once → still flagged → NSFWFlaggedError (post parks, never publishes)
    fal = _NSFWFal([True, True])
    with pytest.raises(NSFWFlaggedError):
        generate_image(fal, "x", "assembled_blocks", LORAS, "square", ENDPOINT)
    assert fal.calls == 2


def test_nsfw_flag_clears_on_retry():
    fal = _NSFWFal([True, False])
    assert generate_image(fal, "x", "assembled_blocks", LORAS, "square", ENDPOINT) == "local.png"
    assert fal.calls == 2
