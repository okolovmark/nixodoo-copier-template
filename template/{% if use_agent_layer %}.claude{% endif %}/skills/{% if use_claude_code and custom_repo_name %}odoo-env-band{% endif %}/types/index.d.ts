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
  // this session works in it: drawn in the band; every env, this one included, is listed in the pane
  isMine: boolean
  git: GitStatus | null
}

// a wt-start.sh or wt-stop.sh the pane runs on one env
export type WtJob = { slug: string; action: 'start' | 'stop' }

// the pane's last word on a job: what it is doing, what it did, or the script's last line when it failed
export type PaneNote = { text: string; isError: boolean }

export type EnvSnapshot = {
  // the env repo (the project itself) and the main addons checkout, when this session works in them
  env: { git: GitStatus | null } | null
  addons: { git: GitStatus | null } | null
  services: Service[]
  wts: WtEnv[]
}

declare module 'claude-code' {
  interface PluginState {
    'odoo-env-band': {
      snapshot: EnvSnapshot | null
      touched: Touched
      // the env whose Tear Down waits for its confirm
      armed: string | null
      jobs: WtJob[]
      note: PaneNote | null
    }
  }
}
