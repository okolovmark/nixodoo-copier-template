import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { HAS_STATUS_GATE } from '../hooks/config'
import {
  enterPhase,
  isDocPath,
  isWorktreeCode,
  newRun,
  openGate,
  qcHolds,
  recordVerdict,
  taskKeyIn,
  testSummary,
} from '../hooks/gates'

const WT = '/p/.worktrees/kio-9-x'
const GREEN = 'INFO odoo.tests.result: 0 failed, 0 error(s) of 14 tests when loading database'
const PANE = {
  plugin: 'pipeline-gates',
  component: 'Pane',
  requestId: 'pipeline',
  props: { title: 'pipeline', isFocused: true, bodyColumns: 90, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const
const TYPED = { origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 } } as const
const DEV = { tool: 'Agent', description: 'dev', prompt: 'build it', subagent_type: 'dev' } as const

// what the person answers in the grill dialog, and what the mod asked and pushed
type Person = { answer?: string; isAway?: boolean; questions: string[]; pushes: string[] }

function engine(on: On, agents: { id: string; type: string }[] = [], person?: Person): void {
  mock.store(on)
  mock.clock(on, { now: 1_000_000 })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 'sid-1' }))
  on('tool.register', (_$, e) => ({ value: { tool: `mcp__pipeline-gates__${e.name}` } }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.toast', () => ({ value: undefined }))
  on('agent.list', () => ({
    value: agents.map(agent => ({ ...agent, description: '', status: 'completed' })),
  }))
  on('skill.prompt', (_$, e) => ({ text: e.text }))
  on('turn.complete', () => ({ text: '' }))
  on('prompt.submit', (_$, e) => ({ text: e.text }))
  on('tool.call', (_$, e) => {
    if (e.tool === 'AskUserQuestion' && person !== undefined) {
      const question = e.questions[0]?.question ?? ''
      person.questions.push(question)
      const answers = person.answer === undefined ? {} : { [question]: person.answer }
      return { result: { questions: e.questions, answers, ...(person.isAway === true ? { afkTimeoutMs: 60_000 } : {}) } }
    }
    if (e.tool === 'PushNotification' && person !== undefined) {
      person.pushes.push(e.message)
      return { result: { message: e.message, pushSent: true } }
    }
    return e.tool === 'Bash' && String(e.command).includes('--test-enable')
      ? { result: { stdout: GREEN, stderr: '', interrupted: false } }
      : { result: { ok: true } }
  })
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
}

describe('rules', () => {
  test('test summaries, paths and task keys', async () => {
    expect(testSummary(GREEN)).toEqual({ isGreen: true, text: '0 failed, 0 error(s) of 14 tests' })
    expect(testSummary(`${GREEN}\nERROR odoo.tests.result: 1 failed, 0 error(s) of 3 tests`)?.isGreen).toBe(false)
    expect(testSummary('0 failed, 0 error(s) of 0 tests')?.isGreen).toBe(false)
    expect(testSummary('no summary here')).toBe(null)
    expect(isWorktreeCode(`${WT}/models/a.py`)).toBe(true)
    expect(isWorktreeCode('/p/.worktrees/_env/kio-9-x/odoo.conf')).toBe(false)
    expect(isWorktreeCode('/p/src/odoo-addons-16/a.py')).toBe(false)
    expect(isDocPath(`${WT}/m/readme/DESCRIPTION.md`)).toBe(true)
    expect(taskKeyIn('run the pipeline for KIO-1834 please')).toBe('KIO-1834')
  })

  test('phases and verdicts refuse what their gates do not allow', async () => {
    let run = newRun('KIO-1', 0)
    expect(typeof enterPhase(run, 2, '', 1)).toBe('string')
    expect(newRun('KIO-1', 0, false).status?.isOpen).toBe(true)
    run = openGate(openGate(run, 'status', 'sent', 1), 'grill', 'confirmed', 1)
    const entered = enterPhase(run, 2, '', 2)
    expect(typeof entered).not.toBe('string')
    if (typeof entered === 'string') return
    expect(typeof recordVerdict(entered, 'pass', 'x', 3)).toBe('string')
    const ready = { ...entered, seq: 3, lastChange: 1, lastReview: 2, lastGreen: 3 }
    const passed = recordVerdict(ready, 'pass', 'QC gate: PASS', 4)
    if (typeof passed === 'string') throw new Error(passed)
    expect(qcHolds(passed)).toBe(true)
    expect(qcHolds({ ...passed, lastChange: passed.seq + 1 })).toBe(false)
    expect(typeof enterPhase({ ...passed, lastChange: passed.seq + 1 }, 6, '', 5)).toBe('string')
  })
})

test('the dev agent and worktree edits wait for the Teams status and your grill press', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.skill.prompt({ skill: 'pipeline', text: 'Odoo Development Pipeline' })

  const dev = { tool: 'Agent', description: 'dev', prompt: 'build it', subagent_type: 'dev' } as const
  const blocked = await $.tool.call(dev)
  expect(blocked.deny).toContain(
    HAS_STATUS_GATE ? 'Teams status and grill alignment gates are closed' : 'grill alignment gate is closed',
  )
  const edit = await $.tool.call({ tool: 'Write', file_path: `${WT}/models/a.py`, content: 'x' })
  expect(edit.deny).toContain('editing')
  const outside = await $.tool.call({ tool: 'Write', file_path: '/p/docs/notes.md', content: 'x' })
  expect(outside.deny).toBeUndefined()

  if (HAS_STATUS_GATE) {
    await $.tool.call({ tool: 'mcp__m365__teams_send_chat_message', chatId: '19:status-chat', body: 'status' })
    expect((await $.tool.call(dev)).deny).toContain('grill alignment gate is closed')
  }

  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await pane.press({ key: 'grill-confirm' })
  expect(await pane.find({ text: 'confirmed by you' })).toBeDefined()
  await pane.unmount()
  expect((await $.tool.call(dev)).deny).toBeUndefined()
})

