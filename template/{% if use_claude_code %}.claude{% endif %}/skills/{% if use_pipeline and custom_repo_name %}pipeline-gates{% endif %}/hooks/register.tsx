import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { Gate, GateName, Run } from '../types'
import {
  ASK_CONFIRM,
  ASK_HEADER,
  ASK_MECHANICAL,
  ASK_NOT_YET,
  GATE_LABELS,
  PHASES,
  askedNote,
  bump,
  closedForDev,
  devRefusal,
  enterPhase,
  filePathOf,
  grillQuestion,
  isActive,
  isAwayGrillAnswer,
  isDocPath,
  isOpen,
  isPrCreate,
  isWorktreeCode,
  mayAskGrill,
  newRun,
  openGate,
  prRefusal,
  qcEvidence,
  qcHolds,
  recordVerdict,
  taskKeyIn,
  testSummary,
  withBlock,
} from './gates'
import { HAS_STATUS_GATE } from './config'

const PANE = 'pipeline'
const RUNS_KEPT = 20
const GATES: readonly GateName[] = ['status', 'grill', 'qc']
// the person's own input: the prompt box, the desktop bridge, an SDK host; never a plugin or another agent
const PERSON_ORIGINS = new Set(['composer', 'bridge', 'sdk'])
const USAGE = 'usage: /pipeline-gate [status] | open <status|grill|qc> <reason> | start [TASK-KEY] | end'

const run = atom({ plugin: 'pipeline-gates', key: 'run' } as const, null)

// subagent id -> its type (dev, review, documenter, ...), from $.agent.list()
const agentTypes = new Map<string, string>()

