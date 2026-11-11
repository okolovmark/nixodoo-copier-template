import type { BlockTask, Gate, GateName, Run, TaskState } from '../types'
import { RELEASE_NOTES_DIR, TICKET_PREFIXES } from './config'

export const PHASES = [
  'Memory bootstrap',
  'Task reading',
  'Development',
  'QC gate',
  'Documentation',
  'Demo video (optional)',
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
const PR_URL = /https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+/
// A task key is one of the ticket prefixes and a number, so prose naming a month column (JAN-2026),
// a standard (ISO-9001) or a project the prefixes do not name stays out of the block; without a
// prefix, any word-dash-number. project_key numbers a project's tasks from an unpadded sequence, so a
// key's number never starts with 0: product codes are zero-padded (RAW-000010, ASM-001640).
const KEY_PREFIX =
  TICKET_PREFIXES.length === 0
    ? '[A-Z][A-Z0-9]{1,9}'
    : `(?:${TICKET_PREFIXES.map(prefix => prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`
const TASK_KEY = new RegExp(`\\b${KEY_PREFIX}-[1-9]\\d*\\b`)

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
  return DOC_PATH.test(path) || isReleaseNotePath(path)
}

// a task's release note, where the project runs release notes: text for the users, written after the QC pass
export function isReleaseNotePath(path: string): boolean {
  return RELEASE_NOTES_DIR !== '' && path.includes(RELEASE_NOTES_DIR) && path.endsWith('.html')
}

// One simple command of a shell line: its words with the quotes taken off, and the here-documents fed
// to it. REDIRECT stands before a redirection's target, which is no word of the command.
type ShellCommand = { words: string[]; heredocs: string[] }
const REDIRECT = '\u0000>'
const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*\+?=/
// what may stand before the program a simple command runs
const RESERVED = new Set(['!', '{', 'if', 'then', 'elif', 'else', 'while', 'until', 'do'])
// programs that run the program after them, past their own options and a count or a duration
const WRAPPERS = new Set(['builtin', 'command', 'env', 'exec', 'nice', 'nohup', 'sudo', 'time', 'timeout'])
const SHELLS = new Set(['bash', 'dash', 'ksh', 'sh', 'zsh'])