test('gh pr create waits for a QC pass that rests on a review and green tests after the last change', async ($, on) => {
  engine(on, [{ id: 'a-review', type: 'review' }])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'pipeline-gate', args: 'start KIO-9', ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open status teams is down', ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open grill agreed in chat', ...TYPED })

  await $.tool.call({ tool: 'Edit', file_path: `${WT}/models/a.py`, old_string: 'a', new_string: 'b' })
  const early = await $.tool.call({ tool: 'mcp__pipeline-gates__qc_verdict', verdict: 'pass', summary: 'too early' })
  expect(String(early.result)).toContain('the pass is refused')
  expect((await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })).deny).toContain('QC gate passes')

  await $.turn.complete({ answer: 'review done', durationMs: 1, isAborted: false, turnId: 't1', agentId: 'a-review', reason: 'answer' })
  await $.tool.call({ tool: 'Bash', command: 'odoo -c x -u m --test-enable --test-tags /m --stop-after-init' })
  const pass = await $.tool.call({ tool: 'mcp__pipeline-gates__qc_verdict', verdict: 'pass', summary: 'QC gate: PASS' })
  expect(String(pass.result)).toContain('PR allowed: yes')

  // a readme edit (what the documenter writes) does not void the pass; a code edit does
  await $.tool.call({ tool: 'Edit', file_path: `${WT}/m/readme/USAGE.md`, old_string: 'a', new_string: 'b' })
  expect((await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })).deny).toBeUndefined()
  await $.tool.call({ tool: 'Edit', file_path: `${WT}/models/a.py`, old_string: 'b', new_string: 'c' })
  expect((await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })).deny).toContain('QC gate passes')
})

test('only the person opens a gate by hand', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'pipeline-gate', args: 'start KIO-9', ...TYPED })
  const byPlugin = await $.command.run({
    command: 'pipeline-gate',
    args: 'open status because',
    origin: { kind: 'plugin', name: 'other' },
    presentation: TYPED.presentation,
  })
  expect(byPlugin.text).toContain('only the person')
  const byYou = await $.command.run({ command: 'pipeline-gate', args: 'open status teams is down', ...TYPED })
  expect(byYou.text).toContain('gate is open (teams is down)')
})

