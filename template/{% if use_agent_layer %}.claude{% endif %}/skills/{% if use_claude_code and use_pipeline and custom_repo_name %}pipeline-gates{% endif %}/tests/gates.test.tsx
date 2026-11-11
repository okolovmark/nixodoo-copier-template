import { describe, expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import type { BlockTask } from '../types'
import { HAS_STATUS_GATE, ODOO_WEB_URL, RELEASE_NOTES_DIR, TASK_URL_BASE, TICKET_PREFIXES } from '../hooks/config'
import {
  addTasks,
  blockLine,
  closedForDev,
  closesOn,
  enterPhase,
  isChildArgs,
  isDocPath,
  isMotherRunFor,
  isNewRound,
  isPrCreate,
  isWorktreeCode,
  moveTasks,
  newRun,
  openGate,
  qcHolds,
  recordVerdict,
  roundsLeft,
  runKeys,
  startRound,
  takeGates,
  taskKeyIn,
  taskKeysIn,
  taskUrlIn,
  testSummary,
  withoutStrayTasks,
} from '../hooks/gates'

// task keys in this project's ticket prefixes; with none, any word-dash-number is a key
const K = TICKET_PREFIXES[0] ?? 'KIO'
const HAS_PREFIX = TICKET_PREFIXES.length > 0
const PR_URL = 'https://github.com/acme/addons/pull/77'
const WT = '/p/.worktrees/kio-9-x'
const GREEN = 'INFO odoo.tests.result: 0 failed, 0 error(s) of 14 tests when loading database'
const PANE = {
  plugin: 'pipeline-gates',
  component: 'Pane',
  requestId: 'pipeline',
  props: { title: 'pipeline', isFocused: true, bodyColumns: 90, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const
const BAND = {
  plugin: 'pipeline-gates',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120, scroll: { offset: 0, bodyRows: 9 }, view: {} },
} as const
const TYPED = { origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 } } as const
const DEV = { tool: 'Agent', description: 'dev', prompt: 'build it', subagent_type: 'dev' } as const

// what the person answers in the grill dialog, and what the mod asked and pushed
type Person = { answer?: string; isAway?: boolean; questions: string[]; pushes: string[] }

function engine(
  on: On,
  agents: { id: string; type: string; name?: string }[] = [],
  person?: Person,
  stored: Readonly<Record<string, unknown>> = {},
): void {
  mock.store(on, stored)
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
    if (e.tool === 'Bash' && String(e.command).includes('gh pr create')) {
      return { result: { stdout: `${PR_URL}\n`, stderr: '', interrupted: false } }
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
    expect(taskKeyIn(`run the pipeline for ${K}-1834 please`)).toBe(`${K}-1834`)
  })

  test('a release note file is a doc path where the project runs release notes, and only the note', () => {
    const note = `${WT}${RELEASE_NOTES_DIR || '/acme_release_notes/notes/'}kio_12.html`
    expect(isDocPath(note)).toBe(RELEASE_NOTES_DIR !== '')
    expect(isDocPath(note.replace('/notes/', '/models/'))).toBe(false)
    expect(isDocPath(note.replace('.html', '.py'))).toBe(false)
  })

  test('a product code is no task key: task numbers carry no leading zero', () => {
    expect(taskKeysIn(`${K}-1791 ON HAND OF RAW-000010 PURGING COMPOUND, ASM-001640`)).toEqual([`${K}-1791`])
    expect(taskKeyIn('RAW-000010 Purging Compound')).toBe('')
    const run = addTasks(newRun(`${K}-1791`, 0), [`${K}-1791`], 0)
    expect(addTasks(run, ['CAP-000059 capacitor'], 1)).toBe(run)
  })

  test('a task key is the ticket prefix and a number: a month column, a standard, another project is none', () => {
    const prose = `${K}-1909 THREE TICKS (SCREENSHOT: MONTH COLUMNS JAN-2026.., ISO-9001, BP-33)`
    expect(taskKeysIn(prose)).toEqual(HAS_PREFIX ? [`${K}-1909`] : [`${K}-1909`, 'JAN-2026', 'ISO-9001', 'BP-33'])
  })

  test('a task of each project the prefixes name is a task key', () => {
    const other = TICKET_PREFIXES[1]
    if (other === undefined) return
    expect(taskKeysIn(`${K}-1909 and ${other}-21, not JAN-2026`)).toEqual([`${K}-1909`, `${other}-21`])
    expect(taskKeyIn(`${other.toLowerCase()}-21 admin lists`.toUpperCase())).toBe(`${other}-21`)
  })

  test('the PR guard reads the command the shell runs, not the text it carries', () => {
    const runs = [
      'gh pr create --fill',
      'cd /p/.worktrees/kio-9-x && gh pr create --fill',
      'GH_REPO=acme/addons gh pr create --fill',
      'url=$(gh pr create --fill) && echo "$url"',
      'echo "opened: $(gh pr create --fill)"',
      'echo `gh pr create --fill`',
      "bash -lc 'gh pr create --fill'",
      '/usr/bin/gh pr create --fill',
      'gh pr new --fill',
      'gh pr -R acme/addons create --fill',
      'timeout 60 gh pr create --fill 2>&1',
      'git push -u origin HEAD\ngh pr create --fill',
      `gh pr create --title "x" --body "$(cat <<'EOF'\nIt doesn't (quite) close it\nEOF\n)"`,
      "bash <<'EOF'\ngh pr create --fill\nEOF",
    ]
    const carries = [
      'kb add "the guard refused gh pr create in a quoted note" --scope kaertech-odoo-16',
      "kb append 996 --body-file - <<'EOF'\nthen gh pr create was refused\nEOF",
      "git commit -m 'gh pr create waits for QC'",
      'grep -n "gh pr create" SKILL.md',
      'echo gh pr create',
      'gh pr view 12 && gh pr list',
      '# gh pr create comes later\ngit status',
      `kb add "$(printf '%s' 'gh pr create')"`,
    ]
    expect(carries.filter(command => isPrCreate(command))).toEqual([])
    expect(runs.filter(command => !isPrCreate(command))).toEqual([])
  })

  test('a stray key a run was saved with leaves its block', () => {
    const stray: BlockTask = { key: 'JAN-2026', title: '', state: 'pending', at: 0, url: '' }
    const task: BlockTask = { key: `${K}-1909`, title: 'forecast', state: 'active', at: 1, url: '' }
    const kept = withoutStrayTasks({ ...newRun(`${K}-1909`, 0), tasks: [stray, task] })
    expect(kept.tasks?.map(each => each.key)).toEqual(HAS_PREFIX ? [`${K}-1909`] : ['JAN-2026', `${K}-1909`])
    const clean = addTasks(newRun(`${K}-1`, 0), [`${K}-1`], 0)
    expect(withoutStrayTasks(clean)).toBe(clean)
  })

  test('a block: its keys, titles and states', async () => {
    expect(taskKeysIn(`block ${K}-1844, ${K}-1845 and ${K}-1441, again ${K}-1844`)).toEqual([`${K}-1844`, `${K}-1845`, `${K}-1441`])
    let run = addTasks(newRun(`${K}-1`, 0), [`${K}-1`, `${K.toLowerCase()}-2 rename the field`, 'not a key'], 1)
    expect(run.tasks?.map(task => [task.key, task.title, task.state])).toEqual([
      [`${K}-1`, '', 'pending'],
      [`${K}-2`, 'rename the field', 'pending'],
    ])
    expect(addTasks(run, [`${K}-1`], 2)).toBe(run)
    run = moveTasks(moveTasks(run, [`${K}-1`, `${K}-3`], 'active', 3), [`${K}-1`], 'done', 4)
    expect(run.tasks?.map(task => task.state)).toEqual(['done', 'pending', 'active'])
    expect(blockLine(run)).toBe('1/3 done · 1 in work · 1 pending')
    expect(blockLine(newRun(`${K}-1`, 0))).toBe('')
  })

  test('a task link opens only a record in the production web client', async () => {
    const web = 'https://erp.example.com/web'
    const record = `${web}#id=33574&model=project.task&view_type=form`
    expect(taskUrlIn(`${K}-1 title ${record}`, web)).toBe(record)
    expect(taskUrlIn(`${K}-1 ${web}site#id=1`, web)).toBe('')
    expect(taskUrlIn(`${K}-1 https://evil.example.com/web#id=1`, web)).toBe('')
    expect(taskUrlIn(`${K}-1 ${record}`, '')).toBe('')
    const run = addTasks(newRun(`${K}-1`, 0), [`${K}-1 size the sub-assemblies ${record}`], 1, web)
    expect(run.tasks?.[0]).toMatchObject({ key: `${K}-1`, title: 'size the sub-assemblies', url: record })
  })

  test('phases and verdicts refuse what their gates do not allow', async () => {
    let run = newRun(`${K}-1`, 0)
    expect(typeof enterPhase(run, 2, '', 1)).toBe('string')
    expect(newRun(`${K}-1`, 0, false).status?.isOpen).toBe(true)
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

  test('a fix round is dev work after a review; the next task of a block counts afresh', () => {
    const open = openGate(openGate(newRun(`${K}-1`, 0), 'status', 'sent', 1), 'grill', 'confirmed', 1)
    const dev = enterPhase(open, 2, '', 2)
    if (typeof dev === 'string') throw new Error(dev)
    expect(isNewRound(dev)).toBe(false)
    let run = startRound({ ...dev, phase: 3, seq: 5, lastReview: 5 })
    expect([run.fixRounds, isNewRound(run), roundsLeft(run)]).toEqual([1, false, 2])
    run = { ...run, seq: 9, lastReview: 9 }
    expect(isNewRound(run)).toBe(true)
    expect(isNewRound({ ...run, phase: 4 })).toBe(false)
    const back = enterPhase(run, 2, '', 10)
    if (typeof back === 'string') throw new Error(back)
    expect(back.fixRounds).toBe(1)
    const next = enterPhase({ ...run, phase: 5 }, 2, `${K}-2`, 11)
    if (typeof next === 'string') throw new Error(next)
    expect([next.fixRounds, isNewRound(next)]).toEqual([0, false])
  })

  test("a child takes only gates the person opened in its mother; a run closes on another's pass or PR", () => {
    const mother = openGate(openGate(newRun(`${K}-9`, 0), 'status', 'sent', 1), 'grill', 'confirmed by you', 1)
    expect(isMotherRunFor(mother, `${K}-9`)).toBe(true)
    expect(isMotherRunFor(mother, `${K}-8`)).toBe(false)
    expect(isMotherRunFor(mother, '')).toBe(false)
    expect(isMotherRunFor({ ...mother, isDone: true }, `${K}-9`)).toBe(false)
    expect(isMotherRunFor(openGate(newRun(`${K}-9`, 0), 'status', 'sent', 1), `${K}-9`)).toBe(false)
    expect(isMotherRunFor(addTasks(mother, [`${K}-10`], 2), `${K}-10`)).toBe(true)

    const child = takeGates(newRun(`${K}-9`, 5), mother, 'sid-mother-1', 6)
    expect(closedForDev(child)).toEqual([])
    expect(child.grill).toEqual({ isOpen: true, how: `confirmed by you, taken from the ${K}-9 run of session sid-moth`, at: 6, from: 'sid-mother-1' })
    expect([child.mother, child.qc]).toEqual(['sid-mother-1', null])
    // a gate taken from another run is never passed on: a child is no mother
    expect(isMotherRunFor(child, `${K}-9`)).toBe(false)
    const own = takeGates(openGate(newRun(`${K}-9`, 5), 'grill', 'confirmed here', 5), mother, 'sid-mother-1', 6)
    expect([own.grill?.how, own.grill?.from, own.status?.from]).toEqual(['confirmed here', undefined, 'sid-mother-1'])
    expect(typeof enterPhase(child, 2, '', 7)).not.toBe('string')
    expect(String(enterPhase(child, 6, '', 7))).toContain('gh pr create is refused')

    // the mother closes on a run for its tasks that recorded a QC pass or a PR, never on a promise
    expect(String(enterPhase(mother, 7, '', 8))).toContain(`a run for ${K}-9 in another session recorded a QC pass or a PR`)
    expect(closesOn(child, [`${K}-9`])).toBe('')
    expect(closesOn({ ...child, pr: PR_URL }, [`${K}-9`])).toBe(`PR ${PR_URL}`)
    expect(closesOn({ ...child, pr: PR_URL }, [`${K}-8`])).toBe('')
    expect(closesOn(openGate(child, 'qc', 'pass: QC gate: PASS', 7), [`${K}-8`, `${K}-9`])).toBe('QC pass: QC gate: PASS')
    expect(closesOn({ ...child, qc: { isOpen: false, how: 'blocking: 2 criticals', at: 7 } }, [`${K}-9`])).toBe('')
    const closed = enterPhase(mother, 7, '', 9, `PR ${PR_URL} of the ${K}-9 run of session sid-chil`)
    if (typeof closed === 'string') throw new Error(closed)
    expect([closed.isDone, closed.closedOn]).toEqual([true, `PR ${PR_URL} of the ${K}-9 run of session sid-chil`])
    // another run's pass is no PR of this one: Phase 6 stays refused
    expect(typeof enterPhase(mother, 6, '', 9, 'x')).toBe('string')
    expect(runKeys(addTasks(mother, [`${K}-10`, `${K}-9`], 2))).toEqual([`${K}-9`, `${K}-10`])

    expect([isChildArgs(`child ${K}-9`), isChildArgs(` Child ${K}-9 ${K}-10`)]).toEqual([true, true])
    expect([isChildArgs(`${K}-9 child`), isChildArgs('children of the block'), isChildArgs('')]).toEqual([false, false, false])
  })
})

test('the dev agent and worktree edits wait for the Teams status and your grill press', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  // typed /pipeline starts the run: skill.prompt never reaches a person's plugin under cc-plugin-sec-default
  await $.prompt.submit({ text: `/pipeline ${K}-9`, command: { name: 'pipeline', args: `${K}-9` }, wait: false, origin: { kind: 'composer' } })
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain(`${K}-9`)

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
  await $.command.run({ command: 'pipeline-gate', args: `start ${K}-9`, ...TYPED })
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
  await $.command.run({ command: 'pipeline-gate', args: `start ${K}-9`, ...TYPED })
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
  await $.command.run({ command: 'pipeline-gate', args: `start ${K}-9`, ...TYPED })
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
  const status = await $.command.run({ command: 'pipeline-gate', args: 'status', ...TYPED })
  expect(status.text).toContain(`the pipeline run for ${K}-9 was ended by you at Phase 0`)
})