// Splits a shell line the way the shell does, far enough to tell which programs it runs: quotes and
// here-documents are text, $( ) and backticks are commands of their own. Nested, the scan stops at
// the `)` that closes its $( ), and `end` is where it stopped.
function scanShell(src: string, start = 0, isNested = false): { commands: ShellCommand[]; end: number } {
  const commands: ShellCommand[] = []
  const pending: { delimiter: string; stripsTabs: boolean; command: ShellCommand }[] = []
  let current: ShellCommand = { words: [], heredocs: [] }
  let word: string | null = null
  let depth = 0
  const endWord = () => {
    if (word !== null) current.words.push(word)
    word = null
  }
  const endCommand = () => {
    endWord()
    if (current.words.length > 0) commands.push(current)
    current = { words: [], heredocs: [] }
  }
  // a substitution's commands are the line's commands too; its text stays in the word
  const substitute = (from: number): number => {
    const inner = scanShell(src, from + 2, true)
    commands.push(...inner.commands)
    word = (word ?? '') + src.slice(from, inner.end + 1)
    return inner.end + 1
  }
  const backticks = (from: number): number => {
    let close = from + 1
    while (close < src.length && src.charAt(close) !== '`') close += src.charAt(close) === '\\' ? 2 : 1
    commands.push(...scanShell(src.slice(from + 1, close)).commands)
    word = (word ?? '') + src.slice(from, close + 1)
    return close + 1
  }
  // <<DELIM, <<-DELIM, <<'DELIM': the body starts on the next line
  const heredoc = (from: number): number => {
    let at = from + 2
    const stripsTabs = src.charAt(at) === '-'
    if (stripsTabs) at++
    while (src.charAt(at) === ' ' || src.charAt(at) === '\t') at++
    let delimiter = ''
    while (at < src.length && !/[\s;&|<>()]/.test(src.charAt(at))) {
      const c = src.charAt(at)
      if (c === "'" || c === '"') {
        const close = src.indexOf(c, at + 1)
        const stop = close === -1 ? src.length : close
        delimiter += src.slice(at + 1, stop)
        at = stop + 1
      } else {
        delimiter += c === '\\' ? src.charAt(at + 1) : c
        at += c === '\\' ? 2 : 1
      }
    }
    pending.push({ delimiter, stripsTabs, command: current })
    return at
  }
  // the bodies of the here-documents the line just ended opened, each up to its delimiter line
  const readHeredocs = (from: number): number => {
    let at = from
    for (const doc of pending) {
      const lines: string[] = []
      while (at < src.length) {
        const newline = src.indexOf('\n', at)
        const stop = newline === -1 ? src.length : newline
        const line = doc.stripsTabs ? src.slice(at, stop).replace(/^\t+/, '') : src.slice(at, stop)
        at = stop + 1
        if (line === doc.delimiter) break
        lines.push(line)
      }
      doc.command.heredocs.push(lines.join('\n'))
    }
    pending.length = 0
    return at
  }

  let i = start
  while (i < src.length) {
    const c = src.charAt(i)
    const next = src.charAt(i + 1)
    if (c === '\\') {
      // an escaped character is text; an escaped newline joins the lines
      if (next !== '\n') word = (word ?? '') + next
      i += 2
    } else if (c === "'") {
      const close = src.indexOf("'", i + 1)
      const stop = close === -1 ? src.length : close
      word = (word ?? '') + src.slice(i + 1, stop)
      i = stop + 1
    } else if (c === '"') {
      word = word ?? ''
      i++
      while (i < src.length && src.charAt(i) !== '"') {
        const d = src.charAt(i)
        if (d === '\\' && i + 1 < src.length && '$`"\\\n'.includes(src.charAt(i + 1))) {
          if (src.charAt(i + 1) !== '\n') word += src.charAt(i + 1)
          i += 2
        } else if (d === '$' && src.charAt(i + 1) === '(') i = substitute(i)
        else if (d === '`') i = backticks(i)
        else {
          word += d
          i++
        }
      }
      i++
    } else if (c === '`') i = backticks(i)
    else if (c === '$' && next === '(') i = substitute(i)
    else if (c === '#' && word === null) {
      const newline = src.indexOf('\n', i)
      i = newline === -1 ? src.length : newline
    } else if (c === ' ' || c === '\t') {
      endWord()
      i++
    } else if (c === '\n') {
      endCommand()
      i = readHeredocs(i + 1)
    } else if (c === '(') {
      endCommand()
      depth++
      i++
    } else if (c === ')') {
      endCommand()
      if (depth === 0 && isNested) return { commands, end: i }
      depth = Math.max(0, depth - 1)
      i++
    } else if (c === ';' || c === '|' || (c === '&' && next !== '>')) {
      endCommand()
      i++
    } else if (c === '<' && next === '<' && src.charAt(i + 2) !== '<') {
      endWord()
      i = heredoc(i)
    } else if (c === '<' || c === '>' || c === '&') {
      // a redirection (2>&1, &>log, <<<text): a file descriptor before it is no word either
      if (word !== null && /^\d+$/.test(word)) word = null
      endWord()
      current.words.push(REDIRECT)
      i++
      while (i < src.length && '<>&|'.includes(src.charAt(i))) i++
    } else {
      word = (word ?? '') + c
      i++
    }
  }
  endCommand()
  return { commands, end: src.length }
}

