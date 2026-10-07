import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import { keyOf } from './register'

const HOME = '/h'
// The test engine types the full CommandRunInput; the engine fills the rest, as for a typed command.
const runCommand = ($: Engine) =>
  $.command.run({ command: 'pr-gate-inbox' } as Parameters<Engine['command']['run']>[0])
// The engine hands fs hooks absolute, native paths (C:\h\inbox on Windows).
const norm = (p: string) => p.replace(/^[A-Za-z]:/, '').replace(/\\/g, '/')

// A file system in memory under HOME, plus gh answering PR 7 of o/my.repo.
const world = (on: On, files: Record<string, string>, ghOk = true) => {
  mock.env(on, { PR_GATE_HOME: HOME })
  const clock = mock.clock(on)
  on('process.run', () => ({
    value: {
      exitCode: ghOk ? 0 : 1,
      stdout: ghOk ? '{"number":7,"url":"https://github.com/o/my.repo/pull/7"}' : '',
      stderr: ghOk ? '' : 'no pull requests found',
      isStdoutTruncated: false,
      isStderrTruncated: false,
    },
  }))
  on('fs.list', ($, e) => {
    const dir = `${norm(e.path)}/`
    return {
      value: Object.keys(files)
        .filter(p => p.startsWith(dir))
        .map(p => ({ name: p.slice(dir.length), kind: 'file' as const, size: 1, mtimeMs: 1, isLink: false })),
    }
  })
  on('fs.exists', ($, e) => ({ value: norm(e.path) in files }))
  on('fs.read', ($, e) => {
    const p = norm(e.path)
    if (!(p in files)) throw new Error(`ENOENT ${p}`)
    return { value: files[p] ?? '' }
  })
  on('fs.write', ($, e) => {
    files[norm(e.path)] = e.text
    return { value: undefined }
  })
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

test('delivers only this PR\'s untaken requests, once', async ($, on) => {
  const files: Record<string, string> = {
    [`${HOME}/inbox/o__my_repo__7.abcdef12.md`]: 'Fix src/a.ts:3',
    [`${HOME}/inbox/o__my_repo__7.11111111.md`]: 'old one',
    [`${HOME}/inbox/o__my_repo__7.11111111.md.taken`]: '1',
    [`${HOME}/inbox/o__my_repo__8.abcdef12.md`]: 'another PR',
    [`${HOME}/state/o__my_repo__7.json`]: '{"rounds":[{"verdict":"fix"},{"verdict":"fix"}]}',
  }
  const { submitted, status, clock } = world(on, files)

  const first = await runCommand($)
  expect(first.text).toContain('o/my.repo#7: checking now')
  await clock.settle()
  expect(submitted.length).toBe(1)
  expect(submitted[0]).toContain('o/my.repo#7')
  expect(submitted[0]).toContain('Fix src/a.ts:3')
  expect(`${HOME}/inbox/o__my_repo__7.abcdef12.md.taken` in files).toBe(true)
  expect(status).toContain('pr-gate #7: round 2/3, fix')

  await runCommand($)
  await clock.settle()
  expect(submitted.length).toBe(1) // taken: never twice
})

test('a branch with no PR delivers nothing', async ($, on) => {
  const { submitted } = world(on, { [`${HOME}/inbox/o__my_repo__7.abcdef12.md`]: 'x' }, false)
  const out = await runCommand($)
  expect(out.text).toContain('no open PR')
  expect(submitted.length).toBe(0)
})
