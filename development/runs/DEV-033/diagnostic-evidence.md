# DEV-033 Sanitized Diagnostic Evidence

This is a deterministic regression fixture, not the rejected text from DEV-032 and not a real model response. The DEV-032 draft was not retained, so its claim cannot be recovered. The fixture confirms the diagnostic format is bounded, redacted, and scoped without storing a provider payload.

```json
{
  "available_source_ids": ["northstar-notes.md:c000"],
  "can_retry": true,
  "can_revise": true,
  "claim_excerpt": "[redacted email]",
  "classification": "unverified",
  "deliverable_id": null,
  "deliverable_ordinal": 1,
  "deliverable_type": "strategy_brief",
  "persisted": false,
  "project_scope_hash": "d0a1107b7a42f9de",
  "rule": "persisted_fact_source_match"
}
```

The fixture's project ID is `northstar-test-project`; the scope hash is SHA-256 truncated to 16 hex characters. Excerpt length is at most 160 characters. Only the available source ID is included, and no deliverable ID is assigned because the rejected batch was not persisted.
