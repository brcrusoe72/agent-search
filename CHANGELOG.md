# Changelog

All notable AgentSearch changes should be recorded here before cutting a
release. Use semantic version tags such as `v2.0.1`.

## Unreleased

+- Added a `youcom` direct provider calling You.com's keyless Agents API (free tier, no key needed). Registered as a named direct provider only — no default strategy pack includes it, so existing behavior is unchanged.
- Added production GitHub governance: CodeQL, Dependabot, release workflow, and branch protection readiness.
- Added runtime hardening: pinned container images, generated SearXNG runtime secrets, constant-time bearer auth comparison, and optional live Docker smoke tests.
- Added CI hardening for tests, package builds, Compose validation, and API/MCP/Tor Docker builds.

## 2.0.0

- Introduced the AgentSearch 2.0 API surface for search, extraction, source tracing, adaptation, SDK, and MCP usage.
