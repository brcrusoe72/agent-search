# AgentSearch Terminal GIF Guide

This repo has a reproducible workflow for creating a short terminal demo GIF for AgentSearch.

## Recommendation

Use `vhs` first. It is deterministic, easy to rerun, and avoids live-recording mistakes. Keep `asciinema` plus `agg` as the fallback when you want a quick manual recording.

## Recommended Demo Flow

Keep the story short:

1. Start or confirm the stack with `docker compose up -d`
2. Wait briefly for readiness
3. Query the AgentSearch API
4. Stop on clean JSON output

Canonical demo command:

```bash
curl -s "http://localhost:3939/search?q=python+async+patterns&count=3" | jq '.'
```

This matches the real repo behavior documented in the README.

## Preferred Workflow: VHS

Install `vhs`:

```bash
go install github.com/charmbracelet/vhs@latest
```

Demo tapes:

- `docs/demo/search-api-demo.bash.tape`
- `docs/demo/search-api-demo.powershell.tape`

Render from the repo root on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\docs\demo\render_demo.ps1
```

Or render the bash tape directly:

```bash
vhs docs/demo/search-api-demo.bash.tape
```

## Fallback Workflow: Asciinema + Agg

Install:

```bash
pip install asciinema
cargo install agg
```

Record:

```bash
asciinema rec demo.cast
```

Inside the recording:

```bash
docker compose up -d
sleep 3
curl -s "http://localhost:3939/search?q=python+async+patterns&count=3" | jq '.'
```

Render:

```bash
agg demo.cast demo.gif --theme monokai --cols 100 --rows 30
```

## Practical Notes

- Practice the commands first so the demo stays tight.
- Keep the final recording under 15 seconds.
- If startup is slow, bring the stack up before recording. Keep `docker compose up -d` in the tape anyway so the workflow is honest and repeatable.
- Use `curl.exe` in PowerShell recordings to avoid the `Invoke-WebRequest` alias.
- If `jq` is unavailable, `python -m json.tool` works as a fallback, but `jq` usually looks better in terminal demos.

## README Embedding

If you later commit a rendered GIF to `docs/demo/demo.gif`, embed it like this:

```markdown
![AgentSearch Demo](docs/demo/demo.gif)
```

If the GIF is not committed yet, link to this guide instead of referencing a missing asset.
