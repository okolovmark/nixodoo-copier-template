import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { BlockTask, Gate, GateName, Run, TaskState } from '../types'
import {
  ASK_CONFIRM,
  ASK_HEADER,
  ASK_MECHANICAL,
  ASK_NOT_YET,
  GATE_LABELS,
  PHASES,
  RESUME_REFUSAL,
  addTasks,
  addressOf,
  askedNote,
  blockLine,
  bump,
  closedForDev,
  devRefusal,
  enterPhase,
  filePathOf,
  grantRounds,
  closesOn,
  grillQuestion,
  hasTask,
  isActive,
  isAwayGrillAnswer,
  isChildArgs,
  isDocPath,
  isMotherRunFor,
  isNewRound,
  isOpen,
  isPrCreate,
  isWorktreeCode,
  mayAskGrill,
  moveTasks,
  newRun,
  openGate,
  prRefusal,
  prUrlIn,
  qcEvidence,
  qcHolds,
  recordVerdict,
  roundsLeft,
  roundsLine,
  roundsRefusal,
  runKeys,
  runName,
  startRound,
  takeGates,
  taskCounts,
  taskKeyIn,
  taskKeysIn,
  tasksOf,
  testSummary,
  withBlock,
  withoutStrayTasks,
} from './gates'
import { HAS_STATUS_GATE, ODOO_WEB_URL, TASK_URL_BASE, TICKET_PREFIXES } from './config'

const PANE = 'pipeline'
const RUNS_KEPT = 20
const GATES: readonly GateName[] = ['status', 'grill', 'qc']
// the person's own input: the prompt box, the desktop bridge, an SDK host; never a plugin or another agent
const PERSON_ORIGINS = new Set(['composer', 'bridge', 'sdk'])
const USAGE = 'usage: /pipeline-gate [status] | open <status|grill|qc|rounds> <reason> | start [TASK-KEY ...] | end'
const TASK_STATES: readonly TaskState[] = ['pending', 'active', 'done']
// a task key as the model is shown one, in this project's first ticket prefix
const SAMPLE_KEY = `${TICKET_PREFIXES[0] ?? 'PROJ'}-1234`

const run = atom({ plugin: 'pipeline-gates', key: 'run' } as const, null)

// subagent id -> its type (dev, review, documenter, ...), from $.agent.list()
const agentTypes = new Map<string, string>()

async function agentType($: EngineInterface, agentId: string): Promise<string> {
  if (!agentTypes.has(agentId)) {
    for (const agent of await $.agent.list()) agentTypes.set(agent.id, agent.type)
  }
  return agentTypes.get(agentId) ?? ''
}

// the type of the agent SendMessage addresses, by its id or its name
async function addresseeType($: EngineInterface, to: unknown): Promise<string> {
  const address = addressOf(to)
  if (address === '') return ''
  const agent = (await $.agent.list()).find(each => each.id === address || each.name === address)
  return agent?.type ?? ''
}

async function save($: EngineInterface, next: Run | null): Promise<void> {
  await update($, run, () => next)
  await $.store.set(`run:${await $.session.id()}`, next)
}

async function change($: EngineInterface, apply: (current: Run) => Run): Promise<void> {
  const current = await read($, run)
  if (isActive(current)) await save($, apply(current))
}

// A run for another task is a new run with its gates closed again: an earlier run left open (stopped
// halfway, never ended) must not lend its open gates to the next one. A block is one run: a task of the
// block keeps it, and `block` (the keys a pipeline started with) joins it.
async function start($: EngineInterface, task: string, block: readonly string[] = []): Promise<void> {
  const current = await read($, run)
  const now = await $.clock.now()
  if (isActive(current) && (task === '' || current.task === '' || task === current.task || hasTask(current, task))) {
    const named = task !== '' && current.task === '' ? { ...current, task } : current
    const joined = addTasks(named, block, now, ODOO_WEB_URL)
    if (joined !== current) await save($, joined)
    return
  }
  await save($, addTasks(newRun(task, now, HAS_STATUS_GATE), block, now, ODOO_WEB_URL))
  void $.ui.open({ id: PANE, title: 'pipeline' })
}

