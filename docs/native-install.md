# Native Install

AgentSearch can run without Docker as a normal Python service. Native mode runs the FastAPI wrapper, cache, extraction kill chain, adapters, and API server on the host.

You still need a SearXNG instance somewhere. Docker Compose remains the easiest way to run both AgentSearch and SearXNG together. Native mode is for users who already run SearXNG, want to install SearXNG separately, or cannot use Docker for the AgentSearch API process.

## Requirements

- Python 3.11+
- A reachable SearXNG instance with JSON search enabled
- `ffmpeg` available on `PATH` for YouTube transcript extraction

AgentSearch and SearXNG are separate services. The native installer below installs the AgentSearch API on the host. Point `SEARXNG_URL` at any SearXNG instance you control, whether it is running on the same machine, another host, or through a package/service manager.

## Quick Start

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
./scripts/install-native.sh
```

Edit `.env.native` if your SearXNG instance is not at `http://localhost:8080`, then start AgentSearch:

```bash
./scripts/run-native.sh
```

`.env.native` is ignored by git. Keep local runtime settings there instead of in
shell startup files like `~/.bashrc`.

Verify:

```bash
curl http://localhost:3939/health
curl "http://localhost:3939/search?q=distributed+consensus+algorithms"
```

## Manual Install

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
mkdir -p data

agent-search \
  --searxng-url http://localhost:8080 \
  --data-dir ./data \
  --adapters-dir ./adapters
```

## SearXNG Requirement

Native AgentSearch does not start SearXNG for you. Before starting AgentSearch, make sure this works:

```bash
curl "http://localhost:8080/search?q=test&format=json"
```

If your SearXNG URL is different, use that URL in `.env.native`:

```bash
SEARXNG_URL=http://127.0.0.1:8080
```

Your SearXNG settings must allow JSON output. In SearXNG `settings.yml`, the `search.formats` list should include `json`:

```yaml
search:
  formats:
    - html
    - json
```

How you install SearXNG is up to your host. Common options are a system package, an upstream SearXNG service install, or an already-running remote/private SearXNG instance. Docker Compose is only the bundled all-in-one path, not a requirement for AgentSearch native mode.

## systemd Service

For a long-running native install, create a service like this after running `./scripts/install-native.sh`:

```ini
[Unit]
Description=AgentSearch API
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/agent-search
EnvironmentFile=/opt/agent-search/.env.native
ExecStart=/opt/agent-search/.venv/bin/agent-search
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Adjust `/opt/agent-search` to wherever you cloned the repository.

## Configuration

The native runner accepts CLI flags and equivalent environment variables:

| Flag | Environment | Default | Purpose |
|---|---|---|---|
| `--host` | `HOST` | `127.0.0.1` | AgentSearch bind host |
| `--port` | `PORT` | `3939` | AgentSearch bind port |
| `--searxng-url` | `SEARXNG_URL` | `http://localhost:8080` | SearXNG base URL |
| `--data-dir` | `DATA_DIR` | `./data` | SQLite cache, logs, dynamic blocklist |
| `--adapters-dir` | `ADAPTERS_DIR` | `./adapters` | Extraction adapter modules |
| `--token` | `AGENT_SEARCH_TOKEN` | empty | Optional bearer token for non-health endpoints |

If `AGENT_SEARCH_TOKEN` is set, all endpoints except `/health` require:

```bash
curl -H "Authorization: Bearer $AGENT_SEARCH_TOKEN" \
  "http://localhost:3939/search?q=agent+search"
```

For safer local token storage, see [Secret Handling](secrets.md).

## Notes

- Native mode stores state under `DATA_DIR` instead of `/app/data`.
- Docker mode still works unchanged because the container working directory is `/app`, so the default relative `data/` path maps to `/app/data`.
- The private Tor stack still requires Docker Compose.
