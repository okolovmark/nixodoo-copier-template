---
name: conventions_tag_vocabulary
description: Editable vocabulary of valid hashtags for journal entries
type: reference
created: 2026-04-09
updated: 2026-04-09
---

# Tag vocabulary

This is the **canonical list** of tags used in journal entries.
**Edit this file freely** to add new tags as the project evolves.
Claude reads it before writing any new tag and uses existing ones
in preference to inventing new ones.

A session typically has 2-5 tags: one module + one work-type +
zero or more domain tags + optionally one status tag.

## Module / area tags (where in the codebase)

`#custom_base` — common overrides (partner, currency, sequence)
`#custom_sale` — sale order extensions
`#custom_stock` — inventory, scrap categories, MSL
`#custom_hr` — timekeeping, leave, OT
`#custom_mrp` — manufacturing
`#custom_project` — project management
`#msl_module` — material storage location (cross-cutting)
`#queue_job` — OCA queue_job / queue_job_cron
`#connector` — OCA component / connector
`#web_responsive` — OCA web modules
`#odoo_core` — base Odoo 16 CE (read-only, but sometimes referenced)

## Type-of-work tags (what kind of activity)

`#bug` — fixing a defect
`#feature` — new functionality
`#refactor` — restructuring without behavior change
`#review` — code review (own or others')
`#testing` — writing/fixing tests
`#docs` — documentation
`#config` — configuration, settings, env
`#research` — investigation, exploration without committing changes
`#discussion` — decision-making conversation, no code changes
`#cleanup` — small housekeeping, dead code removal
`#migration` — schema/data migration scripts
`#data_fix` — one-off production data correction

## Domain / concern tags (cross-cutting)

`#performance` — speed, queries, caching
`#security` — auth, permissions, vulnerabilities
`#compliance` — legal, regulatory, audit requirements
`#accessibility` — a11y
`#i18n` — internationalization, translations
`#data_integrity` — constraints, validations, consistency
`#ux` — user experience, form layouts
`#api` — REST / RPC interfaces

## Status tags (lifecycle markers, optional)

`#wip` — work in progress, intentionally unfinished, will continue next session
`#blocked` — waiting on something external (must also appear in state.md `## Blocked`)
`#ready_for_review` — done from Claude's side, needs Mark's eye (must also appear in state.md `## Ready for review`)
`#ship_blocker` — must be resolved before next deploy (highest urgency, in state.md `## Ship blockers`)

**Important:** any session with `#wip`, `#blocked`, `#ready_for_review`, or
`#ship_blocker` MUST have a corresponding entry in `state.md` so the standup
picks it up. See [[conventions_behavior_protocol]] for the update protocol.
