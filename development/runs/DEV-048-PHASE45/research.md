# Research handoff

ROLE: research. EXECUTOR: independent in-session agent `source_research`.

External OpenCode research execution was rejected by automatic approval review because it could transmit private repository source to that model service. This separate in-session execution is the disclosed general-lane fallback; it did not invoke OpenCode or another model service. No sub-delegation occurred.

Artifact: `docs/research/marketing-connectors.md`.

Inspected actual public source with GitHub fetch tools, including Postiz MCP/auth/tool loader, Activepieces LinkedIn/Instagram implementations, Planable draft-batch instructions/license, Metricool Python server/auth/OAuth, official SDK versioned stdio source and current client, maintained LangChain adapter, Harness tools/plugin-manager source. Compared licenses, authentication, costs, hosting and Windows/Electron constraints. Blob identities are preserved in the artifact.

Recommendation: existing `mcp==1.30.0` SDK stdio proof, existing OMOS gateway/registry/vault/campaign revision/audit policies, local synthetic approved campaign only. Current SDK stable 2.3.0 and new `langchain.mcp` are recorded without forcing dependency migration. Next candidate is a separately hosted Postiz read-only discovery pilot after separate account authorization and revision-specific license checks.

Important findings: Postiz inspected LICENSE is AGPL3; Activepieces enterprise exception includes two directories; Planable MIT skills are not an open-source server; generic Metricool org fork is not its connector; actual Python Metricool source has no root license in inspected listing and must not be assumed MIT. Exact Planable OAuth metadata and numerical localized pricing remain unverified.

Tests: no runtime/product tests executed in research role. Source inspection only. No accounts, outbound provider calls, publishing, code modifications, merges, tags or deployments.

Implementation and tests remain pending founder plan approval; report must not be represented as QA or completion.

Completion timestamp: 2026-11-02T11:51:40Z (UTC). This agent did not independently capture its start timestamp; the orchestrator should use its actual launch record rather than infer a start from prose.
