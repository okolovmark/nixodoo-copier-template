import type { GitStatus, Touched } from '../types'

// `git status --porcelain=v2 --branch`: header lines start with '#', every other line is one changed path
export function parseGitStatus(out: string): GitStatus {
  let branch = '?'
  let oid = ''
  let ahead = 0
  let behind = 0
  let hasUpstream = false
  let dirty = 0
  for (const line of out.split('\n')) {
    if (line.startsWith('# branch.head ')) branch = line.slice('# branch.head '.length).trim()
    else if (line.startsWith('# branch.oid ')) oid = line.slice('# branch.oid '.length).trim()
    else if (line.startsWith('# branch.upstream ')) hasUpstream = true
    else if (line.startsWith('# branch.ab ')) {
      const ab = /\+(\d+) -(\d+)/.exec(line)
      if (ab) {
        ahead = Number(ab[1])
        behind = Number(ab[2])
      }
    } else if (line !== '' && !line.startsWith('#')) dirty += 1
  }
  if (branch === '(detached)') branch = oid.slice(0, 8) || 'detached'
  return { branch, dirty, ahead, behind, hasUpstream }
}

// the worktree env slug when cwd is inside <project>/.worktrees/<slug>/
export function worktreeSlug(cwd: string, project: string): string | null {
  const root = `${project.replace(/\/+$/, '')}/.worktrees/`
  if (!`${cwd}/`.startsWith(root)) return null
  const slug = `${cwd}/`.slice(root.length).split('/')[0] ?? ''
  return slug !== '' && slug !== '_env' ? slug : null
}

// `systemctl --user is-active a b c` prints one state per unit, in argument order
export function parseActive(out: string, units: readonly string[]): Record<string, boolean> {
  const states = out.split('\n').map(line => line.trim())
  return Object.fromEntries(units.map((unit, i) => [unit, states[i] === 'active']))
}

// worktree-env's db_name_for: slug -> safe pg identifier
export function wtDb(slug: string): string {
  return `wt_${slug.replace(/-/g, '_')}`
}

const FILE_TOOLS = new Set(['Edit', 'Write', 'MultiEdit', 'NotebookEdit'])
const WT_PATH = /\.worktrees\/([A-Za-z0-9][A-Za-z0-9._-]*)/g
const WT_SCRIPT = /\bwt-(?:start|stop|bootstrap|restore|link)\.sh\s+([A-Za-z0-9][A-Za-z0-9._-]*)/g
// git that changes a checkout, with the -C options before the verb; status, log, diff and worktree list only look
const GIT_WRITE = /\bgit\b((?:\s+-[Cc]\s+\S+)*)\s+(?:commit|checkout|switch|pull|push|merge|rebase|reset|stash|cherry-pick|revert|am|apply|add|rm|mv|restore)\b/g

export type Where = { project: string; cwd: string; addons: string }

const NONE: Touched = { env: false, addons: false, wts: [] }

function slugsIn(text: string): string[] {
  return [...text.matchAll(WT_PATH), ...text.matchAll(WT_SCRIPT)]
    .map(match => match[1] ?? '')
    .filter(slug => slug !== '' && slug !== '_env')
}

// an absolute path: the env repo (anything under the project but src/ and .worktrees/), addons, a worktree
function classify(path: string, where: Where): Touched {
  const project = `${where.project.replace(/\/+$/, '')}/`
  const target = `${path.replace(/\/+$/, '')}/`
  if (!target.startsWith(project)) return NONE
  const wts = slugsIn(target)
  if (wts.length > 0) return { env: false, addons: false, wts }
  if (target.startsWith(`${project}${where.addons}/`)) return { env: false, addons: true, wts: [] }
  if (target.startsWith(`${project}src/`) || target.startsWith(`${project}.worktrees/`)) return NONE
  return { env: true, addons: false, wts: [] }
}

// a `git -C` argument as written: quotes off, $ODOO16_PROJECT_DIR and relative paths resolved
function resolvePath(raw: string, where: Where): string {
  const bare = raw.replace(/^["']|["']$/g, '').replace(/^\$\{?ODOO16_PROJECT_DIR\}?/, where.project)
  return bare.startsWith('/') ? bare : `${where.cwd.replace(/\/+$/, '')}/${bare}`
}

// Which checkouts one tool call works in. An edit counts, a Bash command that names a worktree or runs a
// wt-*.sh script on it counts, and so does a changing git command, in its -C directory or the session's
// cwd. A read, a grep or a `git status` does not.
export function touchedBy(tool: string, input: Record<string, unknown>, where: Where): Touched {
  if (FILE_TOOLS.has(tool)) {
    const path = typeof input.file_path === 'string' ? input.file_path : input.notebook_path
    return typeof path === 'string' ? classify(path, where) : NONE
  }
  if (tool !== 'Bash' || typeof input.command !== 'string') return NONE
  let found: Touched = { env: false, addons: false, wts: slugsIn(input.command) }
  for (const match of input.command.matchAll(GIT_WRITE)) {
    const dir = /-C\s+(\S+)/.exec(match[1] ?? '')?.[1]
    found = mergeTouched(found, classify(dir === undefined ? where.cwd : resolvePath(dir, where), where))
  }
  return found
}

export function mergeTouched(a: Touched, b: Touched): Touched {
  return {
    env: a.env || b.env,
    addons: a.addons || b.addons,
    wts: [...a.wts, ...b.wts.filter(slug => !a.wts.includes(slug))],
  }
}

export function isSameTouched(a: Touched, b: Touched): boolean {
  return a.env === b.env && a.addons === b.addons && a.wts.length === b.wts.length && a.wts.every(slug => b.wts.includes(slug))
}