async function agentType($: EngineInterface, agentId: string): Promise<string> {
  if (!agentTypes.has(agentId)) {
    for (const agent of await $.agent.list()) agentTypes.set(agent.id, agent.type)
  }
  return agentTypes.get(agentId) ?? ''
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
// halfway, never ended) must not lend its open gates to the next one.
async function start($: EngineInterface, task: string): Promise<void> {
  const current = await read($, run)
  if (isActive(current) && (task === '' || current.task === '' || task === current.task)) {
    if (task !== '' && current.task === '') await save($, { ...current, task })
    return
  }
  await save($, newRun(task, await $.clock.now(), HAS_STATUS_GATE))
  void $.ui.open({ id: PANE, title: 'pipeline' })
}

async function restore($: EngineInterface): Promise<void> {
  const saved = await $.store.get(`run:${await $.session.id()}`)
  if (saved !== undefined && saved !== null) await update($, run, () => saved as Run)
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

function describe(current: Run | null): string {
  if (!isActive(current)) return 'pipeline-gates: no pipeline run in this session.'
  const evidence = qcEvidence(current)
  return [
    `pipeline ${current.task || '(task not set)'} · phase ${current.phase} ${PHASES[current.phase]}`,
    ...GATES.map(name => {
      const gate = current[name]
      return `${GATE_LABELS[name]}: ${gate === null ? 'closed' : `${gate.how}${gate.isOpen ? '' : ' (closed)'}`}`
    }),
    `QC evidence since the last code change: review ${evidence.review ? 'yes' : 'no'}, green tests ${evidence.tests ? 'yes' : 'no'}${current.lastTests ? ` (last run: ${current.lastTests})` : ''}`,
    `dev allowed: ${closedForDev(current).length === 0 ? 'yes' : 'no'} · PR allowed: ${qcHolds(current) ? 'yes' : 'no'}`,
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
    // by hand the label is taken as typed: KIO-1834, or a name for a trial run
    await start($, name.toUpperCase().slice(0, 40))
    return describe(await read($, run))
  }
  if (verb === 'end') {
    if (!isActive(current)) return describe(current)
    await save($, { ...current, isDone: true })
    return 'pipeline-gates: the run is ended; nothing is gated any more.'
  }
  if (verb === 'open') {
    if (!isActive(current)) return describe(current)
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
        "Records that the pipeline enters a phase (0 memory bootstrap ... 7 memory closure), with the task key once known. Call it at the start of every phase. Refused for Phase 2 and later while the Teams status gate or the grill gate is closed, and for Phase 6 and later until the QC verdict passed; the refusal says what is missing. Starts the run when none is active.",
      inputSchema: {
        type: 'object',
        properties: {
          phase: { type: 'integer', minimum: 0, maximum: 7 },
          task: { type: 'string', description: 'The task key, KIO-1234' },
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
      name: 'gates',
      description: 'The current pipeline run: phase, the three gates, and what the QC gate still lacks.',
      inputSchema: { type: 'object', properties: {} },
    })
    await $.command.register({
      name: 'pipeline-gate',
      description: 'The pipeline gates: status, or open a gate by hand with a reason',
      argumentHint: '[status | open <status|grill|qc> <reason> | start [TASK] | end]',
    })
    await restore($)
    if (isActive(await read($, run))) void $.ui.open({ id: PANE, title: 'pipeline' })
    return started
  })

  on('skill.prompt', { skill: 'pipeline' }, async ($, e, next) => {
    await start($, '')
    return next(e)
  })

  on('tool.call', { tool: 'mcp__pipeline-gates__phase' }, async ($, e) => {
    const input = e as unknown as Record<string, unknown>
    const task = typeof input.task === 'string' ? taskKeyIn(input.task.toUpperCase()) : ''
    await start($, task)
    const current = await read($, run)
    if (!isActive(current)) return { result: describe(current) }
    const phase = Number(input.phase)
    let entered = enterPhase(current, phase, task, await $.clock.now())
    let asked = ''
    if (typeof entered === 'string' && phase >= 2 && mayAskGrill(current)) {
      const answer = await waitForGrill($, current, `Phase ${phase}`)
      const fresh = await read($, run)
      if (!isActive(fresh)) return { result: describe(fresh) }
      entered = enterPhase(fresh, phase, task, await $.clock.now())
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
    if (tool === 'Skill' && input.skill === 'pipeline') await start($, taskKeyIn(String(input.args ?? '')))

    const current = await read($, run)
    const path = filePathOf(tool, input)
    const command = tool === 'Bash' && typeof input.command === 'string' ? input.command : ''
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
      if (command !== '' && isPrCreate(command) && !qcHolds(current)) {
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
    const hint = { scope: 'pipeline-gates', underline: true }
    const mark = (gate: Gate | null, holds: boolean) => (holds && isOpen(gate) ? '✓' : '○')

    return (
      <Box flexDirection="column">
        {/* every band plugin draws its tips before the plugins beneath it and its rows after them: all tips sit on top */}
        <Box key="tip-pipeline-gates" display="none" hover={{ scope: 'pipeline-gates', display: 'flex' }} flexDirection="row" columnGap={1}>
          <Text color="cyan">ⓘ</Text>
          <Text dimColor>
            This session runs the pipeline: its phase and the gates the pipeline-gates mod enforces. Teams status and grill
            gate the dev agent and worktree edits, the QC verdict gates gh pr create. open = the pipeline pane.
          </Text>
        </Box>
        {below}
        <Box key="pipeline-row" flexDirection="row" columnGap={1} alignItems="center">
          <Text dimColor hover={hint}>
            pipeline
          </Text>
          <Text bold hover={hint}>
            {current.task || 'task?'}
          </Text>
          <Text hover={hint}>{`${current.phase} ${PHASES[current.phase]}`}</Text>
          <Text color={isOpen(current.status) ? 'green' : 'yellow'} hover={hint}>{`status ${mark(current.status, true)}`}</Text>
          <Text color={isOpen(current.grill) ? 'green' : 'yellow'} hover={hint}>{`grill ${mark(current.grill, true)}`}</Text>
          <Text color={qcHolds(current) ? 'green' : 'yellow'} hover={hint}>{`qc ${mark(current.qc, qcHolds(current))}`}</Text>
          <Button key="pipeline-open" label="open" onPress={() => $.ui.open({ id: PANE, title: 'pipeline' })} />
        </Box>
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const current = await read($, run)
    if (!isActive(current)) return <Text dimColor>No pipeline run in this session. /pipeline-gate start [TASK] starts one by hand.</Text>
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
          <Text bold>{`pipeline ${current.task || '(task not set yet)'}`}</Text>
          <Text dimColor>{`started ${ago(current.startedAt)} · dev ${closedForDev(current).length === 0 ? 'allowed' : 'gated'} · PR ${qcHolds(current) ? 'allowed' : 'gated'}`}</Text>
        </Box>
        {card(
          'pipeline-phases',
          <Box flexDirection="column">
            <Text bold>Phases</Text>
            {PHASES.map((name, i) => {
              const reached = (current.phaseAt[i] ?? 0) > 0
              // a phase passed over without being entered (the optional GIF) reads as skipped, not done
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
