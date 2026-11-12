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
    isCollapsed: false,
    isWorking: false,
    maxRows: 10,
    bodyColumns: 120,
    scroll: { offset: 0, bodyRows: 9 },
    view: {},
  },
} as const

const PANE = {
  plugin: 'odoo-env-band',
  component: 'Pane',
  requestId: 'odoo-wts',
  props: {
    title: 'Worktree envs',
    isFocused: true,
    bodyColumns: 100,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

type Use = { tool_use_id: string; tool: string; input: Record<string, unknown> }

// what the band asked the system to open
const opened: string[][] = []
// the pane's open/close calls, and the scripts it spawned
const panes: string[] = []
const spawned: { argv: string[]; cwd: string | undefined }[] = []
// how the next spawned script ends: what it writes, its exit code
let script = { stdout: 'wt env done\n', stderr: '', code: 0 }

// two worktree envs on the machine (kio-1 up, kio-2 down); pg up
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
  // kio-1 recorded its database at wt-start; kio-2's db file is empty, so it falls back to wt_<slug>
  on('fs.read', (_$, e) => ({
    value: e.path.endsWith('/db')
      ? e.path.includes('kio-1') ? 'wt_kio_1_102026\n' : ''
      : e.path.includes('kio-1') ? '2701\n' : '2702\n',
  }))
  on('process.run', (_$, e) => {
    if (e.argv[0] === 'xdg-open') opened.push([...e.argv])
    const dir = String(e.argv[2] ?? '')
    const stdout =
      e.argv[0] !== 'git'
        ? 'active\nactive\ninactive\n'
        : dir.endsWith(ADDONS)
          ? '# branch.oid aaaa\n# branch.head 16.0\n# branch.upstream origin/16.0\n# branch.ab +0 -3\n'
          : dir === PROJECT
            ? '# branch.oid cccc\n# branch.head main\n# branch.upstream origin/main\n# branch.ab +1 -0\n1 .M x\n'
            : '# branch.oid bbbb\n# branch.head kio-1-feature\n? x.py\n'
    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('process.spawn', async function* (_$, e) {
    spawned.push({ argv: [...e.argv], cwd: e.cwd })
    if (script.stdout !== '') yield { stream: 'stdout' as const, text: script.stdout }
    if (script.stderr !== '') yield { stream: 'stderr' as const, text: script.stderr }
    // a call on $ is answered { value }, a streaming one by its chunks and then that
    return { value: { code: script.code, signal: null } }
  })
  on('ui.panes', () => ({ value: [] }))
  on('ui.open', (_$, e) => {
    panes.push(`open ${e.id}`)
    return { value: { isPlaced: true } }
  })
  on('ui.close', (_$, e) => {
    panes.push(`close ${e.id}`)
    return { value: undefined }
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
    expect(worktreeSlug('/p/.worktrees/_envrepo/kio-1/scripts', PROJECT)).toBe('kio-1')
    expect(worktreeSlug('/p/.worktrees/_envrepo', PROJECT)).toBe(null)
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

test('a session that works in neither: services, and every env as a count beside the pane button', async ($, on) => {
  engine(on, PROJECT)
  await $.session.start({ cwd: PROJECT, surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'true' })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })
    expect(await ui.find({ text: 'addons' })).toBeUndefined()
    expect(await ui.find({ text: /^env$/ })).toBeUndefined()
    // the postgres cluster is the only service, drawn first; no main odoo, no nginx
    const texts = (await ui.findAll({ type: 'Text' })).map(text => text.text)
    expect(texts[0]).toBe(`pg :${PORTS.pg} ●`)
    expect(texts.some(text => /^(odoo|nginx) :/.test(text))).toBe(false)
    expect(await ui.find({ text: 'wts 2' })).toBeDefined()
    expect((await ui.find({ key: 'wts-open' }))?.props.label).toBe('open')
    // no tips: nothing reveals on hover, nothing underlines
    expect(JSON.stringify(await ui.drawn())).not.toMatch(/"hover"|"display":"none"/)
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
  expect(await ui.find({ text: /^env$/ })).toBeDefined()
  // git state as symbols: addons clean and behind, env changed and ahead
  expect(await ui.find({ text: '✓' })).toBeDefined()
  expect(await ui.find({ text: '↓3' })).toBeDefined()
  expect(await ui.find({ text: '↑1' })).toBeDefined()
  expect(await ui.findAll({ type: 'Text', text: '±1' })).toHaveLength(2)
  // a worktree's port and state sit right after its label, before its branch
  const wt = (await ui.findAll({ type: 'Text' })).map(text => text.text)
  const label = wt.indexOf('wt')
  expect(wt.slice(label, label + 3)).toEqual(['wt', ':2701 ●', 'kio-1-feature'])
  // the session's address, its database the subdomain, behind an open button on a remote surface
  expect((await ui.find({ key: 'wt-open-kio-1' }))?.props.label).toBe('open')
  await ui.press({ key: 'wt-open-kio-1' })
  expect(opened).toEqual([['xdg-open', 'http://wt_kio_1_102026.localhost:2701/web']])
  // a bar between each two groups: pg, env, addons, the worktree, every env's count and pane button
  expect(await ui.findAll({ type: 'Text', text: '│' })).toHaveLength(4)
  expect(await ui.find({ text: 'wts 2' })).toBeDefined()
  expect(JSON.stringify(await ui.drawn())).not.toMatch(/"hover"|"display":"none"/)
  await ui.unmount()
  // and a link on the terminal
  const term = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect((await term.find({ type: 'Link' }))?.props).toMatchObject({
    href: 'http://wt_kio_1_102026.localhost:2701/web',
    label: 'open',
  })
  await term.unmount()
})

test("the band's open button brings up the pane", async ($, on) => {
  engine(on, PROJECT)
  await $.session.start({ cwd: PROJECT, surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'true' })
  panes.length = 0
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  await ui.press({ key: 'wts-open' })
  expect(panes).toEqual(['open odoo-wts'])
  await ui.unmount()
})

test('the pane lists every env, mine and the others, each with its open and a tear down', async ($, on) => {
  engine(on, PROJECT, [{ tool_use_id: 'a', tool: 'Edit', input: { file_path: '/p/.worktrees/kio-1/m/x.py' } }])
  await $.session.start({ cwd: PROJECT, surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'true' })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    const texts = (await ui.findAll({ type: 'Text' })).map(text => text.text)
    expect(texts).toEqual(expect.arrayContaining(['kio-1', ':2701 ●', 'wt_kio_1_102026', 'kio-2', ':2702 ○', 'wt_kio_2']))
    expect(await ui.find({ key: 'wts-stop-kio-1' })).toBeDefined()
    expect(await ui.find({ key: 'wts-stop-kio-2' })).toBeDefined()
    expect(await ui.find({ key: 'wts-start' })).toBeDefined()
    if (surface === 'terminal')
      expect((await ui.findAll({ type: 'Link' })).map(link => link.props.href)).toEqual([
        'http://wt_kio_1_102026.localhost:2701/web',
        'http://wt_kio_2.localhost:2702/web',
      ])
    else expect((await ui.find({ key: 'wts-open-kio-2' }))?.props.label).toBe('open')
    await ui.unmount()
  }
  // the phone gets the same list. The start field there is the engine's call, not the pane's: Claude Code
  // up to 2.1.300 puts an empty Box in its place on mobile, a later one sends it to an app that draws fields
  const phone = await $.ui.mount({ ...PANE, surface: 'mobile' })
  expect(await phone.find({ key: 'wts-stop-kio-2' })).toBeDefined()
  expect((await phone.find({ key: 'wts-open-kio-2' }))?.props.label).toBe('open')
  await phone.unmount()
})

test('tear down waits for its confirm, then runs wt-stop.sh on that env from the project', async ($, on) => {
  engine(on, PROJECT)
  await $.session.start({ cwd: PROJECT, surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'true' })
  spawned.length = 0
  script = { stdout: 'stopping kio-2\nwt env kio-2 removed\n', stderr: '', code: 0 }
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'wts-stop-kio-2' })
  expect(spawned).toEqual([])
  // no takes it back
  await ui.press({ key: 'wts-stop-no-kio-2' })
  expect(await ui.find({ key: 'wts-stop-yes-kio-2' })).toBeUndefined()
  await ui.press({ key: 'wts-stop-kio-2' })
  expect(await ui.find({ text: 'tear down? a dirty worktree is kept' })).toBeDefined()
  await ui.press({ key: 'wts-stop-yes-kio-2' })
  expect(spawned).toEqual([{ argv: ['bash', '/p/.claude/skills/worktree-env/scripts/wt-stop.sh', 'kio-2'], cwd: '/p' }])
  // the script's last line is the pane's word, and the env is back to its tear down button
  expect(await ui.find({ text: 'wt env kio-2 removed' })).toBeDefined()
  expect(await ui.find({ key: 'wts-stop-kio-2' })).toBeDefined()
  await ui.unmount()
})

test('the field starts an env by its slug; a failed script shows its last line, a bad slug runs nothing', async ($, on) => {
  engine(on, PROJECT)
  await $.session.start({ cwd: PROJECT, surface: 'terminal', isInteractive: true })
  await $.tool.call({ tool: 'Bash', command: 'true' })
  spawned.length = 0
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    script = { stdout: 'restoring\n', stderr: 'wt-start: the desktop worktree guard refuses .worktrees/\n', code: 4 }
    await ui.input({ key: 'wts-start', text: ' kio-9 ' })
    expect(await ui.find({ text: 'wt-start.sh kio-9 failed: wt-start: the desktop worktree guard refuses .worktrees/' })).toBeDefined()
    await ui.input({ key: 'wts-start', text: 'not a slug' })
    expect(await ui.find({ text: "not a worktree slug: 'not a slug'" })).toBeDefined()
    await ui.unmount()
  }
  expect(spawned).toEqual([
    { argv: ['bash', '/p/.claude/skills/worktree-env/scripts/wt-start.sh', 'kio-9'], cwd: '/p' },
    { argv: ['bash', '/p/.claude/skills/worktree-env/scripts/wt-start.sh', 'kio-9'], cwd: '/p' },
  ])
})
