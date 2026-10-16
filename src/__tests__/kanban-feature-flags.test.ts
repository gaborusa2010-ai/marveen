// Kanban feature switches (owner msg 6475): six registry entries, default on,
// hot-reloadable, and one resolver shared by /api/marveen and the write routes.
import { describe, it, expect, beforeEach, vi } from 'vitest'

const settings = new Map<string, string>()
vi.mock('../settings-store.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../settings-store.js')>()
  return { ...actual, getEffectiveSettingValue: (k: string) => settings.get(k) ?? actual.getEffectiveSettingValue(k) }
})

import { SETTINGS_REGISTRY, validateSettingValue } from '../config-registry.js'
import { kanbanFeatureEnabled, KANBAN_FEATURE_KEYS } from '../web/kanban-features.js'

beforeEach(() => settings.clear())

describe('kanban feature flags', () => {
  it('registers all six as hot-reload booleans in the kanban module, default on', () => {
    for (const key of Object.values(KANBAN_FEATURE_KEYS)) {
      const def = SETTINGS_REGISTRY.find((d) => d.key === key)
      expect(def, key).toBeDefined()
      expect(def).toMatchObject({ type: 'boolean', default: '1', module: 'kanban', requiresRestart: false, secret: false })
      expect(validateSettingValue(def!, false)).toEqual({ ok: true, value: '0' })
    }
  })

  it('resolves on by default and off for the stored "0" (and hand-written false/off)', () => {
    for (const feature of Object.keys(KANBAN_FEATURE_KEYS) as Array<keyof typeof KANBAN_FEATURE_KEYS>) {
      expect(kanbanFeatureEnabled(feature)).toBe(true)
    }
    settings.set('KANBAN_FEATURE_CHECKLIST', '0')
    settings.set('KANBAN_FEATURE_MAP', 'false')
    settings.set('KANBAN_FEATURE_CALENDAR', 'off')
    expect(kanbanFeatureEnabled('checklist')).toBe(false)
    expect(kanbanFeatureEnabled('map')).toBe(false)
    expect(kanbanFeatureEnabled('calendar')).toBe(false)
    expect(kanbanFeatureEnabled('recurring')).toBe(true)
    settings.set('KANBAN_FEATURE_CHECKLIST', '1')
    expect(kanbanFeatureEnabled('checklist')).toBe(true)
  })
})
