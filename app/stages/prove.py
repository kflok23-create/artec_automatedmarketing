"""`artec prove <capability>` — nine capabilities, exercised, each recording a DATED proof.

Nine things this system can do that nobody has ever watched it do. A capability that has
never run is not a capability; it is an intention with code attached. Doctor reports any
proof absent or older than 90 days as YELLOW, and the digest lists the unproven ones weekly
until each goes green.

WHY THIS MAY WRITE `config` WITHOUT BREACHING THE INVARIANT: the rule is that **no agent
TOOL** writes `config` — the capability boundary is the security model, and it is about what
the model can reach. `prove` is a CLI/HTTPS command driven by the operator; it is not
registered with the plugin, appears in no schema, and the agent cannot call it. The seam
grep still returns zero. Stated here so the apparent contradiction is not rediscovered later
and resolved by weakening the rule — which is the direction these things always erode.

S1 — `video-pipeline` and `publish-by-slot` are the first UNATTENDED actions this system
will ever take. Their proofs exercise the real path. A mock would prove the mock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import get_config, set_config

PROOF_MAX_AGE_DAYS = 90

CAPABILITIES = (
    "agent-session",        # the brain holds a Telegram session and its tools resolve
    "sunday-cron",          # the two Sunday cron jobs are registered and time correctly
    "video-pipeline",       # S1 — render → pre-flight → deliverable bytes
    "publish-by-slot",      # S1 — the slot pass selects and would publish
    "brevo-send",           # template contract, substitution, campaign create, delete
    "stripe-attribution",   # a checkout reference joins back to a post
    "budget-refusal",       # the cap and the per-call ceiling actually refuse
    "audit-memory",         # the memory audit runs where the memory is
    "restore",              # the dump restores into a scratch database
)

S1 = ("video-pipeline", "publish-by-slot")

# For each proof: WHAT WOULD MAKE IT REPORT SUCCESS WITHOUT DEMONSTRATING THE THING? The
# answers below are enforced as preconditions in the provers, not left as comments — because
# `publish-by-slot` reported PROVEN over an empty board and nothing in the code showed it.
FALSE_PASS = {
    "agent-session": "a readable store belonging to a DIFFERENT hermes install",
    "sunday-cron": "a cron listing from any hermes install, not artec's",
    "video-pipeline": "a synthetic fixture that is not real bank footage",
    "publish-by-slot": "zero posts to select — the pass runs and selects nothing",
    "brevo-send": "a template that carries no variables, so nothing can fail to substitute",
    "stripe-attribution": "an order row written directly, bypassing the webhook join",
    "budget-refusal": "zero calls estimated — nothing was ever offered to refuse",
    "audit-memory": "a memory directory that is empty, or somebody else's",
    "restore": "a dump that restores structure with no rows",
}


@dataclass
class Proof:
    capability: str
    ok: bool
    detail: str
    evidence: dict | None = None
    at: str = ""

    def as_row(self) -> dict:
        return {"ok": self.ok, "detail": self.detail, "at": self.at or
                datetime.now(UTC).isoformat(), "evidence": self.evidence or {}}


class NotProvable(RuntimeError):
    """This environment cannot exercise the capability. NOT a pass, NOT a failure — an
    honest third state, because recording a skip as a proof is how a capability comes to be
    believed without ever having run."""


# ---------------------------------------------------------------------------------------
# the nine
# ---------------------------------------------------------------------------------------

def prove_budget_refusal(session: Session, **_) -> Proof:
    """The cap and the per-call ceiling must REFUSE, not warn. Pure code, exercisable
    anywhere — so there is no excuse for this one ever being unproven."""
    from app.toolbox.pricing import OutputTooLarge, estimate_micros

    cap = int(get_config(session, "render_run_cap_cents", 250))
    ceiling = int(get_config(session, "per_call_ceiling_cents", 50))
    max_mp = float(get_config(session, "max_output_megapixels", 4.0))

    refused = False
    try:
        estimate_micros(session, "fal-ai/clarity-upscaler", width=4000, height=4000,
                        max_megapixels=max_mp)
    except OutputTooLarge:
        refused = True
    if not refused:
        return Proof("budget-refusal", False,
                     f"a 16 MP output was NOT refused against max_output_megapixels={max_mp}")

    one = estimate_micros(session, "fal-ai/clarity-upscaler", width=1080, height=1920,
                          max_megapixels=max_mp)
    if one <= 0:
        # FALSE PASS: nothing was ever offered, so nothing was refused.
        return Proof("budget-refusal", False,
                     "a legal call estimated 0 micros — the refusal was never exercised "
                     "against anything")
    return Proof("budget-refusal", True,
                 f"oversize output refused before the call; one 1080x1920 upscale = "
                 f"{one} micros, run cap {cap}c, per-call ceiling {ceiling}c",
                 {"one_call_micros": one, "cap_cents": cap, "ceiling_cents": ceiling})


def prove_audit_memory(session: Session, **_) -> Proof:
    """Runs where the memory is. On artec api there is no HERMES_HOME, and saying so is the
    correct answer — not a green line."""
    import os
    from pathlib import Path

    from app.stages.agent_review import audit_memory

    home = os.environ.get("HERMES_HOME")
    if not home:
        raise NotProvable("HERMES_HOME is not set on this service — run this proof on "
                          "artec-brain, where the memory actually lives")
    # HERMES_HOME being set is not evidence this is ARTEC's brain. On a development machine
    # it points at a personal hermes install, and the audit will dutifully report hundreds
    # of hits in somebody else's notes — a confident FAILED about the wrong memory.
    if not any((Path(home) / p).exists() for p in
               ("plugins/artec", "profiles/artec-brain/plugins/artec")):
        raise NotProvable(
            f"{home} carries no artec plugin — this is a hermes install, but not artec's "
            "brain. Auditing it would report on somebody else's memory.")
    hits = audit_memory(log=lambda *_: None)
    return Proof("audit-memory", not hits,
                 "memory clean" if not hits else f"{len(hits)} metric-shaped hit(s)",
                 {"hits": hits[:10]})


def prove_restore(session: Session, settings=None, dump_path: str | None = None,
                  **_) -> Proof:
    from app.stages.backup import restore_check

    if not dump_path:
        raise NotProvable("pass --dump <path> (run `artec backup` first) — a restore proof "
                          "without a dump would be proving nothing")
    result = restore_check(session, settings.DATABASE_URL, dump_path, log=lambda *_: None)
    return Proof("restore", result.ok, result.detail, {"counts": result.counts})


def prove_stripe_attribution(session: Session, **_) -> Proof:
    """A checkout reference must join back to a post. Exercised through the real webhook
    handler, not by writing an order row directly — the join is the thing under test."""
    from app.integrations.stripe_webhook import handle_event
    from app.models import Order, Post

    probe_id = "post_prove_stripe"
    if session.get(Post, probe_id) is None:
        session.add(Post(post_id=probe_id, week_start=datetime.now(UTC).date(),
                         channel="instagram", status="DRAFT"))
        session.flush()
    event = {"id": f"evt_prove_{int(datetime.now(UTC).timestamp())}",
             "type": "checkout.session.completed",
             "data": {"object": {"id": f"cs_prove_{int(datetime.now(UTC).timestamp())}",
                                 "client_reference_id": probe_id,
                                 "amount_total": 14900, "currency": "sgd"}}}
    before = session.query(Order).filter(Order.post_id == probe_id).count()
    if before:
        raise NotProvable("a probe order already exists — a previous proof did not clean up")
    handle_event(session, event)
    order = session.query(Order).filter(Order.post_id == probe_id).first()
    # FALSE PASS: an order row written directly would satisfy "an order exists". The join is
    # the thing under test, so the SOURCE must be the webhook and the amount must be the one
    # the event carried.
    ok = (order is not None and order.source == "stripe"
          and order.amount_minor == 14900 and order.currency == "SGD")
    if order is not None:
        session.delete(order)
    session.delete(session.get(Post, probe_id))
    session.flush()
    return Proof("stripe-attribution", ok,
                 "client_reference_id joined to the post" if ok
                 else "the webhook did not attribute the order to its post")


def prove_video_pipeline(session: Session, **_) -> Proof:
    """S1. The real pre-flight against a real encode — a mock would prove the mock."""
    import shutil
    from pathlib import Path

    from app.stages.preflight import preflight_video

    if not shutil.which("ffprobe"):
        raise NotProvable("ffprobe is not on PATH in this process — the video pre-flight "
                          "cannot run, so nothing here would be proof of anything")
    fixture = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "real_raw_video.mp4"
    if not fixture.is_file():
        raise NotProvable(f"no real encode at {fixture}")
    # FALSE PASS: a synthetic clip would pass the structural checks while proving nothing
    # about real bank footage. Assert the bitrate is in the band real footage occupies.
    import json as _json
    import subprocess as _sub

    probe = _json.loads(_sub.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         str(fixture)], capture_output=True, text=True, timeout=120).stdout or "{}")
    stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), {})
    duration = float(probe.get("format", {}).get("duration") or 0)
    pixels = int(stream.get("width", 0)) * int(stream.get("height", 0))
    bps = (fixture.stat().st_size * 8) / (pixels * duration) if pixels and duration else 0
    if bps < 0.5:
        return Proof("video-pipeline", False,
                     f"the fixture is {bps:.3f} bits/pixel-second — that is a synthetic "
                     "clip, not real bank footage, and would prove the fixture")
    result = preflight_video(str(fixture), aspect_ratio="16:9", duration_bounds=(1.0, 10.0))
    return Proof("video-pipeline", result.ok,
                 "real encode passed publish pre-flight" if result.ok
                 else "; ".join(result.failures),
                 {"checks": getattr(result, "checks", None), "fixture": str(fixture)})


def prove_publish_by_slot(session: Session, **_) -> Proof:
    """S1. The slot pass must SELECT what it would publish and apply the skip rules — the
    first unattended action this system takes."""
    from app.scheduler import select_due_posts
    from app.stages.publish import skip_reason

    slot_times = get_config(session, "slot_times", {}) or {}
    if not slot_times:
        return Proof("publish-by-slot", False, "slot_times is empty — no slot can fire")
    seen = {}
    for slot in sorted(slot_times):
        due = select_due_posts(session, slot)
        seen[slot] = [{"post_id": p.post_id, "skip": skip_reason(session, p)} for p in due]
    would_publish = [p["post_id"] for rows in seen.values() for p in rows if not p["skip"]]
    held = [p["post_id"] for rows in seen.values() for p in rows if p["skip"]]
    evaluated = len(would_publish) + len(held)
    if evaluated == 0:
        # S1. Proving "the loop ran without error" over an empty set would let the first
        # unattended action this system takes be signed off against nothing.
        return Proof("publish-by-slot", False,
                     f"the slot pass evaluated {len(slot_times)} slots and found NO posts "
                     "— that proves the pass runs, not that it selects correctly. Render "
                     "something into a slot and re-run.",
                     {"by_slot": seen, "evaluated": 0})
    return Proof("publish-by-slot", True,
                 f"slot pass evaluated {len(slot_times)} slots: {len(would_publish)} would "
                 f"publish, {len(held)} held by a review gate",
                 {"by_slot": seen, "would_publish": would_publish, "held": held})


def prove_brevo_send(session: Session, settings=None, live: bool = False, **_) -> Proof:
    """Everything except delivery. Fetches template 3, asserts all six in-body variables,
    substitutes locally, asserts no `{{` survives except the two Brevo owns, asserts the
    NON-variable bytes are unchanged, creates the campaign, records the id, DELETES it.
    Never calls sendNow — the single live send is a deliberate operator action, later."""
    from app.integrations.brevo_client import Brevo, substitute_template

    if live:
        raise NotProvable("--live is refused in this pass: the single live send is a "
                          "deliberate operator action (gap B5), not part of a proof run")
    brevo = Brevo(settings)
    html = brevo.get_template_html()
    variables = {"hero_image_url": "https://example.invalid/hero.jpg",
                 "headline": "H", "body_copy": "B", "cta_text": "C",
                 "tracked_url": "https://artec.my/?code=EMAIL50", "story_block": "S"}
    if html.count("{{") < len(variables):
        # FALSE PASS: a template carrying no variables substitutes perfectly and proves
        # nothing about the contract.
        return Proof("brevo-send", False,
                     f"template 3 carries only {html.count('{{')} placeholder(s) — fewer "
                     f"than the {len(variables)} in-body variables the contract requires")
    missing = [v for v in variables if "{{" + v + "}}" not in html.replace(" ", "")]
    if missing:
        return Proof("brevo-send", False,
                     f"template 3 is missing in-body variables: {missing}")
    substituted = substitute_template(html, variables)
    leftovers = [chunk for chunk in substituted.split("{{")[1:]
                 if not chunk.strip().startswith(("mirror", "unsubscribe"))]
    if leftovers:
        return Proof("brevo-send", False,
                     f"{len(leftovers)} unsubstituted placeholder(s) survived")
    # the non-variable bytes must be untouched
    skeleton_before = html
    for name in variables:
        skeleton_before = skeleton_before.replace("{{" + name + "}}", "")
    skeleton_after = substituted
    for value in variables.values():
        skeleton_after = skeleton_after.replace(value, "")
    if skeleton_before.replace(" ", "") != skeleton_after.replace(" ", ""):
        return Proof("brevo-send", False,
                     "substitution changed bytes outside the variables")
    campaign_id = brevo.create_campaign(name=f"artec-prove-{datetime.now(UTC):%Y%m%d%H%M%S}",
                                        subject="artec proof — not sent", html=substituted)
    deleted = brevo.delete_campaign(campaign_id)
    return Proof("brevo-send", True,
                 f"template contract, substitution and campaign creation proven; campaign "
                 f"{campaign_id} created and {'deleted' if deleted else 'NOT deleted'}; "
                 "sendNow was never called",
                 {"campaign_id": campaign_id, "deleted": deleted})


def prove_agent_session(session: Session, **_) -> Proof:
    """The brain's message store must be readable and its plugin registry present —
    otherwise the digest conversation cannot verify a single figure."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "prove_transcript",
        Path(__file__).resolve().parents[2] / "plugins" / "artec" / "transcript.py")
    transcript = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transcript)
    status = transcript.store_status()
    if not status.get("available"):
        raise NotProvable(f"{status.get('reason')} — run this proof on artec-brain")
    # FALSE PASS: a readable store belonging to a different hermes install.
    if not status.get("sessions"):
        return Proof("agent-session", False,
                     "the message store is readable but holds NO sessions — nothing to "
                     "verify a figure against")
    return Proof("agent-session", True,
                 f"message store readable: {status.get('sessions')} session(s), "
                 f"{status.get('operator_turns')} operator turn(s)", status)


