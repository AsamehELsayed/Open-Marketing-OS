# DEV-035 Before-fix reproduction

**Base:** Public `main` commit `3c84e33a81a1a3333a0b131503a3bbae9b8635f5`.
**Method:** Direct deterministic call to `_validate_persisted_facts()`; no model/provider used.

```text
Input fact: **Business name:** Northstar Coffee
Allowed source value: Northstar Coffee
Observed rule: persisted_fact_source_match
Observed claim excerpt: **Business name:** Northstar Coffee
Result: REPRODUCED before source changes
```
