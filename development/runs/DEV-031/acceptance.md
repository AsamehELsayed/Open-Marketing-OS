# DEV-031 Phase A — Browser Acceptance

Environment: isolated public-repo checkout with disposable local workspace data; no provider credentials were supplied to the acceptance server.

## Two-client scenario

- Created **Northstar Coffee** through the client flow with only a business name. All other profile inputs were visibly optional and remained blank.
- Uploaded the synthetic `northstar-product.txt` file through the existing Files UI. The file appeared as an indexed Northstar project file.
- Attempted real brief generation. No provider invocation succeeded because the managed local runtime was unavailable in this isolated checkout and OpenAI/OpenRouter credentials were not configured. Settings displayed `Qwen2.5-7B-Instruct` as the active base model, but no actual provider/model was used for generation.
- Manual brief editing and saving worked after the provider failure. The saved brief separated user-provided facts, retrieved evidence, AI suggestions, and missing information; it did not invent a citation.
- Clicked Regenerate on the saved brief. The browser displayed its explicit replacement confirmation. Dismissing it left the saved text and revision unchanged.
- Restarted the app against the same disposable data directory. Northstar’s name, saved brief, and indexed upload remained present.
- Created **Harbor Studio** through the same name-only flow. Its profile contained only the business name; its brief and project file list were empty.
- In Harbor, Sync Workspace Data reported that shared workspace files are not imported into this client. Rebuild Knowledge Index reported that no knowledge files were found; indexed documents and Knowledge Files both remained 0.
- Harbor generation returned the friendly status: `No AI provider is ready. The local model is unavailable and OpenAI is not connected.` The UI reported generation failed, kept the brief editable, and stated manual editing and saving remain available. No provider or model is claimed because no route started.
- Switched back to Northstar; its saved brief remained intact and only `northstar-product.txt` appeared in the project file list. Harbor never displayed Northstar’s brief or upload.

## Verification

- Broader focused regression set: **124 passed** after updating migration assertions for schema v13.
- Final DEV-031 API-focused suite after output validation changes: **4 passed**.
- Frontend typecheck: passed.
- Frontend production build: passed.
- Real AI output: **not generated**. The deterministic fake provider is used only by tests; it does not count as real generation.
**Remediation follow-up:** The confirmation prompt now states that successful generation immediately saves over the current saved brief and discards unsaved editor changes. A frontend regression assertion pins that wording and the confirmation guard. The browser was reopened after this change; a Regenerate attempt reached the friendly no-provider failure, kept the original saved brief intact, and left the editor enabled. The native confirmation text itself was verified in source and by the focused regression assertion.

**SOL review follow-up:** Focused tests now verify supplied optional profile values round-trip at creation, invalid creation leaves no project row, punctuated/bare unsupported references are rejected without changing the brief, concurrent first reads of a legacy project share one initialized profile, and delayed profile saves/brief saves/generation responses are ignored after an A→B→A switch.
