import { describe, expect, mock, test } from 'claude-code/testing'

import { MASK, redactText, redactValue } from '../hooks/redact'

// built at run time so no scanner mistakes the fixtures for live tokens
const GH = `ghp_${'a1B2'.repeat(9)}`
const JWT = `eyJ${'x'.repeat(12)}.eyJ${'y'.repeat(12)}.${'z'.repeat(12)}`

describe('masked', () => {
  test('env lines, conf lines, JSON, URLs, headers, tokens, keys', async () => {
    const cases: [string, string][] = [
      ['ODOO_PASSWORD_PROD=s3cr3t-Value', `ODOO_PASSWORD_PROD=${MASK}`],
      ['export API_KEY="abc.def-123"', `export API_KEY="${MASK}"`],
      ['admin_passwd = Zx9!kq', `admin_passwd = ${MASK}`],
      ['smtp_password: hunter22', `smtp_password: ${MASK}`],
      ['{"password": "p4ss-word"}', `{"password": "${MASK}"}`],
      ["vals = {'api_key': 'k-123456'}", `vals = {'api_key': '${MASK}'}`],
      ['postgres://odoo_prod:Pg-pass1@db:5432/odoo', `postgres://odoo_prod:${MASK}@db:5432/odoo`],
      ['Authorization: Bearer abcdefgh12345', `Authorization: Bearer ${MASK}`],
      [`token ${GH} end`, `token ghp_${MASK} end`],
      [`jwt ${JWT}`, `jwt ${MASK}`],
      ['-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----', `-----BEGIN OPENSSH PRIVATE KEY-----\n${MASK}\n-----END OPENSSH PRIVATE KEY-----`],
    ]
    for (const [input, output] of cases) expect(redactText(input).text).toBe(output)
  })
})

describe('left alone', () => {
  test('code, dev placeholders, references and prose', async () => {
    const kept = [
      'password = fields.Char("Password", required=True)',
      'db_password = self.db_password',
      'token = vals.get("token")',
      'PGPASSWORD=odoo',
      "self.env['res.users'].create({'login': 'demo', 'password': 'demo'})",
      'postgres://odoo:odoo@localhost:16432/develop',
      'ODOO_PASSWORD=$ODOO_PASSWORD_PROD',
      'The password field is required.',
      'admin_passwd = admin',
    ]
    for (const line of kept) expect(redactText(line)).toEqual({ text: line, count: 0 })
  })

  test('a record keeps its keys and its non-string values', async () => {
    const done = redactValue({ stdout: 'X_TOKEN=abcdef123', code: 0, lines: ['ok', 'SECRET_KEY=zzz999'] })
    expect(done).toEqual({ value: { stdout: `X_TOKEN=${MASK}`, code: 0, lines: ['ok', `SECRET_KEY=${MASK}`] }, count: 2 })
  })
})

test('a tool record is rewritten only when it held a secret, and /redact off stops it', async ($, on) => {
  mock.clock(on)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.toast', () => ({ value: undefined }))
  on('tool.call', (_$, e) =>
    e.tool === 'Bash' && e.command === 'cat .env'
      ? { result: { stdout: 'ODOO_PASSWORD_PROD=s3cr3t-Value\nODOO_URL=x', stderr: '', interrupted: false }, ref: 1 }
      : { result: { stdout: 'nothing here', stderr: '', interrupted: false }, ref: 2 },
  )
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })

  const secret = await $.tool.call({ tool: 'Bash', command: 'cat .env' })
  expect(secret.result).toEqual({ stdout: `ODOO_PASSWORD_PROD=${MASK}\nODOO_URL=x`, stderr: '', interrupted: false })
  expect(secret.ref).toBeUndefined()

  const plain = await $.tool.call({ tool: 'Bash', command: 'ls' })
  expect(plain.ref).toBe(2)

  const typed = { origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 } } as const
  const off = await $.command.run({ command: 'redact', args: 'off', ...typed })
  expect(off.text).toContain('OFF for this session; 1 secret masked')
  const raw = await $.tool.call({ tool: 'Bash', command: 'cat .env' })
  expect(raw.ref).toBe(1)
})

test('only the person turns masking off; anyone turns it back on', async ($, on) => {
  mock.clock(on)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.toast', () => ({ value: undefined }))
  on('tool.call', () => ({ result: { stdout: 'X_TOKEN=abcdef123', stderr: '', interrupted: false }, ref: 1 }))
  await $.session.start({ cwd: '/p', surface: 'terminal', isInteractive: true })
  const presentation = { isFullscreen: true, columns: 160 } as const

  const byPeer = await $.command.run({ command: 'redact', args: 'off', origin: { kind: 'peer' }, presentation })
  expect(byPeer.text).toContain('only the person')
  expect((await $.tool.call({ tool: 'Bash', command: 'env' })).ref).toBeUndefined()

  const byYou = await $.command.run({ command: 'redact', args: 'off', origin: { kind: 'composer' }, presentation })
  expect(byYou.text).toContain('OFF for this session')
  expect((await $.tool.call({ tool: 'Bash', command: 'env' })).ref).toBe(1)

  const onByPlugin = await $.command.run({
    command: 'redact',
    args: 'on',
    origin: { kind: 'plugin', name: 'other' },
    presentation,
  })
  expect(onByPlugin.text).toContain('redact: on;')
  expect((await $.tool.call({ tool: 'Bash', command: 'env' })).ref).toBeUndefined()
})
