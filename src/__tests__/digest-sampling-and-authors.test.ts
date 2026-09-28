// The daily digest sampled the wrong end of the day, and it dropped the author.
//
// Both defects were found by Anorak on 2026-09-04 night, on Marveen's own log
// entry. The generated summary said "I measured the Hungarian subtitle timing
// (agents/roxy/scripts/...)" and referred to Marveen in the third person: the
// work was Roxy's, the log was Marveen's. Two causes, both here:
//
//  1. SAMPLING. The digest asked for the 50 most recently ACCESSED memories and
//     only then kept the ones created today. That is backwards: the LIMIT lands
//     on `accessed_at`, so it trims a TIME SLICE chosen by what happened to be
//     read lately, not a count of today's records. Measured that night: 12 of
//     the day's 37 memories reached the digest and 10 came from one agent, so
//     the summary was written from one agent's point of view. The words "Cody",
//     "worklog", "KDP" and "Anorak" are absent from the generated text -- the
//     day's two most consequential events never entered the fleet's only daily
//     summary.
//
//  2. AUTHORSHIP. Every line was passed as bare content, so six agents' memories
//     arrived as if one assistant had written them all. Nothing in the prompt
//     said who acted, so the model invented a speaker. Same family as the
//     second-person circular letter: the "I" is created by the surrounding text
//     when the record does not carry its author.
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { getDb, getMemoriesCreatedSince, getMemoriesForChat, initDatabase, saveMemory } from '../db.js'

const CHAT = 'zz-digest-probe'

beforeAll(() => {
  process.env.NODE_ENV = 'test'
  initDatabase(':memory:')
})

beforeEach(() => {
  getDb().prepare('DELETE FROM memories WHERE chat_id = ?').run(CHAT)
})

/**
 * Writes a memory with an explicit author and forced timestamps.
 *
 * It inserts directly rather than calling saveMemory(), and that is itself a
 * finding: saveMemory(chatId, content, sector, topicKey) has NO agent_id
 * parameter, so every memory it writes lands on the column DEFAULT ('marveen').
 * Only the /api/memories POST path sets a real author. Measured 2026-09-04
 * while writing this test, after the first version failed with
 * "expected 'marveen' to be 'forgacs'".
 *
 * The consequence for the digest fix: tagging each line with `agent_id` is
 * correct and necessary, but it only carries real information for memories
 * written through the API. Chat-derived memories all claim to be Marveen's
 * whoever produced them, and no amount of prompt wording can recover an author
 * the row never stored.
 */
function mem(agent: string, content: string, createdAgo: number, accessedAgo: number) {
  const now = Math.floor(Date.now() / 1000)
  getDb()
    .prepare(
      'INSERT INTO memories (chat_id, content, sector, salience, created_at, accessed_at, agent_id, category) ' +
        "VALUES (?, ?, 'episodic', 1.0, ?, ?, ?, 'warm')"
    )
    .run(CHAT, content, now - createdAgo, now - accessedAgo, agent)
}

describe('daily digest sampling', () => {
  it('takes TODAY, not the recently-read slice', () => {
    // Three memories from today, never re-read (accessed long ago), and three
    // stale ones that were read a minute ago. The old code, limited to 3, would
    // have returned the three STALE ones and then filtered them all away.
    mem('cody', 'ma, de regen olvasva 1', 3600, 80000)
    mem('cody', 'ma, de regen olvasva 2', 3600, 80000)
    mem('cody', 'ma, de regen olvasva 3', 3600, 80000)
    mem('roxy', 'tegnapelotti, most olvasva 1', 200000, 60)
    mem('roxy', 'tegnapelotti, most olvasva 2', 200000, 60)
    mem('roxy', 'tegnapelotti, most olvasva 3', 200000, 60)

    const oneDayAgo = Math.floor(Date.now() / 1000) - 86400

    // The old shape, reproduced here so the difference is visible rather than
    // asserted from memory.
    const regi = getMemoriesForChat(CHAT, 3).filter((m) => m.created_at >= oneDayAgo)
    expect(regi).toHaveLength(0)

    const uj = getMemoriesCreatedSince(CHAT, oneDayAgo, 3)
    expect(uj).toHaveLength(3)
    expect(uj.every((m) => m.agent_id === 'cody')).toBe(true)
  })

  it('does not let one agent crowd out the others when the window fits', () => {
    // The real failure was proportional, not absolute: 10 of 12 sampled rows
    // came from one agent because that agent's memories had been read most
    // recently. With created_at sampling every agent's same-day rows are in.
    for (let i = 0; i < 8; i++) mem('roxy', `roxy ${i}`, 1000, 10)
    mem('cody', 'cody egyetlen sora', 2000, 90000)
    mem('anorak', 'anorak egyetlen sora', 2000, 90000)

    const oneDayAgo = Math.floor(Date.now() / 1000) - 86400
    const uj = getMemoriesCreatedSince(CHAT, oneDayAgo, 200)
    const szerzok = new Set(uj.map((m) => m.agent_id))
    expect(szerzok.has('cody')).toBe(true)
    expect(szerzok.has('anorak')).toBe(true)
    expect(uj).toHaveLength(10)
  })

  it('every row carries an agent_id, so the prompt can name who acted', () => {
    mem('forgacs', 'szabaslista kesz', 100, 100)
    const rows = getMemoriesCreatedSince(CHAT, Math.floor(Date.now() / 1000) - 86400, 10)
    expect(rows[0].agent_id).toBe('forgacs')
    // The digest builds "- [<agent>] <content>"; an empty author would silently
    // become an anonymous line again, which is exactly the defect.
    expect(rows[0].agent_id).not.toBe('')
  })

  it('saveMemory() stores NO author, so chat-derived rows all read as marveen', () => {
    // Not a wish, a boundary: this is what the digest fix can and cannot reach.
    // If saveMemory ever gains an agent_id parameter, this test goes red and
    // whoever changed it will find the digest note attached to the failure.
    saveMemory(CHAT, 'chatbol szarmazo emlek', 'episodic')
    const rows = getMemoriesCreatedSince(CHAT, Math.floor(Date.now() / 1000) - 86400, 10)
    expect(rows[0].agent_id).toBe('marveen')
  })
})
