import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, Timer } from 'claude-code'

import type { Check } from '../types'
import type { Schedule } from './checks'
import { duePrompt, isSame, timeLeft, toSchedule, upsert, words } from './checks'

const TICK_MS = 30_000
const STORE_KEY = 'checks'
const USAGE = 'usage: /t60 [list] | add <pr> "<deploy ts>" [high|medium|low] [minutes] | now <pr> | cancel <pr>'

const checks = atom({ plugin: 'deploy-watch', key: 'checks' } as const, [])
const now = atom({ plugin: 'deploy-watch', key: 'now' } as const, 0)

// this module's pending timers by `<project>#<pr>`; a reload drops them and session.start re-arms
const timers = new Map<string, Timer>()

function timerKey(check: Pick<Check, 'pr' | 'project'>): string {
  return `${check.project}#${check.pr}`
}

async function stored($: EngineInterface): Promise<Check[]> {
  const value = await $.store.get(STORE_KEY)
  return Array.isArray(value) ? (value as Check[]) : []
}

// the band shows this project's checks; other sessions' writes arrive on the next tick
async function sync($: EngineInterface): Promise<void> {
  const root = await $.session.root()
  const list = (await stored($)).filter(check => check.project === root)
  const at = await $.clock.now()
  await update($, checks, () => list)
  await update($, now, () => at)
}

async function save($: EngineInterface, list: Check[]): Promise<void> {
  await $.store.set(STORE_KEY, list)
  await sync($)
}

async function arm($: EngineInterface, check: Check): Promise<void> {
  if (check.owner !== (await $.session.id())) return
  timers.get(timerKey(check))?.cancel()
  const wait = Math.max(0, check.dueAt - (await $.clock.now()))
  timers.set(
    timerKey(check),
    $.clock.after(wait, () => void fire($, check.pr, false)),
  )
}

async function schedule($: EngineInterface, wanted: Schedule): Promise<string> {
  const check: Check = {
    pr: wanted.pr,
    title: wanted.title,
    since: wanted.since,
    tier: wanted.tier,
    host: wanted.host,
    dueAt: (await $.clock.now()) + wanted.minutes * 60_000,
    project: await $.session.root(),
    owner: await $.session.id(),
  }
  await save($, upsert(await stored($), check))
  await arm($, check)
  return `deploy-watch: T+${wanted.minutes} re-check for PR ${wanted.pr} scheduled, due in ${wanted.minutes}m (INV_SINCE "${wanted.since}", tier ${wanted.tier}, host ${wanted.host}). It survives a restart; the band above the prompt has run-now and cancel; /t60 cancel ${wanted.pr} cancels it.`
}

async function cancel($: EngineInterface, pr: number): Promise<string> {
  const root = await $.session.root()
  const list = await stored($)
  const target = { pr, project: root }
  if (!list.some(check => isSame(check, target))) return `deploy-watch: no pending re-check for PR ${pr}.`
  timers.get(timerKey(target))?.cancel()
  timers.delete(timerKey(target))
  await save($, list.filter(check => !isSame(check, target)))
  $.ui.toast(`T+60 for PR ${pr} cancelled`)
  return `deploy-watch: the re-check for PR ${pr} is cancelled.`
}

// removed from the store first, so a second session or a second press finds nothing to fire
async function fire($: EngineInterface, pr: number, isPress: boolean): Promise<string> {
  const root = await $.session.root()
  const list = await stored($)
  const check = list.find(one => isSame(one, { pr, project: root }))
  timers.get(timerKey({ pr, project: root }))?.cancel()
  timers.delete(timerKey({ pr, project: root }))
  if (check === undefined) return `deploy-watch: no pending re-check for PR ${pr}.`
  await save($, list.filter(one => !isSame(one, check)))
  // a press is the person asking for it; the timer speaks as the plugin
  void $.prompt.submit(isPress ? { text: duePrompt(check), asUser: true } : { text: duePrompt(check) })
  $.ui.toast(`T+60 for PR ${pr}: the re-check prompt is queued`)
  return `deploy-watch: the re-check for PR ${pr} is queued as a prompt.`
}

async function listChecks($: EngineInterface): Promise<string> {
  const pending = await read($, checks)
  if (pending.length === 0) return 'deploy-watch: no pending re-checks in this project.'
  const at = await $.clock.now()
  return pending
    .map(check => `PR ${check.pr} ${check.tier} ${timeLeft(check, at).text} (INV_SINCE "${check.since}", host ${check.host})`)
    .join('\n')
}

