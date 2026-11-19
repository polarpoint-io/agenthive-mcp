# [1.6.0](https://github.com/polarpoint-io/agenthive-mcp/compare/v1.5.0...v1.6.0) (2026-11-19)


### Bug Fixes

* **import-docs:** address PR review findings ([d7f47ab](https://github.com/polarpoint-io/agenthive-mcp/commit/d7f47ab5e129a4f8e76d458ac96e57168c1f1282))
* **tag-filtering:** recompute approx_tokens_full_neighborhood after exclude_tags ([f90adf2](https://github.com/polarpoint-io/agenthive-mcp/commit/f90adf2bdb46418cd3a9ca865a0768704eb42fb3))


### Features

* backfill shared memory from a repo of markdown docs ([4646943](https://github.com/polarpoint-io/agenthive-mcp/commit/46469431b6d60cc5f28492e3f471914743029008))
* **cli:** add import-docs to backfill memory from a markdown repo ([3078a30](https://github.com/polarpoint-io/agenthive-mcp/commit/3078a304ce0e40cdf3af2bf5ed9e1c69752ac656))
* filter retrieve_context/retrieve_index by tag (include and exclude) ([f0a4e8d](https://github.com/polarpoint-io/agenthive-mcp/commit/f0a4e8d1c5e5780695b354dad3ae6ec0042142fc))
* **import-docs:** accept a remote git URL for PATH ([c94f30d](https://github.com/polarpoint-io/agenthive-mcp/commit/c94f30d0e832f3a5cc4f554bc428299b606444e9))
* **import-docs:** add --exclude to scope a repo before --apply ([70fe162](https://github.com/polarpoint-io/agenthive-mcp/commit/70fe162eb064b92e8b568513899ef3c5a9a5db07))
* **import-docs:** add --since for CI-safe automated imports ([90b245a](https://github.com/polarpoint-io/agenthive-mcp/commit/90b245a22f80580cbbd40924b2dc467eede9abe7))
* **import-docs:** filter and tag by a file's own frontmatter tags ([0117765](https://github.com/polarpoint-io/agenthive-mcp/commit/011776574ea3a09801fbe182b3518f6d9e686b92))
* **import-docs:** make --apply safe to re-run before review catches up ([c835f2a](https://github.com/polarpoint-io/agenthive-mcp/commit/c835f2a0be4d627a157da18928b1db7024c35701))
* **server:** filter retrieve_context/retrieve_index by tag ([cdf6900](https://github.com/polarpoint-io/agenthive-mcp/commit/cdf6900b825052a40c079716d0b50892ada71048))

# [1.5.0](https://github.com/polarpoint-io/agenthive-mcp/compare/v1.4.0...v1.5.0) (2026-11-19)


### Bug Fixes

* **codemap:** address PR review findings ([c84b606](https://github.com/polarpoint-io/agenthive-mcp/commit/c84b606ee01bd98851515205f41901375c5cf443))


### Features

* Codebase map hook + Copilot Chat as a fourth harness ([42b2be8](https://github.com/polarpoint-io/agenthive-mcp/commit/42b2be8a042c8b3195a75283c265adf498c7ea61))
* **plugin:** inject a local codebase map alongside team facts ([5552050](https://github.com/polarpoint-io/agenthive-mcp/commit/55520506b739cad328ffce2544d8241dcce9dbf3))
* **plugin:** wire up Copilot Chat as a fourth installable harness ([3c9f721](https://github.com/polarpoint-io/agenthive-mcp/commit/3c9f72191e7651b247204bb8ed02a4ca1c816a21))

# [1.4.0](https://github.com/polarpoint-io/agenthive-mcp/compare/v1.3.0...v1.4.0) (2026-11-03)


### Features

* **plugin:** add `agenthive install` so the plugin has a real install path ([f110e54](https://github.com/polarpoint-io/agenthive-mcp/commit/f110e5431bd4adff459a0f8abb829aa398b7e07f))

# [1.3.0](https://github.com/polarpoint-io/agenthive-mcp/compare/v1.2.0...v1.3.0) (2026-11-02)


### Features

* **plugin:** one hook implementation for Claude Code, VS Code and Cursor ([066382f](https://github.com/polarpoint-io/agenthive-mcp/commit/066382ff24f1e3be3abcf50393d6d616d675127e))
* **tools:** confirm_memory, and a session id so evidence is possible ([11ac2f1](https://github.com/polarpoint-io/agenthive-mcp/commit/11ac2f16b0c681d41939bfe103707819fc39c7b8))


### Performance Improvements

* **tools:** cut the per-request definition overhead from ~1130 to ~378 ([81858b2](https://github.com/polarpoint-io/agenthive-mcp/commit/81858b25d91e6b8a952333131c83716ca6465cb7))

# [1.2.0](https://github.com/polarpoint-io/agenthive-mcp/compare/v1.1.0...v1.2.0) (2026-10-17)


### Features

* **tools:** expose report_gaps, and keep the other proposals out ([9b99b77](https://github.com/polarpoint-io/agenthive-mcp/commit/9b99b77de50f12c2af0c9cbade21a7cd1586b1aa))
* **tools:** expose report_gaps, and keep the other proposals out ([1f4a047](https://github.com/polarpoint-io/agenthive-mcp/commit/1f4a0475334ecd56ee8c495b641f86eb5fb36132))

# [1.1.0](https://github.com/polarpoint-io/agenthive-mcp/compare/v1.0.0...v1.1.0) (2026-10-17)


### Bug Fixes

* **hook:** make agenthive-log actually fail-safe, scoped and idempotent ([225a5fd](https://github.com/polarpoint-io/agenthive-mcp/commit/225a5fd9ee1025591405e6171a6b3bcc549114ea)), closes [#1](https://github.com/polarpoint-io/agenthive-mcp/issues/1)


### Features

* **tools:** add retrieve_index and fetch_nodes, and the agenthive-lo… ([9d2a713](https://github.com/polarpoint-io/agenthive-mcp/commit/9d2a713d5fe5a191f8236dda118a1190ea72c572))
* **tools:** add retrieve_index and fetch_nodes, and the agenthive-log hook ([afa37cb](https://github.com/polarpoint-io/agenthive-mcp/commit/afa37cbc86849fa81aa9dfb48b911213a79dce98))

# 1.0.0 (2026-09-16)


### Bug Fixes

* **ci:** pin mcp <2 and fix ruff 0.16 default-ruleset violations ([b1d3dbb](https://github.com/polarpoint-io/agenthive-mcp/commit/b1d3dbbc32ce7b9abbbb2aa3599e07d1c244ae76))


### Features

* add agent/task tools and a hero banner ([b863b8f](https://github.com/polarpoint-io/agenthive-mcp/commit/b863b8f2052cde71b1e5abd62e5c8b42e2ccc8ea))
* initial standalone MCP server for AgentHive ([272ff4b](https://github.com/polarpoint-io/agenthive-mcp/commit/272ff4b968bb342956df4f8f5e991c620949cc8f))