// the gates opened by hand and a QC pass, so the run can reach its PR
async function toPr($: Engine): Promise<void> {
  await $.command.run({ command: 'pipeline-gate', args: `start ${K}-9`, ...TYPED })
  for (const gate of ['status teams is down', 'grill agreed in chat', 'qc reviewed by hand']) {
    await $.command.run({ command: 'pipeline-gate', args: `open ${gate}`, ...TYPED })
  }
}

test('Phase 5 is the demo video, and a run that went past it shows it skipped', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await toPr($)
  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 6, task: `${K}-9` })
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await pane.find({ text: '5 Demo video (optional) · skipped' })).toBeDefined()
  expect(JSON.stringify(await pane.drawn())).not.toContain('GIF')
  await pane.unmount()
})

test('Phase 7 ends the run, and the gates and the pane say it ended there, with its PR', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await toPr($)
  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 6, task: `${K}-9` })
  expect((await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })).deny).toBeUndefined()
  await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', done: [`${K}-9`] })

  const closed = await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 7 })
  expect(String(closed.result)).toContain(`the pipeline run for ${K}-9 ended at Phase 7 Memory closure`)
  const gates = String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)
  expect(gates).toContain('ended at Phase 7')
  expect(gates).toContain(`PR: ${PR_URL}`)
  expect(gates).not.toContain('no pipeline run in this session')
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await pane.find({ text: 'ended at Phase 7' })).toBeDefined()
  expect((await pane.findAll({ type: 'Link' })).map(link => link.props.href)).toContain(PR_URL)
  await pane.unmount()
})

