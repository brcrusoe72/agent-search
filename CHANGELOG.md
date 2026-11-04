# Changelog

All notable AgentSearch changes should be recorded here before cutting a
release. Use semantic version tags such as `v2.0.1`.

## Unreleased

- Pinned SearXNG to 2026.9.5 (sha256:55e1fa15a63ff04e79e213e6aa2837549877b0c6d60757cdb633ae9111cb5fea), repaired bundled engine mappings, and added offline container checks for both configurations. Live engines can still be blocked or rate-limited.
- General search queries DuckDuckGo, Brave, and Bing together instead of stopping after enough Bing results. Web ranking now includes query-term coverage; scores remain heuristics, not accuracy estimates.
- News requests preserve the specified engine list rather than adding other engines through a broad category. Live egress checks require a successfully executed network-denial probe.
- Added production GitHub governance: CodeQL, Dependabot, release workflow, and branch protection readiness.
- Added runtime hardening: pinned container images, generated SearXNG runtime secrets, constant-time bearer auth comparison, and optional live Docker smoke tests.
- Added CI hardening for tests, package builds, Compose validation, and API/MCP/Tor Docker builds.

## 2.0.0

- Introduced the AgentSearch 2.0 API surface for search, extraction, source tracing, adaptation, SDK, and MCP usage.