// The runs of the other sessions, newest first: the store holds every session's run under run:<id>.
async function otherRuns($: EngineInterface): Promise<{ id: string; run: Run }[]> {
  const mine = `run:${await $.session.id()}`
  const found: { id: string; run: Run }[] = []
  for (const key of await $.store.keys()) {
    if (!key.startsWith('run:') || key === mine) continue
    const other = (await $.store.get(key)) as Run | null | undefined
    if (other !== null && other !== undefined) found.push({ id: key.slice('run:'.length), run: other })
  }
  return found.sort((a, b) => b.run.startedAt - a.run.startedAt)
}

// A child session takes the gates the person opened in its mother: the newest run of another session
// that works on `task` with the Teams status and the grill both opened there.
async function adoptMother($: EngineInterface, task: string): Promise<boolean> {
  const current = await read($, run)
  if (!isActive(current) || task === '' || closedForDev(current).length === 0) return false
  const mother = (await otherRuns($)).find(other => isMotherRunFor(other.run, task))
  if (mother === undefined) return false
  await save($, takeGates(current, mother.run, mother.id, await $.clock.now()))
  return true
}

// The pipeline skill loaded with `args`, typed as /pipeline or called through the Skill tool: its task
// keys start or join the run, and a child takes its mother's gates.
async function startPipeline($: EngineInterface, args: string): Promise<void> {
  const keys = taskKeysIn(args)
  await start($, keys[0] ?? '', keys)
  if (isChildArgs(args)) {
    for (const key of keys) if (await adoptMother($, key)) break
  }
}

// What a run with no QC pass of its own closes on at Phase 7: another session's run for its tasks that
// recorded a QC pass or a PR, named for the person; '' when there is none.
async function closureFor($: EngineInterface, current: Run): Promise<string> {
  const keys = runKeys(current)
  for (const other of await otherRuns($)) {
    const what = closesOn(other.run, keys)
    if (what !== '') return `${what} of ${runName(other.run, other.id)}`
  }
  return ''
}

async function restore($: EngineInterface): Promise<void> {
  const saved = await $.store.get(`run:${await $.session.id()}`)
  if (saved !== undefined && saved !== null) await update($, run, () => withoutStrayTasks(saved as Run))
  const keys = (await $.store.keys()).filter(key => key.startsWith('run:'))
  for (const key of keys.slice(0, Math.max(0, keys.length - RUNS_KEPT))) await $.store.delete(key)
}

// the whole output of a Bash call: inline, or the file a large one was persisted to
async function bashOutput($: EngineInterface, result: unknown, text: string | undefined): Promise<string> {
  const record = (result ?? {}) as { stdout?: string; stderr?: string; persistedOutputPath?: string }
  if (typeof record.persistedOutputPath === 'string') {
    try {
      return await $.fs.read(record.persistedOutputPath)
    } catch {
      // fall through to what the record holds inline
    }
  }
  return `${record.stdout ?? ''}\n${record.stderr ?? ''}\n${text ?? ''}`
}

async function confirmGrill($: EngineInterface, how: string): Promise<void> {
  const at = await $.clock.now()
  await change($, current => openGate(current, 'grill', how, at))
  $.ui.toast(`pipeline-gates: grill gate open (${how})`)
}

// a block task's link: the record URL it was given, else its key's /tasks/<key> where the project has one
function taskLink(task: BlockTask): string {
  if (task.url !== undefined && task.url !== '') return task.url
  return TASK_URL_BASE === '' ? '' : `${TASK_URL_BASE}${task.key}`
}

// one grill dialog at a time: a call blocked while it is open waits for the same answer
let asking: Promise<string> | null = null

// The grill gate waits on the person. They get a push (on the phone through Remote Control; the engine
// skips it while they are at the screen) and the engine's own question dialog, which every surface draws,
// the phone included. Only a press on Confirm or Mechanical opens the gate; Not yet, a dismissal, a dialog
// that resolved itself while they were away and text typed under Other leave it shut and mute the dialog
// until the person's next message.
async function askGrill($: EngineInterface, current: Run, what: string): Promise<string> {
  const task = current.task || 'pipeline'
  try {
    await $.tool.call({ tool: 'PushNotification', message: `${task}: ${what} waits on your grill confirm`, status: 'proactive' })
  } catch {
    // notifications off or nowhere to send them: the dialog still asks
  }
  let answer = ''
  try {
    const options = [ASK_CONFIRM, ASK_MECHANICAL, ASK_NOT_YET]
    answer = (await $.ui.ask(grillQuestion(task, what), { header: ASK_HEADER, options })).trim()
  } catch {
    // dismissed, or a run with no one to ask
  }
  if (answer === ASK_CONFIRM) await confirmGrill($, 'confirmed by you in the dialog')
  else if (answer === ASK_MECHANICAL) await confirmGrill($, 'mechanical task, grill skipped by you in the dialog')
  else await change($, r => ({ ...r, isAskMuted: true }))
  return answer
}