test('a tasks move with no run active opens none and changes nothing', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const none = await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', done: [`${K}-9`] })
  expect(String(none.result)).toContain('no pipeline run is active')
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain('no pipeline run in this session')

  // the KIO-1909 order: Phase 7 first, the block's tasks marked done after it
  await toPr($)
  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 7, task: `${K}-9` })
  const late = await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', done: [`${K}-9`] })
  expect(String(late.result)).toContain('ended at Phase 7')
  expect(String(late.result)).toContain('nothing changed')

  // a later call about the ended run, its own task or a phase, opens no run either: nothing is gated
  await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', add: [`${K}-9 forecast`] })
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 3 })).result)).toContain('ended at Phase 7')
  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await pane.find({ text: 'task not set' })).toBeUndefined()
  await pane.unmount()

  // a task the ended run did not have starts a new run, its gates closed
  await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', add: [`${K}-10 next`] })
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain(`pipeline ${K}-10 ·`)
  expect((await $.tool.call(DEV)).deny).toContain('grill alignment')
})

test('the PR-create words inside an argument are neither refused nor recorded as a block', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'pipeline-gate', args: `start ${K}-9`, ...TYPED })
  const note = 'kb add "the guard refused gh pr create in a quoted note" --scope kaertech-odoo-16'
  expect((await $.tool.call({ tool: 'Bash', command: note })).deny).toBeUndefined()
  const body = "kb append 996 --body-file - <<'EOF'\nthen gh pr create was refused\nEOF"
  expect((await $.tool.call({ tool: 'Bash', command: body })).deny).toBeUndefined()
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await pane.find({ text: 'Blocked' })).toBeUndefined()
  await pane.unmount()
  expect((await $.tool.call({ tool: 'Bash', command: 'cd /p && gh pr create --fill' })).deny).toContain('QC gate passes')
})

