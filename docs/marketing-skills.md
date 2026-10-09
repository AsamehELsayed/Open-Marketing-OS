# Marketing Skills

OMOS includes the pinned, MIT-licensed Marketing Skills library from
`coreyhaines31/marketingskills`. The generated
[manifest](marketing-skills-manifest.json) and root `skills-lock.json` record the
upstream pin and checksums. The API reports the measured registry at
`/api/skills`; the Settings screen lets a user enable or disable playbooks.

Skills provide marketing knowledge. Registered tools perform actions. The skill
registry does not grant a playbook tool permissions or execute it as code.

## Role mapping

The role table assigns each skill to one OMOS category. Two entries resolve an
upstream plan-table mismatch:

- **CONTRACT DEVIATION — `ad-creative` → `content`:** the source plan listed it
  under both `content` and `paid_media`, although a skill must have one home.
- **CONTRACT DEVIATION — `revops` → `strategy`:** the source plan omitted this
  vendored skill and has no `ops` category. Revenue operations is grouped with
  analytics and attribution under strategy.

The runtime role table and generated manifest are the source of truth for the
current skill set and categories. CI verifies their checksums against the
vendored files.
