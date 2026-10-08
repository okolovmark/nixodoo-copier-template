import type { Gate, GateName, Run } from '../types'

export const PHASES = [
  'Memory bootstrap',
  'Task reading',
  'Development',
  'QC gate',
  'Documentation',
  'Demo GIF (optional)',
  'Pull request',
  'Memory closure',
] as const

export const GATE_LABELS: Record<GateName, string> = {
  status: 'Teams status',
  grill: 'grill alignment',
  qc: 'QC verdict',
}

// the grill dialog: a press on its first two options opens the gate, as the pane's buttons do
export const ASK_CONFIRM = 'Confirm'
export const ASK_MECHANICAL = 'Mechanical'
export const ASK_NOT_YET = 'Not yet'
export const ASK_HEADER = 'Grill gate'
const ASK_TAIL = 'waits on the grill gate. Do you agree with the Alignment Summary?'

const FILE_TOOLS = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit'])
const WORKTREE = /\/\.worktrees\/(?!_env\/)[A-Za-z0-9][A-Za-z0-9._-]*\//
// what the documenter writes: never a code change, so it never voids a QC pass
const DOC_PATH = /\/readme\/|\/README\.(?:rst|md)$|\/static\/description\/|\.puml$/
const TEST_SUMMARY = /(\d+) failed, (\d+) error\(s\) of (\d+) tests/g
const PR_CREATE = /\bgh\s+pr\s+create\b/
const TASK_KEY = /\b[A-Z][A-Z0-9]{1,9}-\d+\b/

export function newRun(task: string, now: number, hasStatusGate = true): Run {
  return {
    task,
    startedAt: now,
    phase: 0,
    phaseAt: PHASES.map((_, i) => (i === 0 ? now : 0)),
    status: hasStatusGate ? null : { isOpen: true, how: 'no Teams status flow in this project', at: now },
    grill: null,
    qc: null,
    qcSeq: 0,
    seq: 0,
    lastChange: 0,
    lastReview: 0,
    lastGreen: 0,
    lastTests: '',
    blocked: [],
    isAskMuted: false,
    isDone: false,
  }
}

export function isActive(run: Run | null): run is Run {
  return run !== null && !run.isDone
}

export function filePathOf(tool: string, input: Record<string, unknown>): string | null {
  if (!FILE_TOOLS.has(tool)) return null
  const path = typeof input.file_path === 'string' ? input.file_path : input.notebook_path
  return typeof path === 'string' ? path : null
}

export function isWorktreeCode(path: string): boolean {
  return WORKTREE.test(path)
}

export function isDocPath(path: string): boolean {
  return DOC_PATH.test(path)
}

export function isPrCreate(command: string): boolean {
  return PR_CREATE.test(command)
}

export function taskKeyIn(text: string): string {
  return TASK_KEY.exec(text)?.[0] ?? ''
}

// Every `N failed, M error(s) of K tests` line of an Odoo run: green when all are clean and K adds up
// to more than zero (a bare --test-tags name runs 0 tests and only looks green).
export function testSummary(output: string): { isGreen: boolean; text: string } | null {
  const lines = [...output.matchAll(TEST_SUMMARY)]
  if (lines.length === 0) return null
  const failed = lines.reduce((sum, m) => sum + Number(m[1]), 0)
  const errors = lines.reduce((sum, m) => sum + Number(m[2]), 0)
  const tests = lines.reduce((sum, m) => sum + Number(m[3]), 0)
  return { isGreen: failed === 0 && errors === 0 && tests > 0, text: `${failed} failed, ${errors} error(s) of ${tests} tests` }
}

export function isOpen(gate: Gate | null): boolean {
  return gate?.isOpen === true
}

export function closedForDev(run: Run): GateName[] {
  return (['status', 'grill'] as const).filter(name => !isOpen(run[name]))
}

export function qcHolds(run: Run): boolean {
  return isOpen(run.qc) && run.qcSeq > run.lastChange
}

// what a QC pass rests on, since the last code change
export function qcEvidence(run: Run): { review: boolean; tests: boolean } {
  return { review: run.lastReview > run.lastChange, tests: run.lastGreen > run.lastChange }
}

export function bump(run: Run): [Run, number] {
  const seq = run.seq + 1
  return [{ ...run, seq }, seq]
}

export function withBlock(run: Run, what: string, at: number): Run {
  return { ...run, blocked: [{ what, at }, ...run.blocked].slice(0, 5) }
}

