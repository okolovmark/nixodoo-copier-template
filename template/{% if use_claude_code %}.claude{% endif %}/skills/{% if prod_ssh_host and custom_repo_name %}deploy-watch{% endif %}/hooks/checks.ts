import type { Check } from '../types'

export type Schedule = { pr: number; since: string; title: string; tier: string; host: string; minutes: number }

// Every field that reaches the due prompt is checked to the end: the prompt is read by the model,
// so nothing free-form (the PR title included) goes into it.
const TIERS = ['high', 'medium', 'low']
const SINCE = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/
const HOST = /^[A-Za-z0-9._-]{1,64}$/

// the tool's and the command's input, checked; a string is the refusal
export function toSchedule(input: Record<string, unknown>): Schedule | string {
  const pr = Number(input.pr)
  if (!Number.isInteger(pr) || pr <= 0) return 'pr must be a positive integer'
  const since = typeof input.deploy_ts === 'string' ? input.deploy_ts.trim() : ''
  if (!SINCE.test(since)) return 'deploy_ts must be the deploy timestamp, YYYY-MM-DD HH:MM[:SS] (INV_SINCE of Step 6.5)'
  const tier = typeof input.tier === 'string' && input.tier !== '' ? input.tier : 'high'
  if (!TIERS.includes(tier)) return 'tier must be high, medium or low'
  const minutes = input.delay_minutes === undefined ? 60 : Number(input.delay_minutes)
  if (!Number.isFinite(minutes) || minutes < 1 || minutes > 1440) return 'delay_minutes must be between 1 and 1440'
  const host = typeof input.host === 'string' && input.host !== '' ? input.host : 'prod'
  if (!HOST.test(host)) return 'host must be an ssh alias: letters, digits, dot, dash, underscore'
  // drawn in the band as text, never put in the prompt
  const title = typeof input.title === 'string' ? input.title.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 120) : ''
  return { pr, since, tier, host, title, minutes }
}

export function isSame(a: Pick<Check, 'pr' | 'project'>, b: Pick<Check, 'pr' | 'project'>): boolean {
  return a.pr === b.pr && a.project === b.project
}

// one pending check per PR and project: scheduling again replaces it
export function upsert(checks: readonly Check[], check: Check): Check[] {
  return [...checks.filter(one => !isSame(one, check)), check].sort((a, b) => a.dueAt - b.dueAt)
}

export function timeLeft(check: Check, now: number): { text: string; isDue: boolean } {
  const minutes = Math.round((check.dueAt - now) / 60_000)
  if (minutes > 0) return { text: `in ${minutes}m`, isDue: false }
  return { text: minutes === 0 ? 'due now' : `overdue ${-minutes}m`, isDue: true }
}

export function duePrompt(check: Check): string {
  return [
    `deploy-watch: the T+60 re-check is due for PR ${check.pr}`,
    `(deployed ${check.since}, blast radius ${check.tier}, host ${check.host}).`,
    'Run Step 9 of the deploy skill now: the inventory-analysis refresh, then the invariant check with',
    `INV_SINCE="${check.since}" and INV_TIER=${check.tier} against ${check.host}, and post the result to the chat as Step 9 says.`,
  ].join(' ')
}

// `/t60 add 1278 "2026-10-06 14:53:46" high 60`: words, double quotes group
export function words(args: string): string[] {
  return [...args.matchAll(/"([^"]*)"|(\S+)/g)].map(match => match[1] ?? match[2] ?? '')
}
