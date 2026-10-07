import type { EngineInterface, Register } from 'claude-code'

// pr-gate (any host: Claude, Codex, Cursor, a CLI) writes a fix request to
// <home>/inbox/<key>.<sha8>.md with scripts/handoff.py. This mod runs in the
// owner's session: it asks gh which PR the session's branch has, picks up the
// files for that PR, marks each `.taken` and starts a turn with it once the
// session is idle. It also shows the PR's pr-gate round in the status line.

const COMMAND = 'pr-gate-inbox'
const TICK_MS = 60_000
const PR_EVERY_TICKS = 10 // re-ask gh every 10 min: the branch or its PR may change

type Pr = { repo: string; number: number; key: string }
type Round = { verdict?: string }

// Same as scripts/findings.py key(): owner__repo__N, unsafe chars as "_".
export const keyOf = (repo: string, n: number): string =>
  repo.replace('/', '__').replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') + `__${n}`

const homeOf = async ($: EngineInterface): Promise<string> => {
  const own = await $.env.get('PR_GATE_HOME')
  if (own) return own
  const user = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? '.'
  return `${user}/.pr-gate`
}

const findPr = async ($: EngineInterface): Promise<Pr | undefined> => {
  const ran = await $.process
    .run(['gh', 'pr', 'view', '--json', 'number,url'], { timeoutMs: 20_000 })
    .catch(() => undefined)
  if (!ran || ran.exitCode !== 0) return undefined
  try {
    const { number, url } = JSON.parse(ran.stdout) as { number: number; url: string }
    const repo = /github\.com\/([^/]+\/[^/]+)\/pull\/\d+/.exec(url)?.[1]
    return repo ? { repo, number, key: keyOf(repo, number) } : undefined
  } catch {
    return undefined
  }
}

const showRound = async ($: EngineInterface, home: string, pr: Pr): Promise<void> => {
  const path = `${home}/state/${pr.key}.json`
  if (!(await $.fs.exists(path))) return $.ui.status(undefined)
  try {
    const rounds = (JSON.parse(String(await $.fs.read(path))) as { rounds?: Round[] }).rounds ?? []
    const last = rounds[rounds.length - 1]
    $.ui.status(last ? `pr-gate #${pr.number}: round ${rounds.length}/3, ${last.verdict}` : undefined)
  } catch {
    $.ui.status(undefined)
  }
}

// Delivers every untaken request for this PR; returns how many.
const deliver = async ($: EngineInterface, home: string, pr: Pr): Promise<number> => {
  const dir = `${home}/inbox`
  const entries = await $.fs.list(dir).catch(() => [])
  let count = 0
  for (const entry of entries) {
    if (entry.kind !== 'file' || !entry.name.startsWith(`${pr.key}.`) || !entry.name.endsWith('.md')) continue
    const path = `${dir}/${entry.name}`
    if (await $.fs.exists(`${path}.taken`)) continue
    // ponytail: claim before reading; two sessions on one branch could both claim in the same instant.
    await $.fs.write(`${path}.taken`, String(await $.clock.now()))
    const body = String(await $.fs.read(path))
    await $.prompt.submit({
      text:
        `pr-gate fix request for ${pr.repo}#${pr.number} (${entry.name}), handed over from a pr-gate review ` +
        `session through ~/.pr-gate/inbox. This session's branch has that PR. Follow the request and ` +
        `this repo's own rules; do not merge.\n\n${body}`,
    })
    $.ui.toast(`pr-gate: fix request for #${pr.number} queued`)
    count++
  }
  return count
}

// The module's own memory; a hot reload starts it over, which only costs one gh call.
const live: { pr: Pr | undefined; ticks: number } = { pr: undefined, ticks: 0 }

async function tick($: EngineInterface): Promise<number> {
  if (!live.pr || live.ticks++ % PR_EVERY_TICKS === 0) live.pr = await findPr($)
  const pr = live.pr
  if (!pr) return 0
  const home = await homeOf($)
  await showRound($, home, pr)
  return deliver($, home, pr)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({ name: COMMAND, description: 'Check the pr-gate inbox for this branch\'s PR now' })
    if (e.isInteractive) {
      // A headless run (claude -p, a reviewer) must never pick up a fix request.
      $.clock.every(TICK_MS, () => {
        void tick($).catch(err => $.ui.log(`${COMMAND}: ${String(err)}`, { to: 'debug' }))
      })
    }
    return started
  })

  on('command.run', { command: COMMAND }, async $ => {
    const pr = await findPr($) // ask gh afresh: the branch may have changed
    live.pr = pr
    live.ticks = 1
    if (!pr) return { text: 'pr-gate inbox: this branch has no open PR (gh pr view found none).' }
    // prompt.submit from inside a command would wait on the turn this hook holds: deliver just after.
    $.clock.after(0, () => {
      void tick($).catch(err => $.ui.log(`${COMMAND}: ${String(err)}`, { to: 'debug' }))
    })
    return { text: `pr-gate inbox for ${pr.repo}#${pr.number}: checking now.` }
  })
}