export function devRefusal(run: Run, what: string, asked = ''): string {
  const names = closedForDev(run).map(name => GATE_LABELS[name])
  const closed = `${names.join(' and ')} ${names.length > 1 ? 'gates are' : 'gate is'}`
  const how = [
    !isOpen(run.status) ? 'the status opens on a successful Teams send (my-status send)' : '',
    !isOpen(run.grill)
      ? 'the grill gate opens only when the user presses confirm or mechanical, in the pipeline pane or in the dialog the mod opens when a step waits on it'
      : '',
  ].filter(part => part !== '')
  const refusal = `pipeline-gates: ${what} is refused, the ${closed} closed: ${how.join('; ')}. A user who decides to go on anyway types /pipeline-gate open <gate> <reason>.`
  return asked === '' ? refusal : `${refusal} ${asked}`
}

// The dialog asks only when the grill gate is the one still shut for dev, and once per message of the
// person's: after a "not yet" or a dialog nobody answered, a retry is refused without asking again.
export function mayAskGrill(run: Run): boolean {
  const closed = closedForDev(run)
  return closed.length === 1 && closed[0] === 'grill' && run.isAskMuted !== true
}

export function grillQuestion(task: string, what: string): string {
  return `${task || 'pipeline'}: ${what} ${ASK_TAIL}`
}

// An AskUserQuestion that resolved itself while the person was away (afkTimeoutMs) carries no press of
// theirs: when it is the grill dialog, its answers are dropped.
export function isAwayGrillAnswer(questions: readonly { header?: string; question: string }[], result: unknown): boolean {
  const isGrill = questions.some(asked => asked.header === ASK_HEADER && asked.question.endsWith(ASK_TAIL))
  return isGrill && (result as { afkTimeoutMs?: unknown } | null)?.afkTimeoutMs !== undefined
}

// what the refusal tells the model after a dialog that did not open the gate
export function askedNote(answer: string): string {
  if (answer === ASK_NOT_YET) return 'The user answered Not yet in the grill dialog: stop and wait for them.'
  if (answer === '') {
    return 'Nobody answered the grill dialog (dismissed, or it closed while the user was away): tell the user the pipeline waits on their confirm, and stop.'
  }
  return `Instead of choosing, the user wrote: "${answer.slice(0, 500)}". That does not open the gate: answer them, then call again.`
}

// what a QC pass still lacks since the last code change
export function qcMissing(run: Run): string[] {
  const evidence = qcEvidence(run)
  return [
    !evidence.review ? 'a review agent that finished after the last code change' : '',
    !evidence.tests ? 'a green Odoo test run (more than 0 tests) after the last code change' : '',
  ].filter(part => part !== '')
}

export function prRefusal(run: Run): string {
  const missing = [...qcMissing(run), 'a pass recorded with mcp__pipeline-gates__qc_verdict']
  return `pipeline-gates: gh pr create is refused until the QC gate passes; missing: ${missing.join(', ')}.`
}

// The QC verdict the pipeline records: a pass is taken only on its evidence.
export function recordVerdict(run: Run, verdict: string, summary: string, now: number): Run | string {
  if (verdict === 'blocking') {
    const [next, seq] = bump(run)
    return { ...next, qc: { isOpen: false, how: `blocking: ${summary}`, at: now }, qcSeq: seq }
  }
  if (verdict !== 'pass') return 'pipeline-gates: verdict must be pass or blocking.'
  const missing = qcMissing(run)
  if (missing.length > 0) return `pipeline-gates: the pass is refused; missing: ${missing.join(', ')}.`
  const [next, seq] = bump(run)
  return { ...next, qc: { isOpen: true, how: `pass: ${summary}`, at: now }, qcSeq: seq }
}

export function enterPhase(run: Run, phase: number, task: string, now: number): Run | string {
  if (!Number.isInteger(phase) || phase < 0 || phase >= PHASES.length) return `phase must be 0 to ${PHASES.length - 1}`
  if (phase >= 2 && closedForDev(run).length > 0) return devRefusal(run, `Phase ${phase}`)
  if (phase >= 6 && !qcHolds(run)) return prRefusal(run)
  return {
    ...run,
    task: task !== '' ? task : run.task,
    phase,
    phaseAt: run.phaseAt.map((at, i) => (i === phase && at === 0 ? now : at)),
    isDone: phase === PHASES.length - 1,
  }
}

export function openGate(run: Run, name: GateName, how: string, now: number): Run {
  const gate: Gate = { isOpen: true, how, at: now }
  if (name !== 'qc') return { ...run, [name]: gate }
  const [next, seq] = bump(run)
  return { ...next, qc: gate, qcSeq: seq }
}
