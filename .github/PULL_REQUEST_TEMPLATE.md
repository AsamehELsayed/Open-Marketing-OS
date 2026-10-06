# What

<!-- What changed? Keep this to the substance. -->

# Why

<!-- The problem this solves, or the link to the issue. -->

# How it was tested

<!--
What you ran, and what you saw. Be specific enough that a reviewer can repeat it.
If you verified on a clean machine (fresh install, no dev tooling, no founder API
keys), say so explicitly and note it below.
-->

- [ ] Verified on a clean machine / clean install (required for packaging or installer changes)
- [ ] Not applicable — this change does not affect packaging or installed behaviour

# Checklist

- [ ] Tests pass (`python -m pytest -q`)
- [ ] Frontend typechecks and builds (`npm run typecheck`, `npm run build`) if `frontend/` was touched
- [ ] No secrets, API keys, or `.env` files are committed
- [ ] No unrelated changes — the diff contains only what this PR describes
- [ ] Docs updated if the change is user-facing
