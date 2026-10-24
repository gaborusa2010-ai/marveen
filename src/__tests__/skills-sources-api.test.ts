// Functional test for the read-only Skills-page sources (#635): Codex CLI
// skills, sub-agent definitions, and the per-agent loading state that
// scripts/skill-valasztas.py applies as skillOverrides (#638). Calls the real
// handler against an mkdtemp fixture; HOME, CODEX_HOME, PROJECT_ROOT and the
// agent tree are all redirected, so nothing touches the live fleet.
import { describe, it, expect, afterAll, vi } from 'vitest'
import { mkdirSync, rmSync, writeFileSync, mkdtempSync, existsSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import type { RouteContext } from '../web/routes/types.js'

const tmpRoot = mkdtempSync(join(tmpdir(), 'skills-sources-'))
const HOME_TMP = join(tmpRoot, 'home')
const PROJECT_TMP = join(tmpRoot, 'project')
const AGENTS_TMP = join(PROJECT_TMP, 'agents')
const CODEX_TMP = join(tmpRoot, 'codex')
process.env.CODEX_HOME = CODEX_TMP

vi.mock('node:os', async (orig) => {
  const actual = await orig<typeof import('node:os')>()
  return { ...actual, homedir: () => HOME_TMP }
})
vi.mock('../config.js', async (orig) => {
  const actual = await orig<typeof import('../config.js')>()
  return { ...actual, PROJECT_ROOT: PROJECT_TMP }
})
vi.mock('../web/agent-config.js', async (orig) => {
  const actual = await orig<typeof import('../web/agent-config.js')>()
  return {
    ...actual,
    AGENTS_BASE_DIR: AGENTS_TMP,
    agentDir: (name: string) => join(AGENTS_TMP, name),
    listAgentNames: () =>
      existsSync(AGENTS_TMP)
        ? readdirSync(AGENTS_TMP).filter((f) => statSync(join(AGENTS_TMP, f)).isDirectory())
        : [],
  }
})

const { tryHandleSkills } = await import('../web/routes/skills.js')
const { MAIN_AGENT_ID } = await import('../config.js')

async function get(path: string): Promise<{ status: number; body: any }> {
  const out: { status: number; body: any } = { status: 0, body: null }
  const res: any = {
    writeHead(status: number) { out.status = status; return res },
    end(chunk?: string) { if (chunk) out.body = JSON.parse(chunk) },
  }
  const url = new URL(`http://localhost:3420${path}`)
  const handled = await tryHandleSkills({ req: {} as any, res, path: url.pathname, method: 'GET', url } as RouteContext)
  expect(handled).toBe(true)
  return out
}

function write(path: string, content: string): void {
  mkdirSync(join(path, '..'), { recursive: true })
  writeFileSync(path, content)
}

const skillMd = (desc: string) => `---\nname: x\ndescription: ${desc}\n---\n# X\n`
const agentMd = (name: string, desc: string, extra = '') => `---\nname: ${name}\ndescription: ${desc}\ntools: WebFetch\nmodel: haiku\n---\n# ${name}\n${extra}`

// Fleet: the main agent plus alpha (overrides), beta (no settings file), gamma (broken JSON).
write(join(HOME_TMP, '.claude', 'skills', 'shared-a', 'SKILL.md'), skillMd('Shared A'))
write(join(HOME_TMP, '.claude', 'skills', 'shared-b', 'SKILL.md'), skillMd('Shared B'))
write(join(PROJECT_TMP, '.claude', 'skills', 'root-skill', 'SKILL.md'), skillMd('Repo root skill'))
write(join(PROJECT_TMP, '.claude', 'settings.local.json'), JSON.stringify({ skillOverrides: { 'shared-a': 'on', 'shared-b': 'off', 'root-skill': 'name-only' } }))
write(join(AGENTS_TMP, 'alpha', '.claude', 'settings.local.json'), JSON.stringify({ skillOverrides: { 'shared-a': 'name-only', 'shared-b': 'off', 'root-skill': 'off' } }))
write(join(AGENTS_TMP, 'alpha', '.claude', 'skills', 'alpha-own', 'SKILL.md'), skillMd('Alpha own'))
mkdirSync(join(AGENTS_TMP, 'beta'), { recursive: true })
write(join(AGENTS_TMP, 'gamma', '.claude', 'settings.local.json'), '{not json')

write(join(CODEX_TMP, 'skills', 'codex-user', 'SKILL.md'), skillMd('Codex user skill'))
write(join(CODEX_TMP, 'skills', '.system', 'codex-sys', 'SKILL.md'), skillMd('Codex system skill'))
mkdirSync(join(CODEX_TMP, 'skills', 'no-skill-md'), { recursive: true })

write(join(HOME_TMP, '.claude', 'agents', 'global-helper.md'), agentMd('global-helper', 'Global helper'))
write(join(PROJECT_TMP, '.claude', 'agents', 'reader.md'), agentMd('reader', 'Reader'))
write(join(AGENTS_TMP, 'alpha', '.claude', 'agents', 'reader.md'), agentMd('reader', 'Reader'))
write(join(AGENTS_TMP, 'beta', '.claude', 'agents', 'reader.md'), agentMd('reader', 'Reader', 'drifted copy\n'))
write(join(AGENTS_TMP, 'beta', '.claude', 'agents', 'reader.md.bak-20261023'), agentMd('reader', 'Old backup'))

afterAll(() => { rmSync(tmpRoot, { recursive: true, force: true }) })

describe('GET /api/skills loading state (#635)', () => {
  it('reports each agent by the skillOverrides it actually has', async () => {
    const { status, body } = await get('/api/skills')
    expect(status).toBe(200)
    const a = body.find((s: any) => s.name === 'shared-a')
    // beta has no settings file and gamma's is broken: both fall back to the default, listed in full.
    expect(a.loading).toEqual({ on: expect.arrayContaining([MAIN_AGENT_ID, 'beta', 'gamma']), nameOnly: ['alpha'], off: [] })
    expect(a.loading.on).toHaveLength(3)
    const b = body.find((s: any) => s.name === 'shared-b')
    expect(b.loading.off.sort()).toEqual(['alpha', MAIN_AGENT_ID].sort())
    expect(b.loading.on.sort()).toEqual(['beta', 'gamma'])
  })

  it('gives the repo-root skills a loading state and leaves a sub-agent\'s own skills without one', async () => {
    const { body } = await get('/api/skills/local')
    const root = body.find((s: any) => s.name === 'root-skill')
    expect(root.agentId).toBe(MAIN_AGENT_ID)
    expect(root.loading.nameOnly).toEqual([MAIN_AGENT_ID])
    expect(root.loading.off).toEqual(['alpha'])
    const own = body.find((s: any) => s.name === 'alpha-own')
    expect(own.agentId).toBe('alpha')
    expect(own.loading).toBeUndefined()
  })
})

describe('GET /api/skills/codex', () => {
  it('lists user and .system Codex skills, user first, and skips folders without SKILL.md', async () => {
    const { status, body } = await get('/api/skills/codex')
    expect(status).toBe(200)
    expect(body.map((s: any) => [s.name, s.system, s.source])).toEqual([
      ['codex-user', false, 'codex'],
      ['codex-sys', true, 'codex'],
    ])
    expect(body[0].description).toBe('Codex user skill')
  })

  it('answers an empty list when there is no Codex install', async () => {
    const saved = process.env.CODEX_HOME
    process.env.CODEX_HOME = join(tmpRoot, 'missing-codex')
    try {
      const { status, body } = await get('/api/skills/codex')
      expect(status).toBe(200)
      expect(body).toEqual([])
    } finally {
      process.env.CODEX_HOME = saved
    }
  })
})

describe('GET /api/skills/subagents', () => {
  it('groups definitions by name, marks the global ones, and counts differing copies', async () => {
    const { status, body } = await get('/api/skills/subagents')
    expect(status).toBe(200)
    expect(body.map((s: any) => s.name)).toEqual(['global-helper', 'reader'])
    const g = body[0]
    expect(g).toMatchObject({ global: true, agents: [], variants: 1, tools: 'WebFetch', model: 'haiku', description: 'Global helper' })
    const r = body[1]
    expect(r.global).toBe(false)
    expect(r.agents.sort()).toEqual(['alpha', 'beta', MAIN_AGENT_ID].sort())
    // main and alpha are identical, beta drifted; the .bak file is not a definition.
    expect(r.variants).toBe(2)
  })
})
