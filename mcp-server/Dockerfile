ARG PYTHON_BASE_IMAGE=python@sha256:9e01bf1ae5db7649a236da7be1e94ffbbbdd7a93f867dd0d8d5720d9e1f89fab
FROM ${PYTHON_BASE_IMAGE}

LABEL io.modelcontextprotocol.server.name="io.github.brcrusoe72/agent-search"

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY agent_search_mcp/ ./agent_search_mcp/

RUN pip install --no-cache-dir -e .

ENTRYPOINT ["python", "-m", "agent_search_mcp"]
