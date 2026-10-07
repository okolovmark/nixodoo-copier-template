import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { EnvSnapshot, GitStatus, Service, Touched, WtEnv } from '../types'
import {
  isSameTouched,
  mergeTouched,
  parseActive,
  parseGitStatus,
  touchedBy,
  worktreeSlug,
  wtDb,
} from './env'
import type { Where } from './env'
import { ADDONS, ADDONS_BRANCH, ANSWERS_FILE, DB_NAME, ENV_BRANCHES, PORTS, SERVICE_SUFFIX } from './config'

const REFRESH_MS = 20_000
const SERVICES = [
  { name: 'odoo', unit: `odoo${SERVICE_SUFFIX}.service`, port: PORTS.odoo },
  { name: 'pg', unit: `postgres${SERVICE_SUFFIX}.service`, port: PORTS.pg },
  { name: 'nginx', unit: `nginx${SERVICE_SUFFIX}.service`, port: PORTS.nginx },
] as const

type Tip = 'env-env' | 'env-addons' | 'env-branch' | 'env-dirty' | 'env-sync' | 'env-services' | 'env-others' | `env-wt-${number}`

// what each segment means; shown above the row while the pointer is on the segment
const TIPS: { scope: Tip; text: string }[] = [
  {
    scope: 'env-env',
    text: 'The env repo itself (this project: flake, .claude, docs/adr, scripts): shown because this session edited files in it or ran a changing git command there.',
  },
  {
    scope: 'env-addons',
    text: `The main checkout of the custom modules, ${ADDONS}: shown because this session edited files or ran git in it.`,
  },
  {
    scope: 'env-branch',
    text: `The checkout's branch: plain on its default (env ${ENV_BRANCHES.join('/')}, addons ${ADDONS_BRANCH}), cyan on any other; "(local)" = no upstream yet.`,
  },
  { scope: 'env-dirty', text: 'Working tree: ✓ clean, or ±N files changed, staged or untracked (git status).' },
  {
    scope: 'env-sync',
    text: '↓N behind: commits on the upstream branch not pulled yet. ↑N ahead: local commits not pushed. As of the last fetch.',
  },
  {
    scope: 'env-services',
    text: `The local dev stack (systemctl --user): ${SERVICES[0].unit} on :${PORTS.odoo} (db ${DB_NAME}), ${SERVICES[1].unit} on :${PORTS.pg} (${DB_NAME} and every wt_* db), ${SERVICES[2].unit} proxy on :${PORTS.nginx}. ● up, ○ down.`,
  },
]

const snapshot = atom({ plugin: 'odoo-env-band', key: 'snapshot' } as const, null)
const touched = atom({ plugin: 'odoo-env-band', key: 'touched' } as const, { env: false, addons: false, wts: [] })

let project: string | null = null

// the project root: the nearest folder up from the session's root holding the copier answers file
async function projectDir($: EngineInterface): Promise<string> {
  if (project !== null) return project
  const root = await $.session.root()
  for (let dir = root; dir !== ''; dir = dir.slice(0, dir.lastIndexOf('/'))) {
    if (await $.fs.exists(`${dir}/${ANSWERS_FILE}`)) return (project = dir)
  }
  return (project = root)
}

async function gitStatus($: EngineInterface, dir: string): Promise<GitStatus | null> {
  const run = await $.process.run(['git', '-C', dir, 'status', '--porcelain=v2', '--branch'], { timeoutMs: 5000 })
  return run.exitCode === 0 ? parseGitStatus(run.stdout) : null
}