test('a pipeline for a task of another project the prefixes name takes its key', async ($, on) => {
  const other = TICKET_PREFIXES[1]
  if (other === undefined) return
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `${other}-21 AI admin lists (screenshots: month columns JAN-2026..)` })
  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 1, task: `${other}-21` })
  const gates = String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)
  expect(gates).toContain(`pipeline ${other}-21 ·`)
  expect(gates).not.toContain('tasks:')
})

test('a pipeline for another task starts over with its gates closed', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `${K}-1` })
  await $.command.run({ command: 'pipeline-gate', args: 'open status down', ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open grill agreed', ...TYPED })
  const dev = { tool: 'Agent', description: 'dev', prompt: 'p', subagent_type: 'dev' } as const
  expect((await $.tool.call(dev)).deny).toBeUndefined()

  // the same task again keeps the run; another task does not
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `${K}-1` })
  expect((await $.tool.call(dev)).deny).toBeUndefined()
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `${K}-2` })
  expect((await $.tool.call(dev)).deny).toContain('grill alignment')
})

// the Teams status sent, so the grill gate is the one left shut
async function toGrill($: Engine): Promise<void> {
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `${K}-9` })
  if (HAS_STATUS_GATE) await $.tool.call({ tool: 'mcp__m365__teams_send_chat_message', chatId: '19:status-chat', body: 'status' })
}

