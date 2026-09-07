---
name: teams-message
description: "Compose and send a readable Microsoft Teams message as the user — DM, group chat or channel: HTML that Teams actually renders, recipient verified by email, English body, draft shown before sending. Use whenever something goes out to a person or a chat in Teams (a finding, a handover, an answer, a heads-up), and as the formatting authority for any skill that posts to Teams."
argument-hint: "[recipient name/email or chat topic] [what to say]"
---

# Teams message

One message, readable at chat width (~90 chars), written as the user. Also the
formatting authority for every other skill that posts to Teams (`my-status`,
deploy/pipeline notifications): those own *what* is sent, this owns *how* it is
shaped and sent.

Transport is the **claude.ai Microsoft 365 connector**
(`mcp__claude_ai_Microsoft_365__teams_*`), which the user connects in their
claude.ai settings. There is no project-level Teams MCP server to install.

## Hard rules

- **Write HTML and pass `bodyType: "html"`.** The connector does **no markdown
  pass at all** — it takes text or HTML and sanitizes the HTML. Markdown syntax
  posted as a body lands **literally**: `**Modules:**` renders as asterisks,
  `- item` as a hyphen, `[label](url)` as brackets. This is the single most
  common way a message comes out unreadable. `bodyType: "text"` (the default)
  is for a genuinely one-paragraph message and nothing else.
- **A sent message cannot be edited or deleted.** The connector exposes no
  update or delete tool. There is no read-back-and-fix loop any more: the draft
  gate below is the *only* correction opportunity. Re-read the recipient and
  the body before sending.
- **English body, always** — whatever language the session runs in. The draft
  shown to the user is English too.
- **DM recipient resolved fresh:** `search_people` by name, match the **email**
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
- Body cap is **27000 bytes**, and sends are rate-limited per user; a batch of
  messages needs pacing and a retry after a short delay.

## Layout

Written as HTML, so each block is an explicit tag.

1. **Line 1 is the point** — what happened, or what is needed, in the first
   `<p>`. `<Name>, <point>` is fine; a greeting-only first line is not.
2. **One `<p>` per block.** Do not try to space blocks with newlines: the
   paragraph tag is what gives the message air. `<br>` is a line break *inside*
   a block.
3. **Section labels are `<p><strong>Label:</strong></p>`** or a `<strong>` lead
   on the block's own paragraph. Not `<h1>`/`<h2>`: headings render as
   oversized chat text.
4. **`<ul>` for sets, `<ol>` for ordered steps or numbered asks.** Max ~7
   `<li>` per list, one line each. More than that: split under two labels, or
   move the detail out and link it.
5. **Every link is named:** `<a href="url">label</a>`. A bare URL is autolinked
   but prints its full self, wraps over three lines and pushes the text apart.
6. **`<code>` around identifiers** — model/field/module names, paths, part
   numbers, single commands.
7. **Numbers that compare go in a `<table>`** (2-3 columns, ~6 rows max);
   tables render properly. Prose with five inline counts does not.
8. **Asks last**, under `<strong>Need from you:</strong>`, in an `<ol>`, one
   decision per line.
9. **~15 rendered lines.** Anything longer belongs in the ticket or document,
   with a named link to it.
10. **At most one emoji**, leading a status line. Never as a bullet.

## What survives the sanitizer

Verified live against the connector on 2026-09-07 by posting a probe and
reading it back with `read_resource`.

Kept: `p`, `strong`/`b`, `em`/`i`, `u`, `s`/`del`, `a href`, `ul`/`ol`/`li`
(nested included), `h1`-`h6`, `blockquote`, `code`, `table`/`thead`/`tbody`/
`tr`/`th`/`td`, `br`, `hr`. No CSS or colors survive.

| Written | What lands | Do this |
| --- | --- | --- |
| `<pre>cmd</pre>` | **tag dropped, text survives unformatted** — despite the tool doc listing `pre` as allowed | one `<code>` per line, or accept plain lines |
| `<span style=...>` | tag dropped, inner text survives, style never applies | do not hand-write styling |
| `<img src=...>` | dropped entirely | link the image, or attach it to the ticket |
| `<script>`, `<iframe>`, `onclick=` | dropped entirely | never |
| raw `<field name="x"/>` in the body | parsed as an unknown tag and dropped | escape it: `&lt;field name="x"/&gt;` |
| `&lt;field name="x"/&gt;` | renders as the literal tag text | correct form |
| `2*3*4`, `__init__.py` | survive verbatim — there is no markdown pass to mangle them | plain text is fine; `<code>` only for clarity |
| `**bold**`, `- item`, `[a](b)` | **literal asterisks, hyphens, brackets** | write the HTML tag instead |

Escape `&` as `&amp;` and `<` as `&lt;` in any body text that is not a tag you
intend.

## Send

1. **Identity:** `get_me` must be the identity node's account. The connector's
   auth is managed on the claude.ai side, so there is no command to re-run: if
   it is the wrong account or the call errors on auth, tell the user to
   reconnect the Microsoft 365 connector in their claude.ai settings, and wait.
2. **Target:** DM → `search_people` + email match → `teams_create_chat` (or the
   verified existing chat). Group chat → id from the identity node, else
   `teams_list_chats` by topic. Channel → `teams_list_channels`.
3. **Draft** → user's go.
4. **Send:** `teams_send_chat_message` / `teams_send_channel_message` with
   `bodyType: "html"`.
5. **Read back:** the send result carries a `resource:` URI
   (`teams:///chats/<urlencoded chatId>/messages/<id>`). Pass it to
   `read_resource`; `body.contentType` must be `html`. There is no fix if it is
   not, so this is a check on the *next* message, not a repair of this one.
6. Report the message id and who it went to.

## Mentions

Pass `mentions: [{id: "<AAD guid>", displayName: "Full Name"}]`. Resolve the
guid via `search_people`, with the same email check as any recipient.

**The mention is appended to the end of the body, not placed inline** — the
connector renders each one as a trailing `<at>` block, and supplying mentions
forces the body to HTML. So do not write `@Name` into the text expecting it to
become the ping: it would leave a dead `@Name` in the prose plus a stray name
at the bottom. Either let the trailing mention do the pinging on its own, or
end the body with a line that reads naturally ahead of it, such as
`<p>Over to:</p>`.

## Fixing a sent message

You cannot. Nothing edits or deletes a sent message through the connector.
Send a short correcting follow-up in the same chat, and tell the user it
happened. If the message went to the *wrong chat*, say so explicitly — the
wrong recipients keep it.
