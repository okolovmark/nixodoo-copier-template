import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { ADDONS, PORTS } from '../hooks/config'
import { parseActive, parseGitStatus, touchedBy, worktreeSlug, wtDb } from '../hooks/env'

const PROJECT = '/p'
const AT = { project: PROJECT, cwd: PROJECT, addons: ADDONS }
const BAND = {
  plugin: 'odoo-env-band',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 120,
    scroll: { offset: 0, bodyRows: 9 },
    view: {},
  },
} as const

type Use = { tool_use_id: string; tool: string; input: Record<string, unknown> }

// two worktree envs on the machine (kio-1 up, kio-2 down); odoo, pg, nginx up
function engine(on: On, cwd: string, history: Use[] = []): void {
  mock.clock(on)
  on('session.cwd', () => ({ value: cwd }))
  on('session.root', () => ({ value: PROJECT }))
  on('session.messages', () => ({ value: [{ role: 'assistant', text: '', toolUses: history }] }))
  on('agent.list', () => ({ value: [] }))
  on('fs.exists', () => ({ value: true }))
  on('fs.list', () => ({
    value: [
      { name: 'kio-1', kind: 'dir', size: 0, mtimeMs: 0, isLink: false },
      { name: 'kio-2', kind: 'dir', size: 0, mtimeMs: 0, isLink: false },
    ],
  }))
  on('fs.read', (_$, e) => ({ value: e.path.includes('kio-1') ? '2701\n' : '2702\n' }))
  on('process.run', (_$, e) => {
    const dir = String(e.argv[2] ?? '')
    const stdout =
      e.argv[0] !== 'git'
        ? 'active\nactive\nactive\nactive\ninactive\n'
        : dir.endsWith(ADDONS)
          ? '# branch.oid aaaa\n# branch.head 16.0\n# branch.upstream origin/16.0\n# branch.ab +0 -3\n'
          : dir === PROJECT
            ? '# branch.oid cccc\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +1 -0\n1 .M x\n'
            : '# branch.oid bbbb\n# branch.head kio-1-feature\n? x.py\n'
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('ui.log', () => ({ value: undefined }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('tool.call', () => ({ result: { stdout: '', stderr: '', interrupted: false } }))
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
}

describe('parsers', () => {
  test('porcelain v2 header and changed paths', async () => {
    const out = [
      '# branch.oid 0123456789abcdef',
      '# branch.head kio-1810-x',
      '# branch.upstream origin/kio-1810-x',
      '# branch.ab +2 -1',
      '1 .M N... 100644 100644 100644 a b models/x.py',
      '? new.py',
      '',
    ].join('\n')
    expect(parseGitStatus(out)).toEqual({ branch: 'kio-1810-x', dirty: 2, ahead: 2, behind: 1, hasUpstream: true })
    expect(parseGitStatus('# branch.oid deadbeefcafe\n# branch.head (detached)\n').branch).toBe('deadbeef')
  })

  test('cwd, systemctl and the db name', async () => {
    expect(worktreeSlug('/p/.worktrees/kio-1/models', PROJECT)).toBe('kio-1')
    expect(worktreeSlug('/p/.worktrees/_env/kio-1', PROJECT)).toBe(null)
    expect(parseActive('active\ninactive\n', ['a', 'b'])).toEqual({ a: true, b: false })
    expect(wtDb('kio-1826-chain')).toBe('wt_kio_1826_chain')
  })

  test('what counts as working in a checkout', async () => {
    const none = { env: false, addons: false, wts: [] }
    expect(touchedBy('Edit', { file_path: `/p/${ADDONS}/m/x.py` }, AT)).toEqual({ ...none, addons: true })
    expect(touchedBy('Edit', { file_path: '/p/.worktrees/kio-1/m/x.py' }, AT)).toEqual({ ...none, wts: ['kio-1'] })
    expect(touchedBy('Write', { file_path: '/p/docs/adr/0050-x.md', content: '' }, AT)).toEqual({ ...none, env: true })
    expect(touchedBy('Edit', { file_path: '/p/.claude/skills/deploy/SKILL.md' }, AT)).toEqual({ ...none, env: true })
    expect(touchedBy('Edit', { file_path: '/p/src/odoo/odoo/models.py' }, AT)).toEqual(none)
    expect(touchedBy('Edit', { file_path: '/home/x/.claude/memory/a.md' }, AT)).toEqual(none)
    expect(touchedBy('Bash', { command: `git -C /p/${ADDONS} commit -m x` }, AT).addons).toBe(true)
    expect(touchedBy('Bash', { command: `git -C /p/${ADDONS} worktree list` }, AT)).toEqual(none)
    expect(touchedBy('Bash', { command: 'git add -A && git commit -m "[DOC] adr"' }, AT)).toEqual({ ...none, env: true })
    expect(touchedBy('Bash', { command: 'git -C "$ODOO16_PROJECT_DIR" push' }, AT)).toEqual({ ...none, env: true })
    expect(touchedBy('Bash', { command: `git -C ${ADDONS} pull` }, AT)).toEqual({ ...none, addons: true })
    expect(touchedBy('Bash', { command: 'bash wt-start.sh kio-9 && ls /p/.worktrees/_env' }, AT).wts).toEqual(['kio-9'])
    expect(touchedBy('Read', { file_path: '/p/.worktrees/kio-1/x.py' }, AT)).toEqual(none)
  })
})

test('a session that works in neither: services, and the other envs as a count', async ($, on) => {
  engine(on, PROJECT)
  await $.session.start({ cwd: PROJECT, surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'true' })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })
    expect(await ui.find({ text: 'addons' })).toBeUndefined()
    expect(await ui.find({ text: /^env$/ })).toBeUndefined()
    expect(await ui.find({ text: `odoo :${PORTS.odoo} ●` })).toBeDefined()
    expect(await ui.find({ text: `pg :${PORTS.pg} ●` })).toBeDefined()
    expect(await ui.find({ text: `nginx :${PORTS.nginx} ●` })).toBeDefined()
    expect(await ui.find({ text: '2 other wt envs' })).toBeDefined()
    expect(await ui.find({ key: 'tip-env-others', text: /kio-1 :2701 up, kio-2 :2702 down/ })).toBeDefined()
    await ui.unmount()
  }
})

test('an edit in a worktree and git in addons, earlier in the transcript, show both', async ($, on) => {
  engine(on, PROJECT, [
    { tool_use_id: 'a', tool: 'Edit', input: { file_path: '/p/.worktrees/kio-1/m/x.py' } },
    { tool_use_id: 'b', tool: 'Bash', input: { command: `git -C /p/${ADDONS} pull` } },
    { tool_use_id: 'c', tool: 'Write', input: { file_path: '/p/docs/adr/0050-x.md', content: '' } },
  ])
  await $.session.start({ cwd: PROJECT, surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'true' })
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect(await ui.find({ text: '↓3 behind' })).toBeDefined()
  expect(await ui.find({ text: /^env$/ })).toBeDefined()
  expect(await ui.find({ text: '↑1 ahead' })).toBeDefined()
  expect(await ui.find({ key: 'tip-env-env' })).toBeDefined()
  expect(await ui.find({ text: 'kio-1-feature' })).toBeDefined()
  expect(await ui.find({ text: ':2701 ● up' })).toBeDefined()
  expect(await ui.find({ text: '1 other wt env' })).toBeDefined()
  expect(await ui.find({ key: 'tip-env-wt-0', text: /db wt_kio_1/ })).toBeDefined()
  await ui.unmount()
})
