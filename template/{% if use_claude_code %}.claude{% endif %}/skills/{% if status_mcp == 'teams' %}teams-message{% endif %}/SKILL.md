---
name: teams-message
description: "Compose and send a readable Microsoft Teams message as the user — DM, group chat or channel: markdown that Teams actually renders, recipient verified by email, English body, draft shown before sending. Use whenever something goes out to a person or a chat in Teams (a finding, a handover, an answer, a heads-up), and as the formatting authority for any skill that posts to Teams."
argument-hint: "[recipient name/email or chat topic] [what to say]"
---

# Teams message

One message, readable at chat width (~90 chars), written as the user. Also the
formatting authority for every other skill that posts to Teams (`my-status`,
deploy/pipeline notifications): those own *what* is sent, this owns *how* it is
shaped and sent.

## Hard rules

- **`format: "markdown"` on every send and every update.** The tool default is
  `text`, which Graph posts as `contentType: text` and Teams renders as ONE
  paragraph: newlines, blank lines and `-` bullets all collapse into a wall of
  prose. This is the single most common way a message comes out unreadable.
- **English body, always** — whatever language the session runs in. The draft
  shown to the user is English too.
- **DM recipient resolved fresh:** `search_users` by name, match the **email**
  in the result, and only then take/create the chat. Never reuse a chat id
  carried in context — the id encodes an opaque GUID, so a wrong recipient is
  invisible in the call and in the success response. Watch for duplicate
  same-name accounts (old/disabled mailboxes); pick the live one. Fixed group
  chat ids from the identity memory node are exempt.
- **It is the user speaking, not an assistant.** No offers of extra work
  ("want me to check X", "let me know if you want a hand") — the message is
  signed by a person with their own backlog. End on the fact, or on the step
  the *recipient* takes.
- **No AI style tells:** no em dashes, no contractions (write "do not", "it
  is", "I have"), no "not just X, but Y", no triadic parallel lists, no
  buzzwords (delve, leverage, seamless, robust, empower, unlock), no emoji
  bullets, no "I am excited to" / "Let's connect".
- **Draft first, send on the go.** Show the exact text and wait. A request that
  already dictates the content ("send him: ...") is itself the go.
- **One message per go.** Never split into a burst of follow-ups.

## Layout

1. **Line 1 is the point** — what happened, or what is needed. `<Name>, <point>`
   is fine; a greeting-only first line is not.
2. **Blank line between blocks.** A single newline renders as `<br>` (line break
   inside a block), a blank line starts a new paragraph. Use blank lines between
   blocks so the message has air.
3. **Section labels are `**Label:**` on their own line.** Not `#` headings:
   `h1`/`h2` render as oversized chat text.
4. **`-` bullets for sets, `1.` for ordered steps or numbered asks.** Max ~7
   items per list, one line each. More than that: split under two labels, or
   move the detail out and link it.
5. **Every link is named:** `[label](url)`. A bare URL is autolinked but prints
   its full self, wraps over three lines and pushes the text apart. Link rows
   read `- **SMT BOM:** [MRP/2631](url)`.
6. **Backticks around identifiers** — model/field/module names, paths, part
   numbers, single commands. Multi-line commands go in a fenced block.
7. **Numbers that compare go in a table** (2-3 columns, ~6 rows max); tables
   render properly. Prose with five inline counts does not.
8. **Asks last**, under `**Need from you:**`, numbered, one decision per line.
9. **~15 rendered lines.** Anything longer belongs in the ticket or document,
   with a named link to it.
10. **At most one emoji**, leading a status line. Never as a bullet.

## What survives the renderer

`format: "markdown"` runs the text through `marked` (GFM, `breaks: true`) and
then a strict sanitizer. Allowed through: `b/strong`, `i/em`, `u`, `s/del`, `a`,
`ul/ol/li` (nested lists included), `h1`-`h6`, `blockquote`, `code`, `pre`,
`hr`, `table/thead/tbody/tr/th/td`, `img`, `br`, `p`. Everything else is
dropped, and no CSS or colors survive.

Traps, all verified against that pipeline:

| Written | What lands | Do this |
| --- | --- | --- |
| `<field name="x"/>` or any tag-shaped text | **silently deleted** — the sanitizer drops unknown tags with their contents | backticks or a fenced block |
| hand-written `<span style=...>`, `<script>` | tag stripped (text may survive), styles never apply | do not hand-write HTML |
| `__init__.py`, any dunder | `**init**.py`, bolded | backticks |
| `2*3*4`, two or more `*` in a line | `2<em>3</em>4` | backticks |
| `qty < 5`, `a -> b` | escaped correctly, renders as typed | fine as is |
| `module_name`, `_compute_x` | intact (single intra-word underscores) | fine as is |

`scripts/preview.sh <draft.md>` prints the exact HTML Graph will receive — run
it when a draft carries tag-shaped text, asterisks or dunder names.

## Send

1. **Auth:** `mcp__teams__auth_status` must be the identity node's account. Not
   authenticated → run the re-auth yourself (do not hand the command to the
   user): `npx -y git+https://github.com/okolovmark/teams-mcp.git#stable authenticate`
   (add `--device-code` when the box has no browser), tell the user their
   browser is waiting for the passkey, then re-check.
2. **Target:** DM → `search_users` + email match → `create_chat` (or the
   verified existing chat). Group chat / channel → id from the identity node,
   else `list_chats` / `list_channels` by topic.
3. **Draft** → user's go.
4. **Send:** `mcp__teams__send_chat_message` / `mcp__teams__send_channel_message`
   with `format: "markdown"`.
5. **Read back:** `get_chat_messages` with `contentFormat: "raw"`, newest
   message — the content must be HTML (`<p>`, `<ul>`). Plain text means the
   format flag did not take: `delete_chat_message`, then resend.
6. Report the message id and who it went to.

## Mentions

Write `@Full Name` in the text and pass
`mentions: [{mention: "Full Name", userId: "<AAD guid>"}]`. The `<at>` rewrite
happens after sanitizing, so it survives; Teams pings the person. `@"Full Name"`
also matches. Resolve `userId` via `search_users` / `search_users_for_mentions`
with the same email check as any recipient.

## Fixing a sent message

- Wrong content → `update_chat_message` with `format: "markdown"` (same
  default-`text` trap applies).
- Wrong recipient → `delete_chat_message` in that chat, resend to the verified
  one, and tell the user it happened.