// the program a simple command runs, then its arguments: past assignments, redirections, reserved
// words and the wrappers that run the program after them
function programOf(words: readonly string[]): string[] {
  const args = words.filter((word, i) => word !== REDIRECT && words[i - 1] !== REDIRECT)
  let at = 0
  let isWrapped = false
  while (at < args.length) {
    const word = args[at] ?? ''
    if (ASSIGNMENT.test(word) || RESERVED.has(word)) at++
    else if (WRAPPERS.has(word)) {
      isWrapped = true
      at++
    } else if (isWrapped && /^(?:-|\d+(?:\.\d+)?[smhd]?$)/.test(word)) at++
    else break
  }
  return args.slice(at)
}

// gh pr create, or its alias gh pr new; a --repo may stand before the subcommand
function isGhPrCreate(args: readonly string[]): boolean {
  const words = args.filter((word, i) => !word.startsWith('-') && !/^(?:-R|--repo)$/.test(args[i - 1] ?? ''))
  return words[0] === 'pr' && (words[1] === 'create' || words[1] === 'new')
}

// A command line that runs gh pr create: as a program of its own (after `cd … &&`, in a $( ) or
// backticks, under env or timeout), or through a shell's -c script, eval, or a here-document fed to a
// shell. The words inside a quoted argument, or a here-document fed to anything else, are text and
// no command: a `kb add` note may name it. Nested more than a few shells deep, the plain text decides.
export function isPrCreate(command: string, depth = 0): boolean {
  if (depth > 3) return PR_CREATE.test(command)
  return scanShell(command).commands.some(({ words, heredocs }) => {
    const [program = '', ...args] = programOf(words)
    const name = program.slice(program.lastIndexOf('/') + 1)
    if (name === 'gh') return isGhPrCreate(args)
    if (name === 'eval') return isPrCreate(args.join(' '), depth + 1)
    if (!SHELLS.has(name)) return false
    const script = args.findIndex(arg => /^-[A-Za-z]*c[A-Za-z]*$/.test(arg))
    return script === -1 ? heredocs.some(body => isPrCreate(body, depth + 1)) : isPrCreate(args[script + 1] ?? '', depth + 1)
  })
}

// the pull request a gh pr create printed, '' when none
export function prUrlIn(output: string): string {
  return PR_URL.exec(output)?.[0] ?? ''
}

export function taskKeyIn(text: string): string {
  return TASK_KEY.exec(text)?.[0] ?? ''
}

// every task key in the text, once each, in order: the block a pipeline started with several takes on
export function taskKeysIn(text: string): string[] {
  return [...new Set(text.match(new RegExp(TASK_KEY.source, 'g')) ?? [])]
}

export function tasksOf(run: Run): BlockTask[] {
  return run.tasks ?? []
}

// a block saved before keys were bound to the ticket prefix can hold a word that is no key (JAN-2026)
export function withoutStrayTasks(run: Run): Run {
  const tasks = tasksOf(run).filter(task => taskKeyIn(task.key) === task.key)
  return tasks.length === tasksOf(run).length ? run : { ...run, tasks }
}

export function hasTask(run: Run, key: string): boolean {
  return tasksOf(run).some(task => task.key === key)
}

// The record URL in a task item, kept only when it opens a record in the production web client
// (`webUrl`, then `#` or `?`): a link the person clicks never leads anywhere else.
export function taskUrlIn(item: string, webUrl: string): string {
  const url = /https:\/\/\S+/.exec(item)?.[0] ?? ''
  const base = webUrl.replace(/\/+$/, '')
  return base !== '' && (url.startsWith(`${base}#`) || url.startsWith(`${base}?`)) ? url.slice(0, 500) : ''
}

