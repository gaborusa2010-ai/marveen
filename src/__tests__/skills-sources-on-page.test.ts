// String-contract guard for the extra sources on the global Skills page (#635):
// Codex skills, sub-agent definitions, and the per-agent loading view (#638).
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const APP  = readFileSync(join(__dirname, '../../web/app.js'),     'utf-8')
const HTML = readFileSync(join(__dirname, '../../web/index.html'), 'utf-8')
const CSS  = readFileSync(join(__dirname, '../../web/style.css'),  'utf-8')

describe('Skills page: Codex, sub-agents and loading view', () => {
  it('loads both extra lists without failing the page', () => {
    expect(APP).toContain("optional('/api/skills/codex')")
    expect(APP).toContain("optional('/api/skills/subagents')")
    expect(APP).toMatch(/const optional = url => fetch\(url\)\.then\(r => \(r\.ok \? r\.json\(\) : \[\]\)\)\.catch\(\(\) => \[\]\)/)
  })

  it('has filter buttons and the agent select', () => {
    expect(HTML).toContain('data-filter="subagent"')
    expect(HTML).toContain('data-filter="codex"')
    expect(HTML).toContain('id="skillsAgentSelect"')
    expect(APP).toContain("getElementById('skillsAgentSelect')")
  })

  it('agent view leaves out Codex, off skills and other agents’ own skills', () => {
    const fn = APP.slice(APP.indexOf('function skillsSourceItems()'), APP.indexOf('function renderSkillsSidebar()'))
    expect(fn).toContain("if (kind === 'codex') return false")
    expect(fn).toContain("return state !== 'off'")
    expect(fn).toContain("if (kind === 'local') return skill.agentId === agent")
    expect(fn).toContain("skill.global || (skill.agents || []).includes(agent)")
  })

  it('Codex and sub-agent cards are read-only: no detail click', () => {
    expect(APP).toContain("const isStatic = kind === 'subagent' || kind === 'codex'")
    expect(APP).toMatch(/if \(isStatic\) card\.title = t\('skills\.readonly\.title'\)\s*else card\.addEventListener\('click'/)
    expect(CSS).toContain('.skills-card--static')
  })

  it('loading badges have a style for each state', () => {
    for (const s of ['on', 'name-only', 'off']) expect(CSS).toContain(`.skills-load-badge--${s}`)
  })
})
