# Local Credentials

This directory is for local-only AgentSearch secrets. Everything here is ignored
by git except this README, `.gitignore`, and `*.example` templates.

Use this file for an AgentSearch bearer token:

```text
credentials/agent-search-token.txt
```

The file should contain only the token value, with no quotes or shell syntax.

Do not store GitHub, PyPI, OpenAI, Claude, or other third-party credentials in
this repository. Use each provider's CLI, keychain, or a dedicated secret manager.