async function collect($: EngineInterface): Promise<EnvSnapshot> {
  const project = await projectDir($)
  const cwd = await $.session.cwd()
  const here = worktreeSlug(cwd, project)
  // the cwd counts for addons and a worktree (going there is deliberate), never for the env repo it starts in
  const mine = mergeTouched(await read($, touched), {
    env: false,
    addons: `${cwd}/`.startsWith(`${project}/${ADDONS}/`),
    wts: here === null ? [] : [here],
  })

  const envRoot = `${project}/.worktrees/_env`
  const slugs = (await $.fs.exists(envRoot))
    ? (await $.fs.list(envRoot)).filter(entry => entry.kind === 'dir').map(entry => entry.name).sort()
    : []
  const ports = await Promise.all(
    slugs.map(slug => $.fs.read(`${envRoot}/${slug}/port`).then(text => text.trim(), () => '?')),
  )
  const wtUnit = (slug: string) => `odoo${SERVICE_SUFFIX}-wt@${slug}.service`
  const units = [...SERVICES.map(service => service.unit), ...slugs.map(wtUnit)]
  const systemctl = await $.process.run(['systemctl', '--user', 'is-active', ...units], { timeoutMs: 5000 })
  const active = parseActive(systemctl.stdout, units)

  const wts: WtEnv[] = await Promise.all(
    slugs.map(async (slug, i) => {
      const isMine = mine.wts.includes(slug)
      return {
        slug,
        port: ports[i] ?? '?',
        db: wtDb(slug),
        isActive: active[wtUnit(slug)] === true,
        isMine,
        git: isMine ? await gitStatus($, `${project}/.worktrees/${slug}`) : null,
      }
    }),
  )
  const services: Service[] = SERVICES.map(service => ({
    name: service.name,
    port: service.port,
    isActive: active[service.unit] === true,
  }))
  return {
    env: mine.env ? { git: await gitStatus($, project) } : null,
    addons: mine.addons ? { git: await gitStatus($, `${project}/${ADDONS}`) } : null,
    services,
    wts,
  }
}

let running: Promise<void> | null = null

// one refresh at a time; a call while one runs joins it
function refresh($: EngineInterface): Promise<void> {
  running ??= collect($)
    .then(next => update($, snapshot, () => next))
    .then(
      () => undefined,
      error => $.ui.log(`odoo-env-band: ${String(error)}`, { to: 'debug' }),
    )
    .finally(() => {
      running = null
    })
  return running
}

async function where($: EngineInterface): Promise<Where> {
  return { project: await projectDir($), cwd: await $.session.cwd(), addons: ADDONS }
}