function waitForGrill($: EngineInterface, current: Run, what: string): Promise<string> {
  asking ??= askGrill($, current, what).finally(() => {
    asking = null
  })
  return asking
}

// how a run that is over ended: Phase 7 ends it, and so does the person's /pipeline-gate end
function endedLine(current: Run): string {
  const how = current.phase === PHASES.length - 1 ? 'ended at' : 'was ended by you at'
  return `the pipeline run for ${current.task || '(task not set)'} ${how} Phase ${current.phase} ${PHASES[current.phase]}`
}

// A call about a run that is over (its task, a task of its block, or none named) answers how it
// ended and never opens a fresh run with its gates closed again: a pipeline that marked its tasks
// done after Phase 7 got an empty run that gated what came next.
function isOfEnded(current: Run | null, keys: readonly string[]): current is Run {
  return current !== null && current.isDone && keys.every(key => key === current.task || hasTask(current, key))
}

// a tasks call with no run in progress changes nothing
function noRunAnswer(current: Run | null): string {
  if (current === null) return 'pipeline-gates: no pipeline run is active, so nothing changed; a tasks call starts a run only when `add` names a task.'
  const hint = current.phase === PHASES.length - 1 ? " Mark a block's tasks done before Phase 7, which ends the run." : ''
  return `pipeline-gates: ${endedLine(current)}, so nothing changed.${hint}`
}

function describe(current: Run | null): string {
  if (current === null) return 'pipeline-gates: no pipeline run in this session.'
  if (current.isDone) {
    return [
      `pipeline-gates: ${endedLine(current)}; nothing is gated any more.`,
      ...(blockLine(current) === '' ? [] : [`tasks: ${blockLine(current)}`]),
      `PR: ${current.pr ?? 'no gh pr create seen in this run'}`,
      ...((current.closedOn ?? '') === '' ? [] : [`closed on the ${current.closedOn}`]),
      'A new pipeline (the pipeline skill, Phase 0, or tasks with `add` naming another task) starts a new run.',
    ].join('\n')
  }
  const evidence = qcEvidence(current)
  return [
    `pipeline ${current.task || '(task not set)'} · phase ${current.phase} ${PHASES[current.phase]}`,
    ...(blockLine(current) === '' ? [] : [`tasks: ${blockLine(current)}`]),
    ...GATES.map(name => {
      const gate = current[name]
      return `${GATE_LABELS[name]}: ${gate === null ? 'closed' : `${gate.how}${gate.isOpen ? '' : ' (closed)'}`}`
    }),
    `QC evidence since the last code change: review ${evidence.review ? 'yes' : 'no'}, green tests ${evidence.tests ? 'yes' : 'no'}${current.lastTests ? ` (last run: ${current.lastTests})` : ''}`,
    `dev allowed: ${closedForDev(current).length === 0 ? 'yes' : 'no'} · PR allowed: ${qcHolds(current) ? 'yes' : 'no'}`,
    roundsLine(current),
  ].join('\n')
}

