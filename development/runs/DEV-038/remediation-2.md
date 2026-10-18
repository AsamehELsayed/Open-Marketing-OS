# DEV-038 export-seam remediation

The independent reviewer returned `APPROVE_WITH_CHANGES`: the synthetic runner manually serialized the persisted deliverable instead of using the application's scoped Markdown export service.

Implementer: call `app.services.campaign_deliverables.markdown_export` with the selected project, campaign, and persisted deliverable ID. Assert that its scoped row identity and body match the reread persisted row, then write exactly the service's returned Markdown. Add a regression that fails if the service is bypassed. Replay the complete offline workflow in a fresh isolated output directory while preserving zero provider traffic and database integrity.

After this fix, a separate QA execution must verify export bytes against the production service result. A separate reviewer must re-review and return `APPROVE` before DEV-038 can be marked approved.
