# hermes-brain — NousResearch hermes-agent, PINNED TAG, never main, never `hermes update`
# on a schedule. Runs the Telegram gateway + the two Sunday cron jobs; talks to Postgres
# ONLY through the six tools in plugins/artec_hermes.py.
FROM python:3.12-slim

ARG HERMES_AGENT_TAG=v2026.7.30

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --depth 1 --branch ${HERMES_AGENT_TAG} \
    https://github.com/NousResearch/hermes-agent.git
WORKDIR /opt/hermes-agent
RUN pip install --no-cache-dir -e ".[messaging,cron,mcp]" \
    && pip install --no-cache-dir "sqlalchemy>=2.0" "psycopg[binary]>=3.1"

# The plugin + profile config ship in the image; the entrypoint installs them into the
# VOLUME at $HERMES_HOME on every boot (config/memory/sessions live on the volume and
# survive redeploys — doctor's marker test proves it).
COPY plugins/artec_hermes.py /bootstrap/plugins/artec_hermes.py
COPY deploy/hermes-brain/config.yaml /bootstrap/config.yaml
COPY deploy/hermes-brain/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV HERMES_HOME=/data/hermes
ENTRYPOINT ["/entrypoint.sh"]
