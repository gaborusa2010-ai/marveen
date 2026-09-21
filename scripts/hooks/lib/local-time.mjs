// One owner for "what time is it" in the hook logs.
//
// LOCALTZ901 (owner, 2026-09-20 10:22 BST, via Forgacs' channel, msg 632, verbatim:
// "De ezt mar elmondtam a Marveennak,hogy mindig a Londoni idot hasznaljatok!!! Miert
// van 2 kulonbozo ido?"). He is right, and it had already cost something that morning:
// a log line in UTC and a file mtime in BST were compared inside a single sentence, and
// the conclusion -- that another agent's measurement did not count -- was exactly
// backwards. 59 minutes before turned out to be 66 seconds after.
//
// Where it came from: three hooks each called `new Date().toISOString()`, which returns
// UTC by definition, Z-suffixed, regardless of the machine's zone. Everything else the
// owner sees is London time: the daily log, the worklog, our messages. Two scales, one
// pair of eyes. (Located by Forgacs, three call sites, 2026-09-20.)
//
// WHY THE OFFSET AND NOT A BARE LOCAL TIME. Switching to plain local time would leave
// both logs MIXED: UTC before today, London after, with nothing on the line to say
// which. Anyone reading backwards falls into the same trap. An explicit offset
// (`+01:00` instead of `Z`) carries the answer in the line itself, so an old Z-line and
// a new +01:00 line are both unambiguous and directly comparable. Forgacs proposed this
// over a separator line, and the reason is that it needs no knowledge of when we
// switched.
//
// WHAT THIS DOES NOT DO: it does not read SCHEDULER_TZ. The hooks are standalone .mjs
// files that must run without the build, so they use the process's own zone -- which is
// Europe/London on this machine, the same value SCHEDULER_TZ carries. If the two ever
// disagree, this comment is where to start, and the offset in the line is what makes
// the disagreement visible instead of silent.
export function localStamp(d = new Date()) {
  const p = (n, w = 2) => String(Math.abs(n)).padStart(w, '0')
  // getTimezoneOffset() is minutes BEHIND UTC, so its sign is inverted from the one an
  // ISO offset uses: London in summer reports -60 and must print as +01:00.
  const off = -d.getTimezoneOffset()
  const sign = off < 0 ? '-' : '+'
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
    `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}` +
    `${sign}${p(Math.floor(Math.abs(off) / 60))}:${p(Math.abs(off) % 60)}`
  )
}