test('a step that waits on the grill gate pushes to the phone and asks; your press opens it', async ($, on) => {
  const person: Person = { answer: 'Confirm', questions: [], pushes: [] }
  engine(on, [], person)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `${K}-9` })
  if (HAS_STATUS_GATE) {
    // the Teams status is the agent's to send: no dialog while it is missing
    expect((await $.tool.call(DEV)).deny).toContain('Teams status')
    expect(person.questions).toEqual([])
    await $.tool.call({ tool: 'mcp__m365__teams_send_chat_message', chatId: '19:status-chat', body: 'status' })
  }

  const entered = await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 2, task: `${K}-9` })
  expect(String(entered.result)).toContain('phase 2 Development')
  expect(person.pushes).toEqual([`${K}-9: Phase 2 waits on your grill confirm`])
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

test('a product code named beside the task does not join the block', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const args = `${K}-1791 - Explain why Consumable products show no On Hand quantity (RAW-000010 Purging Compound)`
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args })
  const gates = String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)
  expect(gates).toContain(`pipeline ${K}-1791 ·`)
  expect(gates).not.toContain('tasks:')
})

test('a month column a screenshot description names is no task', async ($, on) => {
  if (!HAS_PREFIX) return
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const args = 'show invoiced quantities too (screenshots: XLSX Forecast 24M Rolling Report with month columns JAN-2026..)'
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args })
  // the order the pipeline takes: the task joins the block, then the phase names it
  await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', add: [`${K}-1909 forecast`], active: [`${K}-1909`] })
  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 1, task: `${K}-1909` })
  const gates = String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)
  expect(gates).toContain(`pipeline ${K}-1909 ·`)
  expect(gates).not.toContain('tasks:')
})

