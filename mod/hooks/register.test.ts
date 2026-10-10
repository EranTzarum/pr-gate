import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { keyOf } from './register'

const HOME = '/h'
const REQ = `${HOME}/inbox/o__my_repo__7.abcdef12.md`
const HEADER = '<!-- pr-gate repo=o/my.repo pr=7 branch=feat/refund -->\n'
// The test engine types the full CommandRunInput; the engine fills the rest, as for a typed command.
const runCommand = ($: Engine) =>
  $.command.run({ command: 'pr-gate-inbox' } as Parameters<Engine['command']['run']>[0])
// The engine hands fs hooks absolute, native paths (C:\h\inbox on Windows).
const norm = (p: string) => p.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/')

type World = {
  cwd: string
  files: Record<string, string>
  dirs?: string[] // folders under cwd
  repos: Record<string, { branch: string; origin: string }> // git checkouts, by path
  surfaces?: string[]
  used?: Record<string, unknown>[] // tool inputs in the session's transcript
}

const world = (on: On, w: World) => {
  mock.env(on, { PR_GATE_HOME: HOME })
  const clock = mock.clock(on)
  on('session.cwd', () => ({ value: w.cwd }))
  on('session.surfaces', () => ({ value: (w.surfaces ?? ['desktop']) as never }))
  on('process.run', ($, e) => {
    const [, , dir = '', ...args] = e.argv
    const repo = w.repos[norm(dir)]
    const out = !repo ? '' : args[0] === 'branch' ? repo.branch : repo.origin
    return {
      value: { exitCode: repo ? 0 : 128, stdout: `${out}\n`, stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })
  on('fs.list', ($, e) => {
    const dir = norm(e.path)
    if (dir === w.cwd)
      return { value: (w.dirs ?? []).map(name => ({ name, kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false })) }
    return {
      value: Object.keys(w.files)
        .filter(p => p.startsWith(`${dir}/`))
        .map(p => ({ name: p.slice(dir.length + 1), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
    }
  })
  on('fs.exists', ($, e) => {
    const p = norm(e.path)
    return { value: p in w.files || (p.endsWith('/.git') && p.slice(0, -5) in w.repos) }
  })
  on('fs.read', ($, e) => {
    const p = norm(e.path)
    if (!(p in w.files)) throw new Error(`ENOENT ${p}`)
    return { value: w.files[p] ?? '' }
  })
  on('fs.write', ($, e) => {
    w.files[norm(e.path)] = e.text
    return { value: undefined }
  })
  on('session.messages', () => ({
    value: [{ role: 'assistant', text: '', toolUses: (w.used ?? []).map((input, i) => ({ tool_use_id: `t${i}`, tool: 'Read', input })) }] as never,
  }))
  const submitted: string[] = []
  const status: (string | undefined)[] = []
  on('prompt.submit', ($, e) => {
    submitted.push(e.text)
    return { text: e.text }
  })
  on('ui.toast', () => ({ value: undefined }))
  on('ui.status', ($, e) => {
    status.push(e.text)
    return { value: undefined }
  })
  return { submitted, status, clock }
}

test('key matches scripts/findings.py key()', () => {
  expect(keyOf('o/my.repo', 7)).toBe('o__my_repo__7')
})

test('a session whose cwd is the branch gets the request, once', async ($, on) => {
  const files: Record<string, string> = {
    [REQ]: `${HEADER}Fix src/a.ts:3`,
    [`${HOME}/inbox/o__my_repo__8.abcdef12.md`]: '<!-- pr-gate repo=o/my.repo pr=8 branch=feat/other -->\nx',
    [`${HOME}/inbox/stray.md`]: 'no header',
    [`${HOME}/state/o__my_repo__7.json`]: '{"rounds":[{"verdict":"fix"}]}',
  }
  const { submitted, status, clock } = world(on, {
    cwd: '/w/my.repo', files,
    repos: { '/w/my.repo': { branch: 'feat/refund', origin: 'https://github.com/o/my.repo.git' } },
  })
  await runCommand($)
  await clock.settle()
  expect(submitted.length).toBe(1)
  expect(submitted[0]).toContain('o/my.repo#7')
  expect(submitted[0]).toContain('Fix src/a.ts:3')
  expect(`${REQ}.taken` in files).toBe(true)

  await runCommand($)
  await clock.settle()
  expect(submitted.length).toBe(1) // taken: never twice
  expect(status).toContain('pr-gate #7: round 1/3, fix')
})

test('an umbrella session gets it only after working in that repo folder', async ($, on) => {
  const files: Record<string, string> = { [REQ]: `${HEADER}Fix it` }
  const used: Record<string, unknown>[] = [{ file_path: 'C:\\w\\other\\x.ts' }]
  const { submitted, clock } = world(on, {
    cwd: '/w', files, dirs: ['my.repo', 'other'], used,
    repos: { '/w/my.repo': { branch: 'feat/refund', origin: 'ssh://github.com/o/my.repo.git' } },
  })
  await runCommand($)
  await clock.settle()
  expect(submitted.length).toBe(0) // never touched my.repo: a manager session must not take it

  used.push({ command: 'git -C my.repo status' })
  await runCommand($)
  await clock.settle()
  expect(submitted.length).toBe(1)
})

test('wrong branch, or no surface (a -p run), delivers nothing', async ($, on) => {
  const files: Record<string, string> = { [REQ]: `${HEADER}x` }
  const { submitted, clock } = world(on, {
    cwd: '/w/my.repo', files, surfaces: [],
    repos: { '/w/my.repo': { branch: 'feat/refund', origin: 'https://github.com/o/my.repo' } },
  })
  await runCommand($)
  await clock.settle()
  expect(submitted.length).toBe(0)
})
