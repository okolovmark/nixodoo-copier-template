export const MASK = '«redacted»'

// Values that are dev placeholders, not secrets: the local env's odoo/odoo and admin/admin, test fixtures.
// Masking them would only break an Edit whose old_string quotes them.
const PLACEHOLDER = /^(?:admin|demo|odoo|test|testing|password|passwd|secret|changeme|example|dummy|x+|\*+|•+|«redacted»|false|true|none|null|undefined)$/i
// `password = self.password`, `token = vals.get` read as code, never as a value
const DOTTED_NAME = /^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$/

const KEY_WORDS = 'PASSWORD|PASSWD|PASS|SECRET|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIALS?'
const LOWER_KEY_WORDS = 'password|passwd|secret|api_?key|apikey|access_?token|refresh_?token|client_secret|token'

type Rule = { pattern: RegExp; mask: (...groups: string[]) => string | null }

// $VAR, ${VAR}, %(var)s, {{ var }}: a reference to a secret, not the secret
const REFERENCE = /^[$%{]/

function value(prefix: string, quote: string, secret: string): string | null {
  if (PLACEHOLDER.test(secret) || DOTTED_NAME.test(secret) || REFERENCE.test(secret)) return null
  return `${prefix}${quote}${MASK}${quote}`
}

const RULES: Rule[] = [
  // PEM private keys, whole block
  {
    pattern: /-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----[\s\S]*?-----END \1PRIVATE KEY-----/g,
    mask: (_all, kind) => `-----BEGIN ${kind}PRIVATE KEY-----\n${MASK}\n-----END ${kind}PRIVATE KEY-----`,
  },
  // tokens with a known shape: the prefix stays so it is clear what was there
  { pattern: /\b(gh[pousr]_)[A-Za-z0-9]{30,}\b/g, mask: (_all, p) => `${p}${MASK}` },
  { pattern: /\b(github_pat_)[A-Za-z0-9_]{40,}\b/g, mask: (_all, p) => `${p}${MASK}` },
  { pattern: /\b(sk-ant-[a-z0-9]+-)[A-Za-z0-9_-]{20,}/g, mask: (_all, p) => `${p}${MASK}` },
  { pattern: /\b(xox[abposr]-)[A-Za-z0-9-]{10,}/g, mask: (_all, p) => `${p}${MASK}` },
  { pattern: /\b(AKIA|ASIA)[0-9A-Z]{16}\b/g, mask: (_all, p) => `${p}${MASK}` },
  { pattern: /\b(AIza)[0-9A-Za-z_-]{35}\b/g, mask: (_all, p) => `${p}${MASK}` },
  { pattern: /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g, mask: () => MASK },
  // scheme://user:password@host
  {
    pattern: /\b([a-z][a-z0-9+.-]*:\/\/[^\s:@/]+:)([^\s@/]+)(@)/gi,
    mask: (_all, head, secret, at) => (PLACEHOLDER.test(secret ?? '') ? null : `${head}${MASK}${at}`),
  },
  // Authorization: Bearer <token>
  {
    pattern: /\b(Authorization:\s*(?:Bearer|Basic|Token|token)\s+)([A-Za-z0-9._~+/=-]{8,})/g,
    mask: (_all, head) => `${head}${MASK}`,
  },
  // .env and shell: ODOO_PASSWORD_PROD=..., export API_KEY="..."
  {
    pattern: new RegExp(`^(\\s*(?:export\\s+)?[A-Z0-9_]*(?:${KEY_WORDS})[A-Z0-9_]*\\s*=\\s*)(["']?)([^\\s"'$][^\\s"']{2,})\\2`, 'gm'),
    mask: (_all, prefix, quote, secret) => value(prefix ?? '', quote ?? '', secret ?? ''),
  },
  // odoo.conf / ini / yaml: admin_passwd = ..., smtp_password: ...; never a call, a container or a comment
  {
    pattern: new RegExp(`^(\\s*[a-z_]*(?:${LOWER_KEY_WORDS})[a-z_]*\\s*[=:]\\s*)()([^\\s#;'"(){}\\[\\],]{3,})[ \\t]*$`, 'gmi'),
    mask: (_all, prefix, quote, secret) => value(prefix ?? '', quote ?? '', secret ?? ''),
  },
  // JSON and Python dicts: "password": "...", 'api_key': '...'
  {
    pattern: new RegExp(`(["'][a-z_]*(?:${LOWER_KEY_WORDS})["']\\s*[:=]\\s*)(["'])([^"'\\s]{3,})\\2`, 'gi'),
    mask: (_all, prefix, quote, secret) => value(prefix ?? '', quote ?? '', secret ?? ''),
  },
]

export function redactText(text: string): { text: string; count: number } {
  let count = 0
  let out = text
  for (const rule of RULES) {
    out = out.replace(rule.pattern, (...args: unknown[]) => {
      // (match, ...groups, offset, input): an unmatched group reads as ''
      const all = String(args[0])
      const groups = args.slice(1, -2).map(arg => (typeof arg === 'string' ? arg : ''))
      const masked = rule.mask(all, ...groups)
      if (masked === null || masked === all) return all
      count += 1
      return masked
    })
  }
  return { text: out, count }
}

// every string inside a tool's record, keys left alone
export function redactValue(input: unknown): { value: unknown; count: number } {
  if (typeof input === 'string') {
    const done = redactText(input)
    return { value: done.text, count: done.count }
  }
  if (Array.isArray(input)) {
    let count = 0
    const value = input.map(item => {
      const done = redactValue(item)
      count += done.count
      return done.value
    })
    return { value, count }
  }
  if (typeof input === 'object' && input !== null) {
    let count = 0
    const value = Object.fromEntries(
      Object.entries(input).map(([key, item]) => {
        const done = redactValue(item)
        count += done.count
        return [key, done.value]
      }),
    )
    return { value, count }
  }
  return { value: input, count: 0 }
}