test('a run saved with a stray key in its block comes back without it', async ($, on) => {
  if (!HAS_PREFIX) return
  const stray: BlockTask = { key: 'JAN-2026', title: '', state: 'pending', at: 0, url: '' }
  const task: BlockTask = { key: `${K}-1909`, title: 'forecast', state: 'active', at: 1, url: '' }
  const next: BlockTask = { key: `${K}-1910`, title: 'wizard', state: 'pending', at: 1, url: '' }
  engine(on, [], undefined, { 'run:sid-1': { ...newRun(`${K}-1909`, 0), tasks: [stray, task, next] } })
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await pane.find({ text: `pipeline ${K}-1909 · block of 2` })).toBeDefined()
  expect(await pane.find({ text: 'JAN-2026' })).toBeUndefined()
})

test('a block is one run: its tasks keep the gates, and the pane and band show how far it got', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `block ${K}-1, ${K}-2 and ${K}-3` })
  await $.command.run({ command: 'pipeline-gate', args: 'open status down', ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open grill agreed', ...TYPED })

  // a task of the block is the same run; one outside it starts over
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 2, task: `${K}-2` })).result)).toContain('phase 2 Development')
  await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', add: [`${K}-4 rename the field`], active: [`${K}-1`, `${K}-2`] })
  const moved = await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', done: [`${K}-1`] })
  expect(String(moved.result)).toContain('tasks: 1/4 done · 1 in work · 2 pending')
  expect((await $.tool.call(DEV)).deny).toBeUndefined()

  for (const surface of ['desktop', 'mobile'] as const) {
    const pane = await $.ui.mount({ ...PANE, surface })
    expect(await pane.find({ text: '1/4 done · 1 in work · 2 pending' })).toBeDefined()
    expect(await pane.find({ text: 'rename the field' })).toBeDefined()
    await pane.unmount()
    const band = await $.ui.mount({ ...BAND, surface })
    expect(await band.find({ text: '1/4' })).toBeDefined()
    // no tip: nothing reveals on hover, nothing underlines
    expect(JSON.stringify(await band.drawn())).not.toMatch(/"hover"|"display":"none"/)
    await band.unmount()
  }

  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 2, task: `${K}-9` })
  expect((await $.tool.call(DEV)).deny).toContain('grill alignment')
})

