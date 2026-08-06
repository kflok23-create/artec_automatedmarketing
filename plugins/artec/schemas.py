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
            # week_start is OPTIONAL: absent means the planning week, computed by the
            # SAME function ideate uses. It was required, so an agent that did not know
            # which week to gate had to invent one.
            "required": [],
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
            "train taste). Idempotent on post_id: the first decision stands. For a brand-new "
            "idea the operator raises at the gate, use action='inject' with post_id omitted "
            "or null — a post is created and APPROVED."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": ["string", "null"],
                            "description": "omit or null when action='inject' to create a new post"},
                "action": {"type": "string",
                           "description": "approve|edit|reject|inject"},
                "edits": {"type": "object",
                          "description": "field→new value; only angle/hook/cta_type/"
                                         "cta_placement/slot/caption/keywords apply. For "
                                         "inject, supply channel/hook/slot here."},
            },
            "required": ["action"],
        },
    },

    # ---- v4 additions --------------------------------------------------------------
    "read_draft_posts": {
        "name": "read_draft_posts",
        "description": (
            "Every DRAFT post for a week with its full creative genome — the designed way "
            "to run the weekly gate. Use this rather than fishing drafts out of the brief. "
            "Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {"week_start": {"type": "string",
                                          "description": "Monday of the planned week, YYYY-MM-DD"}},
            "required": ["week_start"],
        },
    },
    "read_digest": {
        "name": "read_digest",
        "description": (
            "The prepared 21:00 digest for a date. It carries a `messages` list already "
            "split to fit Telegram's message limit on section boundaries — SEND THOSE "
            "VERBATIM, IN ORDER, and do not re-summarise, reorder, merge or shorten them; "
            "NEEDS YOU is first by construction. If `deliver` is false, say the "
            "skip_reason and stop (on Sunday there is no evening digest — the 09:00 gate "
            "is that day's touch). If NEEDS YOU is empty the digest is one line: say it "
            "and stop. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {"date": {"type": "string",
                                    "description": "YYYY-MM-DD; defaults to today"}},
            "required": [],
        },
    },
    "deliver_video": {
        "name": "deliver_video",
        "description": (
            "Send a pending video into this chat as a NATIVE Telegram video message so the "
            "operator can watch it inline, and record the delivery. MUST be called before "
            "review_video — a video the operator was never shown cannot be approved. Never "
            "paste a Drive link instead; Drive links are viewer pages, not playable media."
        ),
        "parameters": {
            "type": "object",
            "properties": {"post_id": {"type": "string"}},
            "required": ["post_id"],
        },
    },
    "review_video": {
        "name": "review_video",
        "description": (
            "Record the operator's decision on a delivered video: approve (goes out at the "
            "next occurrence of its slot), reject (parked with the reason), or rerender "
            "(returns to the render queue with the operator's reason as guidance). Refused "
            "unless the video was actually delivered to this chat first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "decision": {"type": "string", "description": "approve|reject|rerender"},
                "reason": {"type": "string",
                           "description": "required for reject and rerender — it is fed back "
                                          "to the toolbox as guidance"},
            },
            "required": ["post_id", "decision"],
        },
    },
    "review_email": {
        "name": "review_email",
        "description": (
            "Record the operator's decision on a rendered email. Email is the ONLY "
            "irreversible surface — once sent it reaches every subscriber and cannot be "
            "recalled — so it never auto-publishes. approve sends at the next occurrence of "
            "its slot, never immediately. edit overwrites any of the seven Brevo variables "
            "and re-presents next digest. reject parks it. send_test issues a test send to "
            "the operator's own address and changes nothing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "decision": {"type": "string",
                             "description": "approve|reject|edit|test_only"},
                "edits": {"type": "object",
                          "description": "subject, headline, body_copy, cta_text, "
                                         "story_block, hero_image_url, tracked_url"},
                "send_test": {"type": "boolean",
                              "description": "send a test to the operator before deciding"},
            },
            "required": ["post_id", "decision"],
        },
    },
    "record_metrics": {
        "name": "record_metrics",
        "description": (
            "Transcribe engagement figures the operator typed. YOU ARE A TRANSCRIBER, NOT A "
            "SOURCE: every digit you submit must appear in the operator's own message, which "
            "you must pass verbatim as operator_message. Never compute, estimate, infer, "
            "round, sum, or carry a figure forward from another day — if a number is missing "
            "or ambiguous, ask for it. Omitted fields stay unmeasured (NULL); never send zero "
            "to mean 'unknown'. Ask for ONE post at a time, naming the six fields in order, "
            "and accept a single ordered line back. Called without confirm, this WRITES "
            "NOTHING and returns an echo of exactly what would be recorded: read that back "
            "and call again with confirm=true only after the operator agrees."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "channel": {"type": "string"},
                "metric_date": {"type": "string", "description": "YYYY-MM-DD"},
                "figures": {
                    "type": "object",
                    "description": "any of impressions, completion_rate, watch_time_s, "
                                   "saves, shares, clicks — omit what the operator did not say",
                },
                "figures_line": {
                    "type": "string",
                    "description": "the operator's single ordered line, passed through "
                                   "UNCHANGED: impressions, completion_rate, watch_time_s, "
                                   "saves, shares, clicks — e.g. '4200, 0.62, 12, 45, 8, 118'. "
                                   "An empty position stays unmeasured; never fill one in.",
                },
                "operator_message": {"type": "string",
                                     "description": "the operator's message, VERBATIM"},
                "confirm": {"type": "boolean",
                            "description": "false/absent = echo only, nothing is written; "
                                           "true = write, and only after the operator has "
                                           "confirmed the echo"},
            },
            "required": ["post_id", "channel", "metric_date", "operator_message"],
        },
    },
    "retry_post": {
        "name": "retry_post",
        "description": (
            "Retry a FAILED or PARKED post — back to render if it has no media yet, back to "
            "publish if it does. Refuses if the post already holds an external id, because "
            "that means the platform accepted it and retrying would double-publish."
        ),
        "parameters": {
            "type": "object",
            "properties": {"post_id": {"type": "string"}},
            "required": ["post_id"],
        },
    },
    "fulfil_wishlist": {
        "name": "fulfil_wishlist",
        "description": (
            "Attach a newly uploaded bank asset to a PARKED post and return it to APPROVED "
            "so it renders on the next pass. The asset must already be synced and active."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "drive_file_id": {"type": "string"},
            },
            "required": ["post_id", "drive_file_id"],
        },
    },
    "acknowledge_price_table": {
        "name": "acknowledge_price_table",
        "description": (
            "Acknowledge a fal price reconciliation when the change is large enough to alter "
            "how much the render cap can buy. Small deltas apply automatically and are only "
            "reported. accept marks the table acknowledged; decline leaves it flagged."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "as_of": {"type": "string", "description": "YYYY-MM-DD of the pull"},
                "decision": {"type": "string", "description": "accept|decline"},
            },
            "required": ["as_of", "decision"],
        },
    },
}
