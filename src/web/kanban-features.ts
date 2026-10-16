// Per-feature on/off switches for the kanban board (owner msg 6475: every
// ClickUp-like addition separately switchable in Settings). One resolver for
// both readers -- the /api/marveen payload the UI hides features by, and the
// write routes that answer 403 -- so the two can never disagree about whether
// a feature is on.
//
// Resolved per call through the settings layer (override > .env > registry
// default), so a toggle takes effect without a restart. Turning a feature off
// never deletes its data: the UI stops showing it and writes are refused.
import { getEffectiveSettingValue } from '../settings-store.js'

export const KANBAN_FEATURE_KEYS = {
  recurring: 'KANBAN_FEATURE_RECURRING',
  checklist: 'KANBAN_FEATURE_CHECKLIST',
  attachments: 'KANBAN_FEATURE_ATTACHMENTS',
  calendar: 'KANBAN_FEATURE_CALENDAR',
  intakeForm: 'KANBAN_FEATURE_INTAKE_FORM',
  map: 'KANBAN_FEATURE_MAP',
} as const

export type KanbanFeature = keyof typeof KANBAN_FEATURE_KEYS

export function kanbanFeatureEnabled(feature: KanbanFeature): boolean {
  let raw: string
  try {
    raw = String(getEffectiveSettingValue(KANBAN_FEATURE_KEYS[feature])).trim().toLowerCase()
  } catch {
    // An unreadable setting falls back to the registry default (on): a broken
    // overrides file must not make existing data look deleted.
    return true
  }
  // The Settings page stores the canonical "1"/"0"; a hand-edited .env may say
  // "false"/"off". Anything else counts as on, matching the default.
  return !['0', 'false', 'off', 'no', ''].includes(raw)
}

/** The body every disabled feature's write route answers with (HTTP 403). */
export const KANBAN_FEATURE_DISABLED_BODY = { error: 'feature disabled' } as const
