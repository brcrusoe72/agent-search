# Changelog

All notable AgentSearch changes should be recorded here before cutting a
release. Use semantic version tags such as `v2.0.1`.

## Unreleased

- Added production GitHub governance: CodeQL, Dependabot, release workflow, and branch protection readiness.
- Added runtime hardening: pinned container images, generated SearXNG runtime secrets, constant-time bearer auth comparison, and optional live Docker smoke tests.
- Added CI hardening for tests, package builds, Compose validation, and API/MCP/Tor Docker builds.

## 2.0.0

- Introduced the AgentSearch 2.0 API surface for search, extraction, source tracing, adaptation, SDK, and MCP usage.
