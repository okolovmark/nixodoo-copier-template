export type GitStatus = {
  branch: string
  dirty: number
  ahead: number
  behind: number
  hasUpstream: boolean
}

// the checkouts this session works in: by its cwd, its edits and its git / wt-*.sh commands
export type Touched = { env: boolean; addons: boolean; wts: string[] }

export type Service = { name: string; port: string; isActive: boolean }

export type WtEnv = {
  slug: string
  port: string
  db: string
  isActive: boolean
  // this session works in it; only these are drawn, the rest are a count
  isMine: boolean
  git: GitStatus | null
}

export type EnvSnapshot = {
  // the env repo (the project itself) and the main addons checkout, when this session works in them
  env: { git: GitStatus | null } | null
  addons: { git: GitStatus | null } | null
  services: Service[]
  wts: WtEnv[]
}

declare module 'claude-code' {
  interface PluginState {
    'odoo-env-band': { snapshot: EnvSnapshot | null; touched: Touched }
  }
}
