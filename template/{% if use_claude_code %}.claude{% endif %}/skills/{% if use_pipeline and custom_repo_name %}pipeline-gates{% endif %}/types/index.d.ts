export type GateName = 'status' | 'grill' | 'qc'

// how a gate opened (or why the QC gate stays shut), and when
export type Gate = { isOpen: boolean; how: string; at: number }

// one task of the block a run works through, and where it stands; the agent moves it
export type TaskState = 'pending' | 'active' | 'done'
export type BlockTask = { key: string; title: string; state: TaskState; at: number }

// One pipeline run. The seq fields order what happened: a QC pass counts only when the review and
// the green test run it rests on came after the last code change, and it holds only until the next.
export type Run = {
  task: string
  startedAt: number
  phase: number
  phaseAt: number[]
  status: Gate | null
  grill: Gate | null
  qc: Gate | null
  qcSeq: number
  seq: number
  lastChange: number
  lastReview: number
  lastGreen: number
  lastTests: string
  blocked: { what: string; at: number }[]
  // the block of tasks this run works through, one run for all of them (absent in a run stored earlier)
  tasks?: BlockTask[]
  // the grill dialog was declined or went unanswered: it asks again only after the person's next message
  // (absent in a run stored before the dialog existed)
  isAskMuted?: boolean
  isDone: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'pipeline-gates': { run: Run | null }
  }
}