// Tasks given as "KIO-1234", "KIO-1234 short title", either with the task's record URL, join the block
// as pending; one already in it keeps its state and takes a new title or URL when one is given.
export function addTasks(run: Run, items: readonly string[], now: number, webUrl = ''): Run {
  let tasks = tasksOf(run)
  for (const item of items) {
    const key = taskKeyIn(item.toUpperCase())
    if (key === '') continue
    const url = taskUrlIn(item, webUrl)
    const text = item.replace(/https?:\/\/\S+/g, ' ')
    const title = text.slice(text.toUpperCase().indexOf(key) + key.length).replace(/^[\s:·,-]+/, '').trim().slice(0, 80)
    const known = tasks.find(task => task.key === key)
    if (known === undefined) tasks = [...tasks, { key, title, state: 'pending', at: now, url }]
    else if ((title !== '' && title !== known.title) || (url !== '' && url !== known.url)) {
      tasks = tasks.map(task => (task.key === key ? { ...task, title: title || task.title, url: url || task.url } : task))
    }
  }
  return tasks === tasksOf(run) ? run : { ...run, tasks }
}

// moves the named tasks to `state`; a key not in the block joins it in that state
export function moveTasks(run: Run, keys: readonly string[], state: TaskState, now: number): Run {
  const withAll = addTasks(run, keys, now)
  const wanted = new Set(keys.map(key => taskKeyIn(key.toUpperCase())).filter(key => key !== ''))
  const tasks = tasksOf(withAll).map(task => (wanted.has(task.key) && task.state !== state ? { ...task, state, at: now } : task))
  return { ...withAll, tasks }
}

export function taskCounts(run: Run): Record<TaskState, number> & { total: number } {
  const tasks = tasksOf(run)
  const count = (state: TaskState) => tasks.filter(task => task.state === state).length
  return { pending: count('pending'), active: count('active'), done: count('done'), total: tasks.length }
}

// "2/11 done · 4 in work · 5 pending", or '' for a run of one task
export function blockLine(run: Run): string {
  const counts = taskCounts(run)
  return counts.total > 1 ? `${counts.done}/${counts.total} done · ${counts.active} in work · ${counts.pending} pending` : ''
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

export const MAX_FIX_ROUNDS = 3

// A fix round: dev work handed out in Phases 2-3 after a review finished since the last round began.
// Several dev agents spawned for one round count once.
export function isNewRound(run: Run): boolean {
  return run.phase >= 2 && run.phase <= 3 && run.lastReview > (run.roundSeq ?? 0)
}

export function roundsLeft(run: Run): number {
  return MAX_FIX_ROUNDS + (run.extraRounds ?? 0) - (run.fixRounds ?? 0)
}

export function startRound(run: Run): Run {
  const [next, seq] = bump(run)
  return { ...next, fixRounds: (run.fixRounds ?? 0) + 1, roundSeq: seq }
}

export function grantRounds(run: Run): Run {
  return { ...run, extraRounds: (run.extraRounds ?? 0) + MAX_FIX_ROUNDS }
}

export function roundsLine(run: Run): string {
  return `fix rounds: ${run.fixRounds ?? 0} of ${MAX_FIX_ROUNDS + (run.extraRounds ?? 0)}`
}

export function roundsRefusal(run: Run): string {
  return `pipeline-gates: fix round ${(run.fixRounds ?? 0) + 1} is refused, the QC gate still blocks after ${run.fixRounds ?? 0} rounds. Stop and report to the user what still blocks, what keeps coming back, and the choice: narrow the design, split the rest into a follow-up task, or more rounds. Only the user grants more: /pipeline-gate open rounds <reason> (three more).`
}

// A finished agent re-reads its whole context on every call, and writes it to the cache again after a pause.
export const RESUME_REFUSAL =
  'pipeline-gates: a fix round goes to a fresh dev agent, not into one that already worked: spawn dev with a brief (the verified findings with their decisive lines, and `git diff --cached --stat <REVIEWED_TREE>` for what earlier rounds changed).'

// SendMessage's `to`: a name or an agent id, with a " [ref]" a listing may have shown
export function addressOf(to: unknown): string {
  return typeof to === 'string' ? to.replace(/\s*\[[^\]]*\]\s*$/, '').trim() : ''
}