// Recomputed from the transcripts, the main thread's and each subagent's, on every load: what this
// session works in is a fact of its tool calls, so a value an earlier version of these rules kept is
// replaced, never merged in.
async function rescan($: EngineInterface): Promise<void> {
  const at = await where($)
  const uses = (await $.session.messages()).flatMap(message => message.toolUses)
  for (const agent of await $.agent.list()) {
    const found = await $.session.messages({ agentId: agent.id })
    if (Array.isArray(found)) uses.push(...found.flatMap(message => message.toolUses))
  }
  const found = uses
    .filter(use => use.isError !== true)
    .reduce<Touched>((all, use) => mergeTouched(all, touchedBy(String(use.tool), use.input, at)), {
      env: false,
      addons: false,
      wts: [],
    })
  await update($, touched, () => found)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await rescan($)
    void refresh($)
    $.clock.every(REFRESH_MS, () => void refresh($))
    return started
  })

  // a checkout this session starts working in shows at once; so does a git command or a service restart
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    if (ran.deny !== undefined || ran.isError === true) return ran
    const tool = String(e.tool)
    const found = touchedBy(tool, e as unknown as Record<string, unknown>, await where($))
    const before = await read($, touched)
    const after = mergeTouched(before, found)
    if (!isSameTouched(before, after)) await update($, touched, () => after)
    if (tool === 'Bash' || !isSameTouched(before, after)) void refresh($)
    return ran
  })

  on('turn.complete', async ($, e, next) => {
    void refresh($)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const below = await next(e)
    const env = await read($, snapshot)
    if (env === null || e.props.hasSurvey) return below

    const { Box, Text } = $.ui.resolve(e)
    const mine = env.wts.filter(wt => wt.isMine)
    const others = env.wts.filter(wt => !wt.isMine)
    // a segment joins its tip's hover group: pointing at it underlines it and reveals the tip row
    const hint = (scope: Tip) => ({ scope, underline: true })

    const gitState = (git: GitStatus | null, defaults: readonly string[], isWorktree: boolean) =>
      git === null ? (
        <Text color="red">no git</Text>
      ) : (
        <Box flexDirection="row" columnGap={1}>
          <Text bold color={defaults.includes(git.branch) ? undefined : 'cyan'} hover={hint('env-branch')}>
            {git.hasUpstream || defaults.includes(git.branch) || isWorktree
              ? git.branch
              : `${git.branch} (local)`}
          </Text>
          {git.dirty > 0 ? (
            <Text color="yellow" hover={hint('env-dirty')}>{`±${git.dirty} changed`}</Text>
          ) : (
            <Text color="green" hover={hint('env-dirty')}>
              ✓ clean
            </Text>
          )}
          {git.ahead > 0 && <Text dimColor hover={hint('env-sync')}>{`↑${git.ahead} ahead`}</Text>}
          {git.behind > 0 && <Text color="yellow" hover={hint('env-sync')}>{`↓${git.behind} behind`}</Text>}
        </Box>
      )

    const dynamicTips: { scope: Tip; text: string }[] = [
      ...mine.map((wt, i) => ({
        scope: `env-wt-${i}` as const,
        text: `Worktree env this session works in: .worktrees/${wt.slug}, Odoo odoo${SERVICE_SUFFIX}-wt@${wt.slug} on :${wt.port} (${wt.isActive ? 'up' : 'down'}), db ${wt.db}.`,
      })),
      ...(others.length === 0
        ? []
        : [
            {
              scope: 'env-others' as const,
              text: `Worktree envs of other sessions on this machine: ${others
                .map(wt => `${wt.slug} :${wt.port} ${wt.isActive ? 'up' : 'down'}`)
                .join(', ')}.`,
            },
          ]),
    ]

    // a tip only for a segment that is drawn
    const hasCheckout = env.env !== null || env.addons !== null || mine.length > 0
    const isTipShown = (scope: Tip) =>
      scope === 'env-services' ||
      (scope === 'env-env' ? env.env !== null : scope === 'env-addons' ? env.addons !== null : hasCheckout)

    const row = (
      <Box key="odoo-env" flexDirection="row" columnGap={1} flexWrap="wrap">
        {env.env !== null && (
          <Box flexDirection="row" columnGap={1}>
            <Text dimColor hover={hint('env-env')}>
              env
            </Text>
            {gitState(env.env.git, ENV_BRANCHES, false)}
            <Text dimColor>·</Text>
          </Box>
        )}
        {env.addons !== null && (
          <Box flexDirection="row" columnGap={1}>
            <Text dimColor hover={hint('env-addons')}>
              addons
            </Text>
            {gitState(env.addons.git, [ADDONS_BRANCH], false)}
            <Text dimColor>·</Text>
          </Box>
        )}
        {mine.map((wt, i) => (
          <Box flexDirection="row" columnGap={1}>
            <Text dimColor hover={hint(`env-wt-${i}`)}>
              wt
            </Text>
            {gitState(wt.git, [ADDONS_BRANCH], true)}
            <Text color={wt.isActive ? 'green' : undefined} dimColor={!wt.isActive} hover={hint(`env-wt-${i}`)}>
              {`:${wt.port} ${wt.isActive ? '● up' : '○ down'}`}
            </Text>
            <Text dimColor>·</Text>
          </Box>
        ))}
        {env.services.map(service => (
          <Text color={service.isActive ? 'green' : 'red'} hover={hint('env-services')}>
            {`${service.name} :${service.port} ${service.isActive ? '●' : '○'}`}
          </Text>
        ))}
        {others.length > 0 && <Text dimColor>·</Text>}
        {others.length > 0 && (
          <Text dimColor hover={hint('env-others')}>
            {`${others.length} other wt env${others.length === 1 ? '' : 's'}`}
          </Text>
        )}
      </Box>
    )
    return (
      <Box flexDirection="column">
        {/* every band plugin draws its tips before the plugins beneath it and its rows after them: all tips sit on top */}
        {[...TIPS.filter(tip => isTipShown(tip.scope)), ...dynamicTips].map(tip => (
          <Box key={`tip-${tip.scope}`} display="none" hover={{ scope: tip.scope, display: 'flex' }} flexDirection="row" columnGap={1}>
            <Text color="cyan">ⓘ</Text>
            <Text dimColor>{tip.text}</Text>
          </Box>
        ))}
        {below}
        {row}
      </Box>
    )
  })
}
