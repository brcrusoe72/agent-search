# BUILD PROMPT - AgentSearch Terminal GIF Demo

You are working inside the `agent-search` repository.

Your job is to build a short, polished, reproducible terminal demo workflow for AgentSearch and wire it into the repo docs.

## Goal

Create a deterministic demo that shows the fastest path from zero to value:

1. Start the stack with `docker compose up -d`
2. Query the local API at `http://localhost:3939/search`
3. Show clean, pretty-printed JSON output
4. Keep the whole recording under 15 seconds

Canonical demo command:

```bash
curl -s "http://localhost:3939/search?q=python+async+patterns&count=3" | jq '.'
```

## Repo Context

This repository already exposes the exact demo flow in `README.md`:

- `docker compose up -d`
- `http://localhost:3939/search`
- port `3939`
- FastAPI app behind Docker Compose

Do not replace those commands with placeholders or alternate endpoints unless you find a real bug in the repo.

## Deliverables

Add or update:

1. `docs/TERMINAL_GIF_GUIDE.md`
2. `docs/demo/search-api-demo.bash.tape`
3. `docs/demo/search-api-demo.powershell.tape`
4. `docs/demo/render_demo.ps1`
5. `README.md` with a short pointer to the demo workflow

## Preferred Tooling

Use `vhs` as the primary path because it is deterministic.

Fallback path:

- `asciinema`
- `agg`

References:

- `https://github.com/charmbracelet/vhs`
- `https://asciinema.org`
- `https://github.com/asciinema/agg`

## Requirements

### Demo quality

- Under 15 seconds
- Monokai or another dark theme
- Readable terminal dimensions for README embedding
- Use `jq` so the JSON is formatted cleanly
- Keep the sequence minimal and confidence-inspiring

### Practical behavior

- Assume contributors may already have the stack running; `docker compose up -d` should still be included because it becomes effectively instant in that case
- Provide both bash and PowerShell variants
- Keep all demo assets under `docs/demo/`
- Do not commit a fake or missing `demo.gif`

### README behavior

- If no rendered GIF is committed, point the reader to the guide
- If a real `docs/demo/demo.gif` exists later, it can be embedded with Markdown

## VHS Tape Shape

Use a tape close to this:

```text
Output "docs/demo/demo.gif"
Set Width 1000
Set Height 600
Set Theme "Monokai"

Type "docker compose up -d"
Enter
Sleep 2s

Type "curl -s \"http://localhost:3939/search?q=python+async+patterns&count=3\" | jq ."
Enter
Sleep 3s
```

Use `curl.exe` in the PowerShell version to avoid alias ambiguity.

## Fallback Workflow

Document this alternative:

```bash
pip install asciinema
cargo install agg
asciinema rec demo.cast
agg demo.cast demo.gif --theme monokai --cols 100 --rows 30
```

## Acceptance Criteria

The work is complete when:

- A contributor can regenerate the AgentSearch terminal demo without guessing
- The commands match the real repo behavior
- The repo has a deterministic `vhs` workflow and a documented fallback
- README clearly points to the demo instructions

## Final Response Format

Return:

1. Files created or changed
2. Exact render command
3. Whether the GIF was rendered or only scaffolded
4. Any assumptions or blockers
