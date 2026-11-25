# unitoolkit MCP server, stdio transport. Used by Glama for its automated checks.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UNITOOLKIT_TOOLS=core

WORKDIR /app
COPY pyproject.toml README.md ./
COPY unitoolkit ./unitoolkit
COPY registry ./registry

RUN pip install --no-cache-dir . && \
    useradd --create-home --uid 10001 mcp

USER mcp
# stdio MCP: no ports, no credentials needed to start; the school-session tools
# report their own error until someone signs in, which is the designed behaviour.
ENTRYPOINT ["unitoolkit-mcp"]
