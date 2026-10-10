import type { EngineInterface, Register } from 'claude-code'

// pr-gate (any host: Claude, Codex, Cursor, a CLI) writes a fix request to
// <home>/inbox/<key>.<sha8>.md with scripts/handoff.py; its first line names
// the repo, PR and head branch. This mod runs in every Claude Code session and
// delivers the request to the one that owns the branch: its cwd, or a repo
// folder right under its cwd that the session has worked in (the umbrella
// layout: sessions open in a parent folder and work in <repo>/), checked out
// on that branch with that origin. All local git, no network. It marks the
// file `.taken` and starts a turn with it once the session is idle, and shows
// the PR's pr-gate round in the status line.

const COMMAND = 'pr-gate-inbox'
const TICK_MS = 60_000
const HISTORY = 200 // transcript rows scanned for the folders this session works in
const HEADER = /^<!--\s*pr-gate repo=(\S+) pr=(\d+) branch=(\S+)\s*-->/

type Request = { file: string; path: string; repo: string; pr: number; branch: string; key: string }
type Round = { verdict?: string }

// Same as scripts/findings.py key(): owner__repo__N, unsafe chars as "_".
export const keyOf = (repo: string, n: number): string =>
  repo.replace('/', '__').replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') + `__${n}`

export const norm = (p: string): string => p.replace(/\\\\/g, '/').replace(/\\/g, '/').toLowerCase()

// The module's own memory; a hot reload starts it over.
const live: { statusKey: string | undefined; statusPr: number } = {
  statusKey: undefined,
  statusPr: 0,
}

async function homeOf($: EngineInterface): Promise<string> {
  const own = await $.env.get('PR_GATE_HOME')
  if (own) return own
  const user = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? '.'
  return `${user}/.pr-gate`
}

async function git($: EngineInterface, dir: string, args: string[]): Promise<string> {
  const ran = await $.process.run(['git', '-C', dir, ...args], { timeoutMs: 10_000 }).catch(() => undefined)
  return ran && ran.exitCode === 0 ? ran.stdout.trim() : ''
}

// Untaken requests in the inbox, with a valid header.
async function pending($: EngineInterface, home: string): Promise<Request[]> {
  const dir = `${home}/inbox`
  const out: Request[] = []
  for (const entry of await $.fs.list(dir).catch(() => [])) {
    if (entry.kind !== 'file' || !entry.name.endsWith('.md')) continue
    const path = `${dir}/${entry.name}`
    if (await $.fs.exists(`${path}.taken`)) continue
    const m = HEADER.exec(String(await $.fs.read(path).catch(() => '')))
    const [repo, pr, branch] = [m?.[1], Number(m?.[2]), m?.[3]]
    if (repo && pr && branch) out.push({ file: entry.name, path, repo, pr, branch, key: keyOf(repo, pr) })
  }
  return out
}

// The tool inputs of the session's recent rows; the transcript survives a reload of this mod.
async function toolInputs($: EngineInterface): Promise<string[]> {
  const rows = await $.session.messages().catch(() => [])
  if (!Array.isArray(rows)) return []
  return rows.slice(-HISTORY).flatMap(row => row.toolUses.map(use => norm(JSON.stringify(use.input)).slice(0, 4000)))
}

// The cwd, plus git repos right under it that this session's tool calls named.
async function ownDirs($: EngineInterface): Promise<string[]> {
  const cwd = await $.session.cwd()
  const touched = await toolInputs($)
  const dirs = [cwd]
  for (const entry of await $.fs.list(cwd).catch(() => [])) {
    if (entry.kind !== 'dir') continue
    const dir = `${cwd}/${entry.name}`
    const full = norm(dir)
    const rel = new RegExp(`(^|[\\s"'=(])${entry.name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([/"'\\s]|$)`)
    if (!touched.some(t => t.includes(full) || rel.test(t))) continue
    if (await $.fs.exists(`${dir}/.git`)) dirs.push(dir)
  }
  return dirs
}

async function owns($: EngineInterface, dirs: string[], req: Request): Promise<boolean> {
  for (const dir of dirs) {
    if ((await git($, dir, ['branch', '--show-current'])) !== req.branch) continue
    const origin = norm(await git($, dir, ['remote', 'get-url', 'origin'])).replace(/\.git$/, '')
    if (origin.endsWith(`/${req.repo.toLowerCase()}`) || origin.endsWith(`:${req.repo.toLowerCase()}`)) return true
  }
  return false
}

async function showRound($: EngineInterface, home: string): Promise<void> {
  if (!live.statusKey) return
  const path = `${home}/state/${live.statusKey}.json`
  try {
    const rounds = (JSON.parse(String(await $.fs.read(path))) as { rounds?: Round[] }).rounds ?? []
    const last = rounds[rounds.length - 1]
    $.ui.status(last ? `pr-gate #${live.statusPr}: round ${rounds.length}/3, ${last.verdict}` : undefined)
  } catch {
    $.ui.status(undefined)
  }
}

// Delivers every request this session owns; returns how many.
async function tick($: EngineInterface): Promise<number> {
  // Only a session someone looks at (terminal or app); a plain -p run, a reviewer, has no surface.
  if ((await $.session.surfaces()).length === 0) return 0
  const home = await homeOf($)
  await showRound($, home)
  const reqs = await pending($, home)
  if (reqs.length === 0) return 0
  const dirs = await ownDirs($)
  let count = 0
  for (const req of reqs) {
    if (!(await owns($, dirs, req))) continue
    // ponytail: claim before submitting; two sessions on one branch could both claim in the same instant.
    await $.fs.write(`${req.path}.taken`, String(await $.clock.now()))
    const body = String(await $.fs.read(req.path))
    await $.prompt.submit({
      text:
        `pr-gate fix request for ${req.repo}#${req.pr} (branch ${req.branch}), handed over from a pr-gate ` +
        `review session through ~/.pr-gate/inbox (${req.file}). This session has that branch checked out. ` +
        `Follow the request and the repo's own rules; do not merge.\n\n${body}`,
    })
    $.ui.toast(`pr-gate: fix request for #${req.pr} queued`)
    live.statusKey = req.key
    live.statusPr = req.pr
    count++
  }
  return count
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({ name: COMMAND, description: 'Check the pr-gate inbox now' })
    $.clock.every(TICK_MS, () => {
      void tick($).catch(err => $.ui.log(`${COMMAND}: ${String(err)}`, { to: 'debug' }))
    })
    return started
  })

  on('command.run', { command: COMMAND }, async $ => {
    // prompt.submit from inside a command would wait on the turn this hook holds: deliver just after.
    $.clock.after(0, () => {
      void tick($).catch(err => $.ui.log(`${COMMAND}: ${String(err)}`, { to: 'debug' }))
    })
    return { text: 'pr-gate inbox: checking now.' }
  })
}
