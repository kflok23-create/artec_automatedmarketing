"""Tool schemas the LLM reads — format per
https://hermes-agent.nousresearch.com/docs/developer-guide/plugins (Schema format)."""

SCHEMAS: dict[str, dict] = {
    "read_brief": {
        "name": "read_brief",
        "description": (
            "Read this week's brief: the v_brief view (last week's post genomes and "
            "outcomes, learnings verdicts, active config, parked count, asset inventory) "
            "plus REVENUE and ENGAGEMENT as two separate blocks. The lanes are never "
            "blended; unmeasured is labelled unmeasured, not zero. Read-only. Call this "
            "first, every Sunday."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "read_learnings": {
        "name": "read_learnings",
        "description": (
            "Deterministic lever scores and keep/kill/test verdicts for one week — SQL "
            "already did the arithmetic; your job is interpreting WHY a lever moved, "
            "never recomputing it. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "week_start": {"type": "string",
                               "description": "Monday of the scored week, YYYY-MM-DD"},
            },
            "required": ["week_start"],
        },
    },
    "read_asset_inventory": {
        "name": "read_asset_inventory",
        "description": (
            "Counts per subject/medium in the Drive asset bank, plus how many are unused. "
            "Plan only what the bank can service — ideas the bank cannot service will "
            "PARK. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "read_parked_posts": {
        "name": "read_parked_posts",
        "description": (
            "PARKED posts with their asset wishlists (written in the bank's folder "
            "vocabulary). Used in the monthly first-Sunday wishlist review. Read-only."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "write_plan": {
        "name": "write_plan",
        "description": (
            "Insert the 7-day plan, sized exactly by the channel cadence from the brief. "
            "In shadow mode your plan lands in plans_shadow and never goes live; in agent "
            "mode it creates DRAFT posts. Idempotent on (week_start, channel, slot) — "
            "safe to retry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "week_start": {"type": "string",
                               "description": "Monday of the planned week, YYYY-MM-DD"},
                "posts": {
                    "type": "array",
                    "description": "One object per planned post",
                    "items": {
                        "type": "object",
                        "properties": {
                            "channel": {"type": "string",
                                        "description": "instagram|tiktok|facebook|youtube|linkedin|email"},
                            "angle": {"type": "string"},
                            "hook": {"type": "string"},
                            "cta_type": {"type": "string",
                                         "description": "discount|learn_more|ugc_ask|story"},
                            "cta_placement": {"type": "string",
                                              "description": "caption_start|caption_end|comment"},
                            "keywords": {"type": "array", "items": {"type": "string"}},
                            "slot": {"type": "string",
                                     "description": "morning|lunch|evening|weekend — a real firing time AND a learned lever"},
                        },
                        "required": ["channel", "hook", "slot"],
                    },
                },
            },
            "required": ["week_start", "posts"],
        },
    },
    "record_gate_decision": {
        "name": "record_gate_decision",
        "description": (
            "Record the operator's weekly-gate verdict for one post: approve | edit | "
            "reject | inject. Rejected means REJECTED — never draft a replacement; fewer "
            "posts that week is the correct outcome. Edit deltas are stored verbatim (they "
            "train taste). Idempotent on post_id: the first decision stands."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "action": {"type": "string",
                           "description": "approve|edit|reject|inject"},
                "edits": {"type": "object",
                          "description": "field→new value; only angle/hook/cta_type/"
                                         "cta_placement/slot/caption/keywords apply"},
            },
            "required": ["post_id", "action"],
        },
    },
}