async function command($: EngineInterface, args: string): Promise<string> {
  const [verb = 'list', ...rest] = words(args)
  if (verb === 'list') return listChecks($)
  if (verb === 'cancel' || verb === 'now') {
    const pr = Number(rest[0])
    if (!Number.isInteger(pr) || pr <= 0) return USAGE
    return verb === 'cancel' ? cancel($, pr) : fire($, pr, true)
  }
  if (verb === 'add') {
    const wanted = toSchedule({ pr: rest[0], deploy_ts: rest[1], tier: rest[2], delay_minutes: rest[3] })
    return typeof wanted === 'string' ? `deploy-watch: ${wanted}\n${USAGE}` : schedule($, wanted)
  }
  return USAGE
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.tool.register({
      name: 'schedule_check',
      description:
        "Schedules the deploy skill's T+60 invariant re-check (Step 9) instead of a background `sleep 3600`. It is kept outside the session, so it survives a restart; at the due time it queues a prompt in this session to run Step 9, and the band above the prompt shows a countdown with run-now and cancel. Scheduling the same PR again replaces it.",
      inputSchema: {
        type: 'object',
        properties: {
          pr: { type: 'integer', description: 'The deployed PR number' },
          deploy_ts: { type: 'string', description: 'The deploy timestamp passed to the check as INV_SINCE, YYYY-MM-DD HH:MM:SS' },
          title: { type: 'string', description: "The PR title, for the band and the chat message" },
          tier: { type: 'string', enum: ['high', 'medium', 'low'], description: 'Blast radius; default high' },
          host: { type: 'string', description: 'Deploy host alias; default prod' },
          delay_minutes: { type: 'integer', description: 'Minutes until the re-check; default 60' },
        },
        required: ['pr', 'deploy_ts'],
      },
    })
    await $.tool.register({
      name: 'cancel_check',
      description: 'Cancels a pending deploy-watch re-check for a PR.',
      inputSchema: { type: 'object', properties: { pr: { type: 'integer' } }, required: ['pr'] },
    })
    await $.command.register({
      name: 't60',
      description: 'Pending T+60 deploy re-checks: list, add, now, cancel',
      argumentHint: '[list | add <pr> "<deploy ts>" [tier] [minutes] | now <pr> | cancel <pr>]',
    })
    await sync($)
    for (const check of await read($, checks)) await arm($, check)
    $.clock.every(TICK_MS, () => void sync($))
    return started
  })

  on('tool.call', { tool: 'mcp__deploy-watch__schedule_check' }, async ($, e) => {
    const wanted = toSchedule(e as unknown as Record<string, unknown>)
    return { result: typeof wanted === 'string' ? `refused: ${wanted}` : await schedule($, wanted) }
  })

  on('tool.call', { tool: 'mcp__deploy-watch__cancel_check' }, async ($, e) => ({
    result: await cancel($, Number((e as unknown as Record<string, unknown>).pr)),
  }))

  on('command.run', { command: 't60' }, async ($, e) => ({ text: await command($, e.args) }))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const below = await next(e)
    const pending = await read($, checks)
    if (pending.length === 0 || e.props.hasSurvey) return below
    const at = await read($, now)
    const sid = await $.session.id()
    const { Box, Button, Text } = $.ui.resolve(e)

    return (
      <Box flexDirection="column">
        {/* below the plugins beneath, as every band plugin: their tips stay on top */}
        {below}
        {pending.map(check => {
          const left = timeLeft(check, at)
          return (
            <Box key={`t60-${check.pr}`} flexDirection="row" columnGap={1} alignItems="center">
              <Text color={left.isDue ? 'red' : 'cyan'}>T+60</Text>
              <Text bold>{`PR ${check.pr}`}</Text>
              {check.title !== '' && <Text dimColor wrap="truncate-end">{check.title}</Text>}
              <Text dimColor>{`${check.tier} · ${check.host}`}</Text>
              <Text color={left.isDue ? 'red' : undefined}>{left.text}</Text>
              {check.owner !== sid && <Text dimColor>(scheduled in another session)</Text>}
              <Button key={`t60-now-${check.pr}`} label="run now" onPress={() => fire($, check.pr, true)} />
              <Button key={`t60-cancel-${check.pr}`} label="cancel" onPress={() => cancel($, check.pr)} />
            </Box>
          )
        })}
      </Box>
    )
  })
}
