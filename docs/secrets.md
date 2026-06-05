# Secret Handling

AgentSearch supports local credentials without putting secrets in chat prompts,
shell startup files, commits, or public notes.

## AgentSearch API Token

If `AGENT_SEARCH_TOKEN` is set on the server, every endpoint except `/health`
requires a bearer token.

Recommended local options, in order:

1. Export for the current shell only:

   ```bash
   export AGENT_SEARCH_TOKEN="replace-me"
   ```

2. Put runtime config in a gitignored `.env.native` file:

   ```bash
   cp .env.example .env.native
   ```

   Then edit `.env.native` locally.

3. Put only the AgentSearch bearer token in:

   ```text
   credentials/agent-search-token.txt
   ```

4. For a user-wide SDK/MCP token, put only the token value in:

   ```text
   ~/.config/agent-search/token
   ```

## What Not To Do

Do not put secrets in:

- `~/.bashrc`, `~/.profile`, or other shell startup files
- README files, notes, test fixtures, prompts, or chat messages
- committed `.env` files
- GitHub issue comments, PR comments, or Actions logs

## Third-Party Tokens

GitHub, PyPI, OpenAI, Anthropic/Claude, and similar provider credentials should
use the provider's CLI login, keychain, or dedicated secret store. They should
not be stored in this repository.

If a token is pasted into chat, committed, or placed in a broadly readable file,
treat it as compromised: revoke it and create a new one.
