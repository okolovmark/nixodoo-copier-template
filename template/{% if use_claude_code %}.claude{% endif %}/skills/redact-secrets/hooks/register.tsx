import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import { redactText, redactValue } from './redact'

// rows that reach the model and the transcript besides a tool's own result
const DOORS = new Set(['tool-result', 'tool-message', 'hook-context', 'attachment'])
// the person's own input: the prompt box, the desktop bridge, an SDK host; never a plugin, a schedule,
// a task notification or another session
const PERSON_ORIGINS = new Set(['composer', 'bridge', 'sdk'])

const isOff = atom({ plugin: 'redact-secrets', key: 'isOff' } as const, false)
const masked = atom({ plugin: 'redact-secrets', key: 'masked' } as const, 0)

async function counted($: EngineInterface, count: number, where: string): Promise<void> {
  if (count === 0) return
  await update($, masked, total => total + count)
  $.ui.toast(`redact: masked ${count} secret${count === 1 ? '' : 's'} in ${where}`)
}

type Block = { type: string; text?: unknown; content?: unknown }

function redactBlocks(blocks: readonly Block[]): { blocks: Block[]; count: number } {
  let count = 0
  const out = blocks.map(block => {
    if (block.type === 'text' && typeof block.text === 'string') {
      const done = redactText(block.text)
      count += done.count
      return done.count === 0 ? block : { ...block, text: done.text }
    }
    if (block.type === 'tool_result') {
      const done = redactValue(block.content)
      count += done.count
      return done.count === 0 ? block : { ...block, content: done.value }
    }
    return block
  })
  return { blocks: out, count }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({
      name: 'redact',
      description: 'Secret masking in tool output: status, or off/on for this session',
      argumentHint: '[status | off | on]',
    })
    return started
  })

  on('command.run', { command: 'redact' }, async ($, e) => {
    const verb = e.args.trim()
    // masking goes off only by the person's hand; anyone may turn it back on
    if (verb === 'off' && !PERSON_ORIGINS.has(e.origin.kind)) {
      return { text: 'redact: only the person at the keyboard turns masking off.' }
    }
    if (verb === 'off' || verb === 'on') await update($, isOff, () => verb === 'off')
    const total = await read($, masked)
    const state = (await read($, isOff)) ? 'OFF for this session' : 'on'
    return { text: `redact: ${state}; ${total} secret${total === 1 ? '' : 's'} masked so far in this session.` }
  })

  // The tool's record itself is rewritten, so the transcript stores the masked record (and `entire`
  // pushes that), not only the text the model reads. An errored call is left to session.append.
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny !== undefined || ran.isError === true || (await read($, isOff))) return ran
    const done = redactValue(ran.result)
    if (done.count === 0) return ran
    await counted($, done.count, `${String(e.tool)} output`)
    return ran.context === undefined ? { result: done.value } : { result: done.value, context: ran.context }
  })

  on('session.append', async ($, e, next) => {
    if (!DOORS.has(e.door) || (await read($, isOff))) return next(e)
    const done = redactBlocks(e.message.content as readonly Block[])
    if (done.count === 0) return next(e)
    await counted($, done.count, e.door)
    return next({ ...e, message: { ...e.message, content: done.blocks as typeof e.message.content } })
  })
}
