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
COPY deploy/hermes-brain/report_volume.py /bootstrap/report_volume.py
COPY deploy/hermes-brain/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV HERMES_HOME=/data/hermes
ENTRYPOINT ["/entrypoint.sh"]