test('a block task given its record URL gets an open link in the pane', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const record = `${ODOO_WEB_URL}#id=33574&model=project.task&view_type=form`
  await $.tool.call({ tool: 'mcp__pipeline-gates__tasks', add: [`${K}-1 first ${record}`, `${K}-2 second`] })
  for (const surface of ['desktop', 'mobile'] as const) {
    const pane = await $.ui.mount({ ...PANE, surface })
    const links = await pane.findAll({ type: 'Link' })
    // the record URL where one was given, else the key's route where the project has it
    const byKey = TASK_URL_BASE === '' ? [] : [`${TASK_URL_BASE}${K}-2`]
    expect(links.map(link => link.props.href)).toEqual([...(ODOO_WEB_URL === '' ? [] : [record]), ...byKey])
    expect(await pane.find({ text: 'second' })).toBeDefined()
    await pane.unmount()
  }
})

test('each fix round goes to a fresh dev agent, and a fourth waits for you', async ($, on) => {
  engine(on, [
    { id: 'a-review', type: 'review' },
    { id: 'a-dev', type: 'dev', name: 'kio-dev' },
  ])
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'pipeline-gate', args: `start ${K}-9`, ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open status teams is down', ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open grill agreed in chat', ...TYPED })
  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 2, task: `${K}-9` })
  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  const ask = { tool: 'SendMessage', to: 'kio-dev', message: 'one more thing' } as const
  expect((await $.tool.call(ask)).deny).toBeUndefined()

  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 3 })
  const reviewed = async (turnId: string) => {
    await $.turn.complete({ answer: 'review done', durationMs: 1, isAborted: false, turnId, agentId: 'a-review', reason: 'answer' })
  }
  await reviewed('r1')
  expect((await $.tool.call(ask)).deny).toContain('fresh dev agent')
  expect((await $.tool.call({ ...ask, to: 'a-dev [ref]' })).deny).toContain('fresh dev agent')
  expect((await $.tool.call({ tool: 'SendMessage', to: 'a-review', message: 'say more' })).deny).toBeUndefined()

  for (const round of ['r2', 'r3']) {
    expect((await $.tool.call(DEV)).deny).toBeUndefined()
    // a second dev agent for the same round is the same round
    expect((await $.tool.call(DEV)).deny).toBeUndefined()
    await reviewed(round)
  }
  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  await reviewed('r4')
  expect((await $.tool.call(DEV)).deny).toContain('fix round 4 is refused')

  const byPlugin = await $.command.run({
    command: 'pipeline-gate',
    args: 'open rounds because',
    origin: { kind: 'plugin', name: 'other' },
    presentation: TYPED.presentation,
  })
  expect(byPlugin.text).toContain('only the person')
  const granted = await $.command.run({ command: 'pipeline-gate', args: 'open rounds the design holds', ...TYPED })
  expect(granted.text).toContain('fix rounds: 3 of 6')
  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain('fix rounds: 4 of 6')
})

// the run of a mother session as the store holds it: the person sent the status and confirmed the grill
function motherRun(task: string, grill = true) {
  const sent = openGate({ ...newRun(task, 0), phase: 1 }, 'status', 'sent to 19:status…', 1)
  return grill ? openGate(sent, 'grill', 'confirmed by you', 2) : sent
}

// the run of a child session as the store holds it: its gates taken from its mother's run, at Phase 6
function childRun(task: string, pr = '') {
  const run = { ...takeGates(newRun(task, 3), motherRun(task), 'sid-mother', 4), phase: 6 }
  return pr === '' ? run : { ...run, pr }
}

