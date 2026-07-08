---
name: conventions_node_format
description: Rules for writing and linking memory nodes in this project
type: reference
created: 2026-04-09
updated: 2026-04-09
---

# Node format and conventions

## Frontmatter (required)

Node frontmatter follows Claude Code's built-in memory schema, which is in the
system prompt (always in context) — no need to restate the full YAML here. In
short: `name` + `description` at the top level; `type`
(`user | feedback | project | reference`), `created`, `updated` under a nested
`metadata:` block; the harness maintains `metadata.node_type` itself and
normalizes the block on every write.

Project specific: the **slug** is `lowercase_underscore_separated` and **must
match the filename** (`<type>_<topic>.md`). The system-prompt example shows a
kebab-case slug; this project uses underscores.

## Naming convention

- Filename = `<type>_<topic>.md` (e.g. `feedback_testing.md`, `project_msl_bug.md`)
- Slug uses underscores, lowercase only
- For multi-word topics: `feedback_terse_responses.md`

## Atomicity (soft rule)

Each node should cover **one** distinct rule, fact, decision, or reference.
If a node grows beyond ~50 body lines or starts covering multiple unrelated
topics, split it into atomic sub-nodes linked via `Part_of:` to a parent
overview node.

Why: atomic nodes are more grep-friendly, easier to update without
unintended side effects, and link more cleanly into the graph.

Exception: convention/reference nodes (like this one) are intentionally
larger because they're cohesive bodies of rules.

## Body structure by type

### type: user
Just the fact, optionally with context. Short.

### type: feedback
Rule the user gave Claude. Always include Why and How to apply.

```markdown
Don't mock the database in tests for this project — use TransactionCase.

**Why:** Got burned last quarter when mocked tests passed but a prod
migration silently broke. Mock/prod divergence masked the issue.

**How to apply:** When writing/reviewing tests in modules,
prefer TransactionCase + real DB fixtures over `unittest.mock` for
anything touching ORM. Pure utility functions can still use mocks.
```

### type: project
Non-obvious fact about the project, decision, gotcha.

```markdown
The MSL (Material Storage Location) module rewrite is driven by legal
compliance, not tech-debt cleanup.

**Why:** Legal flagged the old session-token storage format as
non-compliant with new data-retention requirements.

**How to apply:** Scope decisions for MSL changes should favor
compliance over developer ergonomics. Don't suggest "while we're
in here, let's refactor X" — keep changes minimal and auditable.
```

### type: reference
Pointer to an external resource or another note.

```markdown
Pipeline bugs are tracked in Linear project "INGEST".

**How to apply:** When Mark mentions a ticket number, check INGEST.
When suggesting where to file a new issue related to data ingestion,
point to INGEST.
```

## Relationships (Obsidian-compatible `[[wikilinks]]`)

Add a `## Links` section at the bottom of any node that connects to others:

```markdown
## Links

- **Related:** [[feedback_testing]], [[user_role]]
- **Part_of:** [[project_msl_module]]
- **Blocks:** [[project_mrp_release]]
- **Supersedes:** [[feedback_old_testing_rule]]
- **Trigger:** journal/2026-03-15
```

Vocabulary for node-to-node links (use `[[wikilinks]]`):
- `Related:` — loose association, use sparingly to avoid noise
- `Part_of:` / `Contains:` — hierarchy
- `Blocks:` / `Blocked_by:` — dependencies between project items
- `Supersedes:` / `Superseded_by:` — replaces older info (link both directions when retired)

For node-to-journal links use **plain text**, not wikilinks (journal entries
are not part of the graph index):
- `Trigger:` — the session that caused this rule/decision (`journal/YYYY-MM-DD`)

Note on `Trigger:` format: the path is a **slug reference**, not a
clickable file path. The actual file on disk is `journal/<date>.md`,
but the `.md` extension is omitted in the trigger reference for
readability (matches how `[[wikilinks]]` work).

Reverse links are found via grep — no need to manually maintain backlinks.

## Duplicate prevention

Before writing a new node:

1. Grep `nodes/` for keywords from the new fact: `grep -ri "<keyword>" nodes/`
2. Read any candidates that match
3. Decide:
   - **Same statement** (same rule, same domain, same scope) → update existing,
     bump `updated` date, don't create new
   - **Different aspect of related topic** → create new + add mutual `Related:` links
   - **Direct conflict** (new contradicts old) → flag to user, ask which is current,
     mark old as `Superseded_by` if confirmed
   - **Unrelated** → create new

Subjective judgment, not arithmetic. When in doubt, lean toward updating
existing rather than creating duplicates.

## Index maintenance (Claude's responsibility)

Whenever Claude creates/renames/deletes a node, also update `MEMORY.md`:
- Add the line under the right type section (User / Feedback / Project / Reference)
- Format: `- [Title](nodes/file.md) — one-line hook`
- Keep total under 200 lines AND under ~2k tokens (whichever comes first)

The standup auto-discovery check (see [[conventions_behavior_protocol]])
will surface orphan nodes — files in `nodes/` not referenced in the index.
Don't rely on it as the primary mechanism, but it's a safety net.
