// One scheduled re-check, kept in $.store so it outlives the session that scheduled it.
export type Check = {
  pr: number
  title: string
  // the deploy's INV_SINCE, passed back verbatim so the window covers deploy -> now
  since: string
  tier: string
  host: string
  dueAt: number
  // the session root it was scheduled from: other projects never see it
  project: string
  // only the scheduling session fires it on its own; any other session shows it with run-now
  owner: string
}

declare module 'claude-code' {
  interface PluginState {
    'deploy-watch': { checks: Check[]; now: number }
  }
}