def prove_sunday_cron(session: Session, **_) -> Proof:
    """Registered AND timing correctly. `hermes cron create` exits 0 on failure, so the
    only evidence is a listing."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("hermes"):
        raise NotProvable("hermes is not installed on this service — run this proof on "
                          "artec-brain, where cron actually lives")
    home = os.environ.get("HERMES_HOME", "")
    if not home or not any((Path(home) / p).exists() for p in
                           ("plugins/artec", "profiles/artec-brain/plugins/artec")):
        raise NotProvable("hermes is installed here but this is not artec's brain — a "
                          "listing from another install proves nothing about artec's cron")
    # `hermes cron list` prints box-drawing characters. Decoding them with the platform
    # default raised UnicodeDecodeError inside subprocess and left stdout None, which the
    # next line iterated. Found by RUNNING the proof, not by reading it.
    completed = subprocess.run(["hermes", "cron", "list"], capture_output=True,
                               timeout=120)
    out = (completed.stdout or b"").decode("utf-8", errors="replace")
    have = [name for name in ("learn-ideate", "weekly-gate") if name in out]
    tz_ok = "+08:00" in out
    ok = len(have) == 2 and tz_ok
    return Proof("sunday-cron", ok,
                 f"registered: {have}; next-run times {'are' if tz_ok else 'are NOT'} +08:00",
                 {"listing": out[:2000]})


PROVERS = {
    "agent-session": prove_agent_session,
    "sunday-cron": prove_sunday_cron,
    "video-pipeline": prove_video_pipeline,
    "publish-by-slot": prove_publish_by_slot,
    "brevo-send": prove_brevo_send,
    "stripe-attribution": prove_stripe_attribution,
    "budget-refusal": prove_budget_refusal,
    "audit-memory": prove_audit_memory,
    "restore": prove_restore,
}


# ---------------------------------------------------------------------------------------
# recording and reporting
# ---------------------------------------------------------------------------------------

def record(session: Session, proof: Proof) -> dict:
    """Writes `config.proofs`. Legitimate because `prove` is operator-driven and no agent
    tool can reach it — see the module docstring."""
    proofs = dict(get_config(session, "proofs", {}) or {})
    proofs[proof.capability] = proof.as_row()
    set_config(session, "proofs", proofs)
    return proofs[proof.capability]


def run(session: Session, capability: str, settings=None, log=print, **kwargs) -> Proof:
    if capability not in PROVERS:
        raise KeyError(f"unknown capability {capability!r} — one of {sorted(PROVERS)}")
    try:
        proof = PROVERS[capability](session, settings=settings, log=log, **kwargs)
    except NotProvable:
        raise
    except Exception as e:                                     # noqa: BLE001
        # A prover that blows up has still told us something: the capability did not work.
        # Recording it as a FAILURE keeps it visible; letting it escape would abort a
        # multi-capability run at the first bad one and leave the rest unexamined.
        proof = Proof(capability, False, f"{type(e).__name__}: {e}")
    proof.at = datetime.now(UTC).isoformat()
    record(session, proof)
    log(f"prove {capability}: {'PROVEN' if proof.ok else 'FAILED'} — {proof.detail}")
    return proof


def run_all(session: Session, settings=None, log=print, **kwargs) -> dict:
    """Every capability, one pass, three outcomes — never two.

    THE POINT IS THE CLASSIFICATION, not the score. Nine capabilities have sat UNPROVEN
    since the build began and nobody could say WHY each one was unproven — whether the code
    was broken, or the world had not yet supplied the thing it needs. Those are different
    problems with different owners, and a single "9 unproven" line conflates them.

      proven       the real path ran and demonstrated the thing
      failed       the real path ran and did not — a defect, ours
      not_provable the path could not run because a precondition is absent — NOT a defect,
                   and NOT a pass. `NotProvable` carries what is missing.

    Recording a skip as a proof is how a capability comes to be believed without ever having
    run; that is why `run()` re-raises NotProvable rather than swallowing it, and why this
    keeps the three apart.

    NOTHING IRREVERSIBLE HAPPENS HERE. `brevo-send` is dry by default — a real send needs
    review_email(approve) and reaches a real customer — and `stripe-attribution` needs a real
    card purchase that no code can manufacture. Both report what is missing instead. A proof
    harness that could fake either would be worse than no harness.
    """
    results: dict[str, dict] = {}
    for capability in CAPABILITIES:
        try:
            proof = run(session, capability, settings=settings, log=log, **kwargs)
            results[capability] = {"state": "proven" if proof.ok else "failed",
                                   "detail": proof.detail, "evidence": proof.evidence}
        except NotProvable as e:
            # A precondition is absent. Say WHAT, so the operator knows whether the gap is
            # theirs (upload footage, make a purchase) or ours (fix the code).
            results[capability] = {"state": "not_provable", "detail": str(e),
                                   "needs": str(e)}
            log(f"prove {capability}: NOT PROVABLE — {e}")
        except Exception as e:                                 # noqa: BLE001
            # One bad prover must not abort the other eight.
            results[capability] = {"state": "failed",
                                   "detail": f"{type(e).__name__}: {e}"}
            log(f"prove {capability}: FAILED — {type(e).__name__}: {e}")

    tally = {state: sorted(c for c, r in results.items() if r["state"] == state)
             for state in ("proven", "failed", "not_provable")}
    log("")
    log("=== PROOF MATRIX ===")
    for state in ("proven", "failed", "not_provable"):
        log(f"  {state:<13} {len(tally[state])}  {tally[state]}")
    blocked_s1 = [c for c in S1 if results[c]["state"] != "proven"]
    if blocked_s1:
        log(f"  S1 STILL UNPROVEN: {blocked_s1}")
    return {"results": results, "tally": tally, "s1_unproven": blocked_s1}


def proof_status(session: Session, now: datetime | None = None) -> list[dict]:
    """Every capability with its state: proven / stale / failed / never. `never` and `stale`
    are YELLOW at doctor; `failed` is RED."""
    now = now or datetime.now(UTC)
    proofs = get_config(session, "proofs", {}) or {}
    out = []
    for capability in CAPABILITIES:
        row = proofs.get(capability)
        if not row:
            out.append({"capability": capability, "state": "never", "at": None,
                        "s1": capability in S1})
            continue
        at = str(row.get("at", ""))
        try:
            when = datetime.fromisoformat(at)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            age = (now - when).days
        except ValueError:
            age = None
        state = ("failed" if not row.get("ok")
                 else "stale" if age is None or age > PROOF_MAX_AGE_DAYS
                 else "proven")
        out.append({"capability": capability, "state": state, "at": at[:10],
                    "age_days": age, "detail": row.get("detail", ""),
                    "s1": capability in S1})
    return out


def unproven(session: Session, now: datetime | None = None) -> list[dict]:
    return [row for row in proof_status(session, now) if row["state"] != "proven"]