// A pipeline split over two sessions: the mother grills and closes, a child develops and opens the PR.
// The child loads the skill as `child <TASK-KEYS>`. Every session's run sits in the mod's store under
// run:<session id>, which is how one session reads another's.
const CHILD_ARGS = /^\s*child\b/i

export function isChildArgs(args: string): boolean {
  return CHILD_ARGS.test(args)
}

// how a run names another session's run to the person: its task and the start of its session id
export function runName(run: Run, sessionId: string): string {
  return `the ${run.task || 'pipeline'} run of session ${sessionId.slice(0, 8)}`
}

// the keys a run works on: its task and its block's
export function runKeys(run: Run): string[] {
  return [...new Set([run.task, ...tasksOf(run).map(task => task.key)].filter(key => key !== ''))]
}

function isOfTasks(run: Run, keys: readonly string[]): boolean {
  return keys.some(key => run.task === key || hasTask(run, key))
}

// A gate opened in that run itself: by the person's press or their /pipeline-gate open, the Teams
// send, or a project with no status flow. A gate a run took from another one is never passed on.
function isOpenedThere(gate: Gate | null): boolean {
  return isOpen(gate) && gate?.from === undefined
}

// The run a child takes its gates from: in progress, working on `task` (its task or one of its
// block), with the Teams status and the grill both opened there.
export function isMotherRunFor(other: Run, task: string): boolean {
  return isActive(other) && task !== '' && isOfTasks(other, [task]) && isOpenedThere(other.status) && isOpenedThere(other.grill)
}

// A child's run takes the status and grill gates the person opened in the mother, each naming the run
// it came from; a gate the child opened itself stays as it is. The QC gate is never taken.
export function takeGates(run: Run, mother: Run, motherId: string, now: number): Run {
  const source = runName(mother, motherId)
  const taken = (own: Gate | null, given: Gate | null): Gate | null =>
    isOpen(own) || given === null ? own : { isOpen: true, how: `${given.how}, taken from ${source}`, at: now, from: motherId }
  return { ...run, status: taken(run.status, mother.status), grill: taken(run.grill, mother.grill), mother: motherId }
}

// What lets a run close at Phase 7 without a QC pass of its own: another run for its tasks that
// recorded a QC pass or a PR (the child of a mother that grilled and handed off).
export function closesOn(other: Run, keys: readonly string[]): string {
  if (!isOfTasks(other, keys)) return ''
  if ((other.pr ?? '') !== '') return `PR ${other.pr}`
  return isOpen(other.qc) ? `QC ${other.qc?.how ?? 'pass'}` : ''
}

function closureRefusal(run: Run): string {
  const keys = runKeys(run).join(', ') || 'this task'
  return `pipeline-gates: Phase 7 is refused until the QC gate passes in this run, or a run for ${keys} in another session recorded a QC pass or a PR; none has.`
}

// `closure`: the other run this one closes on, named for the person ('' when none was found)
export function enterPhase(run: Run, phase: number, task: string, now: number, closure = ''): Run | string {
  if (!Number.isInteger(phase) || phase < 0 || phase >= PHASES.length) return `phase must be 0 to ${PHASES.length - 1}`
  if (phase >= 2 && closedForDev(run).length > 0) return devRefusal(run, `Phase ${phase}`)
  const isClosure = phase === PHASES.length - 1
  if (isClosure && !qcHolds(run) && closure === '') return closureRefusal(run)
  if (phase >= 6 && !isClosure && !qcHolds(run)) return prRefusal(run)
  // development entered anew (the next task of a block) counts its rounds afresh; back from QC it does not
  const isNewCycle = phase === 2 && run.phase !== 2 && run.phase !== 3
  return {
    ...run,
    ...(isNewCycle ? { fixRounds: 0, roundSeq: run.seq, extraRounds: 0 } : {}),
    ...(isClosure && !qcHolds(run) ? { closedOn: closure } : {}),
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