// a mother at the end of Phase 1: the person opened the status and the grill there by hand
async function toHandOff($: Engine): Promise<void> {
  await $.command.run({ command: 'pipeline-gate', args: `start ${K}-9`, ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open status teams is down', ...TYPED })
  await $.command.run({ command: 'pipeline-gate', args: 'open grill agreed in chat', ...TYPED })
  await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 1, task: `${K}-9` })
}

test('a child session runs on the gates the person opened in its mother; its PR still waits for its own QC pass', async ($, on) => {
  engine(on, [], undefined, { 'run:sid-mother': motherRun(`${K}-9`) })
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `child ${K}-9` })

  expect((await $.tool.call(DEV)).deny).toBeUndefined()
  expect((await $.tool.call({ tool: 'Write', file_path: `${WT}/models/a.py`, content: 'x' })).deny).toBeUndefined()
  const entered = String((await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 2, task: `${K}-9` })).result)
  expect(entered).toContain(`grill alignment: confirmed by you, taken from the ${K}-9 run of session sid-moth`)
  expect(entered).toContain('dev allowed: yes · PR allowed: no')
  expect((await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })).deny).toContain('QC gate passes')
})

test("only a child takes a mother's gates, and only gates the person opened there for its task", async ($, on) => {
  engine(on, [], undefined, {
    'run:sid-mother': motherRun(`${K}-9`),
    'run:sid-shut': motherRun(`${K}-7`, false),
    'run:sid-child': childRun(`${K}-6`),
  })
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  const closed = HAS_STATUS_GATE ? 'Teams status and grill alignment gates are closed' : 'grill alignment gate is closed'

  // a second mother for the same task is no child: it runs its own status and grill
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `${K}-9` })
  expect((await $.tool.call(DEV)).deny).toContain(closed)
  // a child whose mother has no confirmed grill waits, as do one with no mother at all and one whose
  // only other run is a child that took its gates in turn
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `child ${K}-7` })
  expect((await $.tool.call(DEV)).deny).toContain('grill alignment')
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `child ${K}-8` })
  expect((await $.tool.call(DEV)).deny).toContain(closed)
  await $.tool.call({ tool: 'Skill', skill: 'pipeline', args: `child ${K}-6` })
  expect((await $.tool.call(DEV)).deny).toContain(closed)
})

test("a mother closes Phase 7 on its child's PR and names the run it closed on", async ($, on) => {
  engine(on, [], undefined, { 'run:sid-child': childRun(`${K}-9`, PR_URL) })
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await toHandOff($)
  // the child's PR is not the mother's: its own gh pr create stays behind the QC gate
  expect((await $.tool.call({ tool: 'Bash', command: 'gh pr create --fill' })).deny).toContain('QC gate passes')
  const closed = String((await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 7 })).result)
  expect(closed).toContain(`the pipeline run for ${K}-9 ended at Phase 7 Memory closure`)
  expect(closed).toContain(`closed on the PR ${PR_URL} of the ${K}-9 run of session sid-chil`)
})

test("without a QC pass or a PR for its tasks anywhere, the mother's Phase 7 waits", async ($, on) => {
  engine(on, [], undefined, { 'run:sid-child': childRun(`${K}-9`), 'run:sid-other': childRun(`${K}-8`, PR_URL) })
  await $.session.start({ cwd: '/p', surface: 'desktop', isInteractive: true })
  await toHandOff($)
  const refused = String((await $.tool.call({ tool: 'mcp__pipeline-gates__phase', phase: 7 })).result)
  expect(refused).toContain(`Phase 7 is refused until the QC gate passes in this run, or a run for ${K}-9 in another session recorded a QC pass or a PR`)
  expect(String((await $.tool.call({ tool: 'mcp__pipeline-gates__gates' })).result)).toContain('phase 1 Task reading')
})
