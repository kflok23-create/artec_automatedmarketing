# hermes-brain — NousResearch hermes-agent, PINNED TAG (v2026.7.30 = release 0.19.1,
# 2026-07-30), never main, never `hermes update` on a schedule.
# Extras verified against pyproject.toml at the pinned tag: messaging, cron, mcp all
# exist; `anthropic` added for the native Anthropic transport (also a real extra).
FROM python:3.12-slim

ARG HERMES_AGENT_TAG=v2026.7.30
ENV TZ=Asia/Singapore

RUN apt-get update && apt-get install -y --no-install-recommends git tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --depth 1 --branch ${HERMES_AGENT_TAG} \
    https://github.com/NousResearch/hermes-agent.git
WORKDIR /opt/hermes-agent
RUN pip install --no-cache-dir -e ".[messaging,cron,mcp,anthropic]" \
    && pip install --no-cache-dir "sqlalchemy>=2.0" "psycopg[binary]>=3.1"

# The plugin PACKAGE + profile config + cron prompts ship in the image; the entrypoint
# installs them into the VOLUME at $HERMES_HOME on every boot and verifies discovery.
COPY plugins/artec /bootstrap/plugins/artec
COPY deploy/hermes-brain/config.yaml /bootstrap/config.yaml
COPY deploy/hermes-brain/cron-learn-ideate.txt /bootstrap/cron-learn-ideate.txt
COPY deploy/hermes-brain/cron-weekly-gate.txt /bootstrap/cron-weekly-gate.txt
# THESE THREE WERE MISSING AND THE FIRST DEPLOY CRASHED ON ONE OF THEM. All three exist in
# the repo; none was COPYed, so `cat` and `python` inside the entrypoint hit a file that was
# never in the image. Packaging/environment class — the code was right and never shipped.
#   cron-nightly-digest.txt → `cat` returned nothing, so `hermes cron create` got an empty
#     prompt, answered "create requires either prompt or at least one skill", AND EXITED 0.
#     Job 12 never registered; the boot guard hard-failed, which is why the service CRASHED.
#   probe_scouting.py, audit_memory_report.py → both guarded by `|| echo WARN`, so they
#     failed SILENTLY. The digest has been reporting "scouting: NOT YET PROBED" and an
#     agent-memory-audit line for scripts that were not in the image at all.
# tests/unit/test_deploy_packaging.py now asserts every path the entrypoint reads is COPYed.
COPY deploy/hermes-brain/cron-nightly-digest.txt /bootstrap/cron-nightly-digest.txt
COPY deploy/hermes-brain/probe_scouting.py /bootstrap/probe_scouting.py
COPY deploy/hermes-brain/audit_memory_report.py /bootstrap/audit_memory_report.py
COPY deploy/hermes-brain/purge_memory_claims.py /bootstrap/purge_memory_claims.py
COPY deploy/hermes-brain/report_board.py /bootstrap/report_board.py
COPY deploy/hermes-brain/report_volume.py /bootstrap/report_volume.py
# The three §9 proofs whose evidence lives on THIS volume and nowhere else. Self-contained
# for the same reason every script above is: the brain image does not carry the app package.
COPY deploy/hermes-brain/prove_brain.py /bootstrap/prove_brain.py
COPY deploy/hermes-brain/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV HERMES_HOME=/data/hermes
ENTRYPOINT ["/entrypoint.sh"]