async function runCommand($: EngineInterface, args: string, isPerson: boolean): Promise<string> {
  const [verb = 'status', name = '', ...rest] = args.trim().split(/\s+/).filter(part => part !== '')
  const current = await read($, run)
  if (verb === 'status') return describe(current)
  // `end` and `open` let work past the gates; `start` may come from anyone, a new run's gates are closed
  if ((verb === 'end' || verb === 'open') && !isPerson) {
    return 'pipeline-gates: only the person at the keyboard ends a run or opens a gate by hand.'
  }
  if (verb === 'start') {
    // by hand the label is taken as typed: KIO-1834, a block of keys, or a name for a trial run
    const keys = taskKeysIn([name, ...rest].join(' ').toUpperCase())
    await start($, keys[0] ?? name.toUpperCase().slice(0, 40), keys)
    return describe(await read($, run))
  }
  if (verb === 'end') {
    if (!isActive(current)) return describe(current)
    await save($, { ...current, isDone: true })
    return 'pipeline-gates: the run is ended; nothing is gated any more.'
  }
  if (verb === 'open') {
    if (!isActive(current)) return describe(current)
    if (name === 'rounds' && rest.length > 0) {
      const granted = grantRounds(current)
      await save($, granted)
      return `pipeline-gates: three more fix rounds (${rest.join(' ')}); ${roundsLine(granted)}.`
    }
    if (!GATES.includes(name as GateName) || rest.length === 0) return USAGE
    await save($, openGate(current, name as GateName, `opened by you: ${rest.join(' ')}`, await $.clock.now()))
    return `pipeline-gates: the ${GATE_LABELS[name as GateName]} gate is open (${rest.join(' ')}).`
  }
  return USAGE
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.tool.register({
      name: 'phase',
      description:
        "Records that the pipeline enters a phase (0 memory bootstrap ... 7 memory closure), with the task key once known. Call it at the start of every phase. Refused for Phase 2 and later while the Teams status gate or the grill gate is closed, and for Phase 6 and later until the QC verdict passed; the refusal says what is missing. A child session (the pipeline skill loaded as `child <TASK-KEYS>`) runs on the status and grill gates the person opened in its mother's run for the task, each naming that run; its QC gate stays its own. A run without a QC pass of its own (the mother's) enters Phase 7 once a run for its task in another session recorded a QC pass or a PR. Starts the run when none is active.",
      inputSchema: {
        type: 'object',
        properties: {
          phase: { type: 'integer', minimum: 0, maximum: 7 },
          task: { type: 'string', description: `The task key, ${SAMPLE_KEY}` },
        },
        required: ['phase'],
      },
    })
    await $.tool.register({
      name: 'qc_verdict',
      description:
        "Records the Phase-3 QC gate decision. A pass is accepted only when a review agent finished and a green Odoo test run (more than 0 tests) happened after the last code change in the worktree; otherwise it is refused with what is missing. Until a pass stands, gh pr create is refused; a later code change voids it.",
      inputSchema: {
        type: 'object',
        properties: {
          verdict: { type: 'string', enum: ['pass', 'blocking'] },
          summary: { type: 'string', description: 'The gate report line' },
        },
        required: ['verdict', 'summary'],
      },
    })
    await $.tool.register({
      name: 'tasks',
      description:
        `The block of tasks this pipeline run works through, and where each stands: the user watches it as progress in the pipeline pane and band. A block is one run (one Teams status, one grill alignment), so a task of the block never resets the gates. \`add\` the block's tasks, each "${SAMPLE_KEY} short title <its _url from the Odoo connector>" (the URL gives the task an open button in the pane; a pipeline started with several keys has the keys already, add again to give titles and URLs). Then move them as the work goes: \`active\` when you start on a task, \`done\` when it is finished (its part is in the PR, or it closed without code), \`pending\` to put one back. Tasks that share one PR are yours to mark.`,
      inputSchema: {
        type: 'object',
        properties: {
          add: { type: 'array', items: { type: 'string' }, description: `Tasks of the block: "${SAMPLE_KEY} short title https://…/web#id=…" (title and URL optional)` },
          active: { type: 'array', items: { type: 'string' }, description: 'Task keys now in work' },
          done: { type: 'array', items: { type: 'string' }, description: 'Task keys finished' },
          pending: { type: 'array', items: { type: 'string' }, description: 'Task keys put back to pending' },
        },
      },
    })
    await $.tool.register({
      name: 'gates',
      description: 'The current pipeline run: phase, the three gates, and what the QC gate still lacks.',
      inputSchema: { type: 'object', properties: {} },
    })
    await $.command.register({
      name: 'pipeline-gate',
      description: 'The pipeline gates: status, or open a gate by hand with a reason',
      argumentHint: '[status | open <status|grill|qc> <reason> | start [TASK ...] | end]',
    })
    await restore($)
    if (isActive(await read($, run))) void $.ui.open({ id: PANE, title: 'pipeline' })
    return started
  })

  // /pipeline typed; not at skill.prompt, which Claude Code's built-in cc-plugin-sec-default (outermost
  // for Team and Enterprise organizations) sends past every plugin a person installs
  on('prompt.submit', { command: { name: 'pipeline' } }, async ($, e, next) => {
    await startPipeline($, e.command?.args ?? '')
    return next(e)
  })

  on('tool.call', { tool: 'mcp__pipeline-gates__phase' }, async ($, e) => {
    const input = e as unknown as Record<string, unknown>
    const task = typeof input.task === 'string' ? taskKeyIn(input.task.toUpperCase()) : ''
    const phase = Number(input.phase)
    // Phase 0 starts a pipeline; a later phase of the run that is over starts none
    const before = await read($, run)
    if (phase !== 0 && isOfEnded(before, task === '' ? [] : [task])) return { result: describe(before) }
    await start($, task)
    const current = await read($, run)
    if (!isActive(current)) return { result: describe(current) }
    // a mother's closure rests on its child's run: the QC pass or the PR it recorded
    const closure = phase === PHASES.length - 1 && !qcHolds(current) ? await closureFor($, current) : ''
    let entered = enterPhase(current, phase, task, await $.clock.now(), closure)
    let asked = ''
    if (typeof entered === 'string' && phase >= 2 && mayAskGrill(current)) {
      const answer = await waitForGrill($, current, `Phase ${phase}`)
      const fresh = await read($, run)
      if (!isActive(fresh)) return { result: describe(fresh) }
      entered = enterPhase(fresh, phase, task, await $.clock.now(), closure)
      if (typeof entered === 'string' && !isOpen(fresh.grill)) asked = askedNote(answer)
    }
    if (typeof entered === 'string') {
      const latest = await read($, run)
      if (isActive(latest)) await save($, withBlock(latest, `Phase ${String(input.phase)}`, await $.clock.now()))
      return { result: asked === '' ? entered : `${entered} ${asked}` }
    }
    await save($, entered)
    return { result: describe(entered) }
  })

  on('tool.call', { tool: 'mcp__pipeline-gates__qc_verdict' }, async ($, e) => {
    const input = e as unknown as Record<string, unknown>
    const current = await read($, run)
    if (!isActive(current)) return { result: describe(current) }
    const summary = typeof input.summary === 'string' ? input.summary.slice(0, 300) : ''
    const recorded = recordVerdict(current, String(input.verdict), summary, await $.clock.now())
    if (typeof recorded === 'string') return { result: recorded }
    await save($, recorded)
    return { result: describe(recorded) }
  })

  on('tool.call', { tool: 'mcp__pipeline-gates__gates' }, async $ => ({ result: describe(await read($, run)) }))

  on('tool.call', { tool: 'mcp__pipeline-gates__tasks' }, async ($, e) => {
    const input = e as unknown as Record<string, unknown>
    const list = (value: unknown) => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [])
    const added = list(input.add)
    const now = await $.clock.now()
    const current = await read($, run)
    // added to the run in progress, never a reason to start over; with none, a run starts only for a
    // task `add` names that the run just over did not have, and a move alone changes nothing
    if (isActive(current)) await change($, r => addTasks(r, added, now, ODOO_WEB_URL))
    else {
      const named = added.map(item => taskKeyIn(item.toUpperCase())).filter(key => key !== '')
      if (named.length === 0 || isOfEnded(current, named)) return { result: noRunAnswer(current) }
      await start($, named[0] ?? '', added)
    }
    for (const state of TASK_STATES) {
      const keys = list(input[state])
      if (keys.length > 0) await change($, r => moveTasks(r, keys, state, now))
    }
    return { result: describe(await read($, run)) }
  })

  // a grill dialog that resolved itself while the person was away carries no press: its answer is dropped
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    const ran = await next(e)
    if (!isAwayGrillAnswer(e.questions, ran.result)) return ran
    const result = { ...(ran.result as object), answers: {} }
    return ran.context === undefined ? { result } : { result, context: ran.context }
  })

  // the person's own message lets the grill dialog ask again
  on('prompt.submit', async ($, e, next) => {
    if (PERSON_ORIGINS.has(e.origin.kind) && (await read($, run))?.isAskMuted === true) {
      await change($, r => ({ ...r, isAskMuted: false }))
    }
    return next(e)
  })

  // the surface already heads a command's output with the plugin's name
  on('command.run', { command: 'pipeline-gate' }, async ($, e) => ({
    text: (await runCommand($, e.args, PERSON_ORIGINS.has(e.origin.kind))).replace(/^pipeline-gates: /, ''),
  }))

  // the gates themselves, and the facts the QC gate rests on
  on('tool.call', async ($, e, next) => {
    const tool = String(e.tool)
    const input = e as unknown as Record<string, unknown>
    if (tool === 'Skill' && input.skill === 'pipeline') await startPipeline($, String(input.args ?? ''))

    const current = await read($, run)
    const path = filePathOf(tool, input)
    const command = tool === 'Bash' && typeof input.command === 'string' ? input.command : ''
    const isPr = command !== '' && isPrCreate(command)
    if (isActive(current)) {
      const isDev = tool === 'Agent' && input.subagent_type === 'dev'
      const isWorktreeEdit = path !== null && isWorktreeCode(path)
      if ((isDev || isWorktreeEdit) && closedForDev(current).length > 0) {
        const what = isDev ? 'spawning the dev agent' : `editing ${path}`
        const answer = mayAskGrill(current) ? await waitForGrill($, current, what) : null
        const latest = await read($, run)
        if (isActive(latest) && closedForDev(latest).length > 0) {
          await save($, withBlock(latest, what, await $.clock.now()))
          $.ui.toast(`pipeline-gates: ${what} blocked`)
          return { deny: devRefusal(latest, what, answer === null || isOpen(latest.grill) ? '' : askedNote(answer)) }
        }
      }
      // a fix round: a fresh dev agent, and no more than the rounds the person allows
      if (isNewRound(current) && closedForDev(current).length === 0) {
        if (tool === 'SendMessage' && (await addresseeType($, input.to)) === 'dev') {
          await save($, withBlock(current, 'a fix round sent into an old dev agent', await $.clock.now()))
          $.ui.toast('pipeline-gates: fix round into an old dev agent blocked')
          return { deny: RESUME_REFUSAL }
        }
        if (isDev) {
          if (roundsLeft(current) <= 0) {
            await save($, withBlock(current, `fix round ${(current.fixRounds ?? 0) + 1}`, await $.clock.now()))
            $.ui.toast('pipeline-gates: fix round blocked, the user decides')
            return { deny: roundsRefusal(current) }
          }
          await change($, r => (isNewRound(r) ? startRound(r) : r))
        }
      }
      if (isPr && !qcHolds(current)) {
        await save($, withBlock(current, 'gh pr create', await $.clock.now()))
        $.ui.toast('pipeline-gates: gh pr create blocked')
        return { deny: prRefusal(current) }
      }
    }

    const ran = await next(e)
    if (!isActive(await read($, run)) || ran.deny !== undefined || ran.isError === true) return ran

    const agentId = (e as { agentId?: string }).agentId
    if (path !== null && isWorktreeCode(path) && !isDocPath(path)) {
      const isDocumenter = agentId !== undefined && (await agentType($, agentId)) === 'documenter'
      if (!isDocumenter) {
        await change($, r => {
          const [next2, seq] = bump(r)
          return { ...next2, lastChange: seq }
        })
      }
    }
    if (command.includes('--test-enable')) {
      const summary = testSummary(await bashOutput($, ran.result, ran.text))
      if (summary !== null) {
        await change($, r => {
          const [next2, seq] = bump(r)
          return { ...next2, lastTests: summary.text, lastGreen: summary.isGreen ? seq : r.lastGreen }
        })
      }
    }
    if (isPr) {
      const url = prUrlIn(await bashOutput($, ran.result, ran.text))
      if (url !== '') await change($, r => ({ ...r, pr: url }))
    }
    if (tool.endsWith('__teams_send_chat_message')) {
      const chat = typeof input.chatId === 'string' ? input.chatId : 'a chat'
      const at = await $.clock.now()
      await change($, r => (isOpen(r.status) ? r : openGate(r, 'status', `sent to ${chat.slice(0, 16)}…`, at)))
    }
    return ran
  })

  // a subagent's end: dev counts as a code change, review as the review the QC gate needs
  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId !== undefined && !e.isAborted && isActive(await read($, run))) {
      const type = await agentType($, e.agentId)
      if (type === 'dev' || type === 'review') {
        await change($, r => {
          const [next2, seq] = bump(r)
          return type === 'dev' ? { ...next2, lastChange: seq } : { ...next2, lastReview: seq }
        })
      }
    }
    return result
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const below = await next(e)
    const current = await read($, run)
    if (!isActive(current) || e.props.hasSurvey) return below
    const { Box, Button, Text } = $.ui.resolve(e)
    const counts = taskCounts(current)
    const mark = (gate: Gate | null, holds: boolean) => (holds && isOpen(gate) ? '✓' : '○')

    return (
      <Box flexDirection="column">
        {below}
        <Box key="pipeline-row" flexDirection="row" columnGap={1} alignItems="center">
          <Text dimColor>pipeline</Text>
          <Text bold>{current.task || 'task?'}</Text>
          {counts.total > 1 && <Text color={counts.done === counts.total ? 'green' : 'cyan'}>{`${counts.done}/${counts.total}`}</Text>}
          <Text>{`${current.phase} ${PHASES[current.phase]}`}</Text>
          <Text color={isOpen(current.status) ? 'green' : 'yellow'}>{`status ${mark(current.status, true)}`}</Text>
          <Text color={isOpen(current.grill) ? 'green' : 'yellow'}>{`grill ${mark(current.grill, true)}`}</Text>
          <Text color={qcHolds(current) ? 'green' : 'yellow'}>{`qc ${mark(current.qc, qcHolds(current))}`}</Text>
          <Button key="pipeline-open" label="open" onPress={() => $.ui.open({ id: PANE, title: 'pipeline' })} />
        </Box>
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Link, Text } = $.ui.resolve(e)
    const current = await read($, run)
    if (current === null) return <Text dimColor>No pipeline run in this session. /pipeline-gate start [TASK] starts one by hand.</Text>
    if (current.isDone) {
      const ended = endedLine(current)
      return (
        <Box flexDirection="column" rowGap={1}>
          <Text key="pipeline-ended" bold>{`${ended.charAt(0).toUpperCase()}${ended.slice(1)}.`}</Text>
          {blockLine(current) !== '' && <Text key="pipeline-ended-tasks" dimColor>{`tasks: ${blockLine(current)}`}</Text>}
          {current.pr !== undefined && (
            <Box key="pipeline-ended-pr" flexDirection="row" columnGap={1}>
              <Text dimColor>PR</Text>
              <Link href={current.pr} label={current.pr} />
            </Box>
          )}
          <Text key="pipeline-ended-next" dimColor>Nothing is gated. The next pipeline starts a new run; /pipeline-gate start [TASK] starts one by hand.</Text>
        </Box>
      )
    }
    const now = await $.clock.now()
    const ago = (at: number) => {
      const minutes = Math.max(0, Math.round((now - at) / 60_000))
      return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes}m ago` : `${Math.floor(minutes / 60)}h${minutes % 60}m ago`
    }
    const hasCards = e.surface !== 'terminal'
    const card = (key: string, children: RenderChildren) => (
      <Box key={key} flexDirection="column" rowGap={hasCards ? 1 : 0} borderStyle={hasCards ? 'round' : undefined} borderDimColor={hasCards ? true : undefined}>
        {children}
      </Box>
    )
    const evidence = qcEvidence(current)
    const tasks = tasksOf(current)
    const counts = taskCounts(current)
    const taskRow = (task: BlockTask) => (
      <Box key={`task-${task.key}`} flexDirection="row" columnGap={1}>
        <Text color={task.state === 'done' ? 'green' : task.state === 'active' ? 'cyan' : undefined} dimColor={task.state === 'pending'}>
          {task.state === 'done' ? '✓' : task.state === 'active' ? '●' : '○'}
        </Text>
        <Text bold={task.state === 'active'} dimColor={task.state === 'pending'}>
          {task.key}
        </Text>
        {task.title !== '' && <Text dimColor>{task.title}</Text>}
        {task.state !== 'pending' && <Text dimColor>{`${task.state === 'done' ? 'done' : 'in work'} ${ago(task.at)}`}</Text>}
        {taskLink(task) !== '' && <Link href={taskLink(task)} label="open in Odoo" />}
      </Box>
    )
    const gateRow = (name: GateName, holds: boolean, extra: RenderChildren) => {
      const gate = current[name]
      const isUp = holds && isOpen(gate)
      return (
        <Box key={`gate-${name}`} flexDirection="column">
          <Box flexDirection="row" columnGap={1}>
            <Text color={isUp ? 'green' : 'yellow'}>{isUp ? '✓' : '○'}</Text>
            <Text bold>{GATE_LABELS[name]}</Text>
            <Text dimColor>{gate === null ? 'closed' : `${gate.how} · ${ago(gate.at)}`}</Text>
          </Box>
          {extra}
        </Box>
      )
    }

    return (
      <Box flexDirection="column" rowGap={1}>
        <Box key="pipeline-head" flexDirection="column">
          <Text bold>{`pipeline ${current.task || '(task not set yet)'}${tasks.length > 1 ? ` · block of ${tasks.length}` : ''}`}</Text>
          <Text dimColor>{`started ${ago(current.startedAt)} · dev ${closedForDev(current).length === 0 ? 'allowed' : 'gated'} · PR ${qcHolds(current) ? 'allowed' : 'gated'}`}</Text>
        </Box>
        {tasks.length > 1 &&
          card(
            'pipeline-tasks',
            <Box flexDirection="column">
              <Text bold>Tasks</Text>
              <Box flexDirection="row">
                <Text color="green">{'■'.repeat(counts.done)}</Text>
                <Text color="cyan">{'■'.repeat(counts.active)}</Text>
                <Text dimColor>{'□'.repeat(counts.pending)}</Text>
              </Box>
              <Text dimColor>{blockLine(current)}</Text>
              {tasks.map(task => taskRow(task))}
            </Box>,
          )}
        {card(
          'pipeline-phases',
          <Box flexDirection="column">
            <Text bold>Phases</Text>
            {PHASES.map((name, i) => {
              const reached = (current.phaseAt[i] ?? 0) > 0
              // a phase passed over without being entered (the optional demo video) reads as skipped, not done
              const isSkipped = i < current.phase && !reached
              return (
                <Box flexDirection="row" columnGap={1}>
                  <Text
                    color={i < current.phase && !isSkipped ? 'green' : i === current.phase ? 'cyan' : undefined}
                    dimColor={i > current.phase || isSkipped}
                  >
                    {isSkipped ? '–' : i < current.phase ? '✓' : i === current.phase ? '●' : '○'}
                  </Text>
                  <Text bold={i === current.phase} dimColor={i > current.phase || isSkipped}>
                    {`${i} ${name}${isSkipped ? ' · skipped' : ''}`}
                  </Text>
                  {reached && <Text dimColor>{ago(current.phaseAt[i] ?? 0)}</Text>}
                </Box>
              )
            })}
          </Box>,
        )}
        {card(
          'pipeline-gates',
          <Box flexDirection="column" rowGap={1}>
            <Text bold>Gates</Text>
            {gateRow('status', true, null)}
            {gateRow(
              'grill',
              true,
              !isOpen(current.grill) && (
                <Box flexDirection="row" columnGap={1} paddingLeft={2} alignItems="center">
                  <Text dimColor>after you agree with the Alignment Summary:</Text>
                  <Button key="grill-confirm" label="confirm" onPress={() => confirmGrill($, 'confirmed by you')} />
                  <Button key="grill-mechanical" label="mechanical" onPress={() => confirmGrill($, 'mechanical task, grill skipped by you')} />
                </Box>
              ),
            )}
            {gateRow(
              'qc',
              qcHolds(current),
              <Box flexDirection="column" paddingLeft={2}>
                <Text dimColor>{`since the last code change: review ${evidence.review ? '✓' : '○'} · green tests ${evidence.tests ? '✓' : '○'}`}</Text>
                {current.lastTests !== '' && <Text dimColor>{`last test run: ${current.lastTests}`}</Text>}
                <Text dimColor>{roundsLine(current)}</Text>
              </Box>,
            )}
          </Box>,
        )}
        {current.blocked.length > 0 &&
          card(
            'pipeline-blocked',
            <Box flexDirection="column">
              <Text bold>Blocked</Text>
              {current.blocked.map(block => (
                <Text dimColor>{`${ago(block.at)}  ${block.what}`}</Text>
              ))}
            </Box>,
          )}
      </Box>
    )
  })
}
