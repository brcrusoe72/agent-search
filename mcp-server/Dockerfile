FROM python:3.12-slim

LABEL io.modelcontextprotocol.server.name="io.github.brcrusoe72/agent-search"

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY agent_search_mcp/ ./agent_search_mcp/

RUN pip install --no-cache-dir mcp>=1.0.0 httpx>=0.27.0 && \
    pip install --no-cache-dir -e .

ENTRYPOINT ["python", "-m", "agent_search_mcp"]
