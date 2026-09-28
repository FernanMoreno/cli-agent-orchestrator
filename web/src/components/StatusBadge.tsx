// StatusBadge — terminal status pill for the web dashboard.
//
// STATUS_CONFIG / UNKNOWN_CONFIG are generated from the shared design-token SSOT
// (design-tokens/status.json + tokens.json) via `node design-tokens/gen.mjs`.
// Do not hand-edit the status taxonomy here — edit the JSON and regenerate.
import { STATUS_CONFIG, UNKNOWN_CONFIG } from '../status.generated'
import type { StatusStyle } from '../status.generated'
import type { WorkView } from '../api'
import { workStatusSemantics, type WorkSemanticRole } from '../work-status.generated'

export { STATUS_CONFIG }

type TerminalStatus =
  | 'IDLE'
  | 'PROCESSING'
  | 'COMPLETED'
  | 'WAITING_USER_ANSWER'
  | 'WAITING_QUOTA'
  | 'ERROR'
  | string
  | null

const WORK_ROLE_CONFIG: Record<WorkSemanticRole, StatusStyle> = {
  info: STATUS_CONFIG.PROCESSING,
  accent: STATUS_CONFIG.COMPLETED,
  warning: STATUS_CONFIG.WAITING_USER_ANSWER,
  danger: STATUS_CONFIG.ERROR,
  neutral: STATUS_CONFIG.STOPPED,
}

export function StatusBadge({ status, workView }: { status: TerminalStatus; workView?: WorkView | null }) {
  const normalized = status ? status.toUpperCase() : null
  const workSemantics = workView ? workStatusSemantics(workView.work_state) : undefined
  // Work text and color role come from generated Work semantics. The legacy
  // status table supplies only the client's palette for each semantic role.
  const workStyle = workSemantics && WORK_ROLE_CONFIG[workSemantics.semanticRole]
  const config = workView
    ? workSemantics && workStyle
      ? { ...workStyle, label: workSemantics.label, pulse: false }
      : UNKNOWN_CONFIG
    : (normalized && STATUS_CONFIG[normalized]) || UNKNOWN_CONFIG

  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${config.bgClass}`}>
      <span className={`w-2 h-2 rounded-full ${config.dotClass} ${config.pulse ? 'animate-pulse' : ''}`} />
      <span className={`text-xs font-medium ${config.textClass}`}>{config.label}</span>
    </span>
  )
}