test('only the person ends a run', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'pipeline-gate', args: 'start KIO-9', ...TYPED })
  const dev = { tool: 'Agent', description: 'dev', prompt: 'p', subagent_type: 'dev' } as const
  const bySchedule = await $.command.run({
    command: 'pipeline-gate',
    args: 'end',
    origin: { kind: 'scheduled-trigger' },
    presentation: TYPED.presentation,
  })
  expect(bySchedule.text).toContain('only the person')
  expect((await $.tool.call(dev)).deny).toContain('grill alignment')
  const byYou = await $.command.run({ command: 'pipeline-gate', args: 'end', ...TYPED })
  expect(byYou.text).toContain('the run is ended')
  expect((await $.tool.call(dev)).deny).toBeUndefined()
})

test('a pipeline for another task starts over with its gates closed', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: 'KIO-1' })
  await $.command.run({ command: 'pipeline-gate', args: 'open status down', ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open grill agreed', ...TYPED })
  const dev = { tool: 'Agent', description: 'dev', prompt: 'p', subagent_type: 'dev' } as const
  expect((await $.tool.call(dev)).deny).toBeUndefined()

  // the same task again keeps the run; another task does not
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: 'KIO-1' })
  expect((await $.tool.call(dev)).deny).toBeUndefined()
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: 'KIO-2' })
  expect((await $.tool.call(dev)).deny).toContain('grill alignment')
})

// the Teams status sent, so the grill gate is the one left shut
async function toGrill($: Engine): Promise<void> {
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: 'KIO-9' })
  if (HAS_STATUS_GATE) await $.tool.call({ tool: 'mcp__m365__teams_send_chat_message', chatId: '19:status-chat', body: 'status' })
}

test('a step that waits on the grill gate pushes to the phone and asks; your press opens it', async ($, on) => {
  const person: Person = { answer: 'Confirm', questions: [], pushes: [] }
  engine(on, [], person)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: 'KIO-9' })
  if (HAS_STATUS_GATE) {
    // the Teams status is the agent's to send: no dialog while it is missing
    expect((await $.tool.call(DEV)).deny).toContain('Teams status')
    expect(person.questions).toEqual([])
    await $.tool.call({ tool: 'mcp__m365__teams_send_chat_message', chatId: '19:status-chat', body: 'status' })
  }

  const entered = await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 2, task: 'KIO-9' })
  expect(String(entered.result)).toContain('phase 2 Development')
  expect(person.pushes).toEqual(['KIO-9: Phase 2 waits on your grill confirm'])
  expect(person.questions[0]).toContain('Do you agree with the Alignment Summary?')
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain('confirmed by you in the dialog')
  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  expect(person.questions.length).toBe(1)
})

test('Mechanical opens the gate for the dev agent waiting on it', async ($, on) => {
  const person: Person = { answer: 'Mechanical', questions: [], pushes: [] }
  engine(on, [], person)
  await toGrill($)
  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain('mechanical task')
})

test('Not yet keeps the gate shut and the dialog quiet until your next message', async ($, on) => {
  const person: Person = { answer: 'Not yet', questions: [], pushes: [] }
  engine(on, [], person)
  await toGrill($)
  expect((await $.tool.call(DEV)).deny).toContain('answered Not yet')
  expect((await $.tool.call(DEV)).deny).toContain('grill alignment gate is closed')
  expect(person.questions.length).toBe(1)

  // a plugin's prompt is not the person's; their own message lets the dialog ask again
  await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'plugin', name: 'other' } })
  expect((await $.tool.call(DEV)).deny).toBeDefined()
  expect(person.questions.length).toBe(1)
  person.answer = 'Confirm'
  await $.prompt.submit({ text: 'go on', wait: false, origin: { kind: 'bridge' } })
  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  expect(person.questions.length).toBe(2)
})

test('a dialog that resolved itself while you were away, or text under Other, opens nothing', async ($, on) => {
  const person: Person = { answer: 'Confirm', isAway: true, questions: [], pushes: [] }
  engine(on, [], person)
  await toGrill($)
  expect((await $.tool.call(DEV)).deny).toContain('Nobody answered the grill dialog')
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain('grill alignment: closed')
})

test('text typed under Other reaches the agent and keeps the gate shut', async ($, on) => {
  const person: Person = { answer: 'rename the field first', questions: [], pushes: [] }
  engine(on, [], person)
  await toGrill($)
  const phase = await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 2 })
  expect(String(phase.result)).toContain('the user wrote: "rename the field first"')
  expect((await $.tool.call(DEV)).deny).toContain('grill alignment gate is closed')
})
