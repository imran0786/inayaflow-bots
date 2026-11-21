// AUTO-DEPLOY ROBOT: what does THE APP read from a guide's frontmatter?
//
// The robot reads slug / title / date / updated / description with its own simple line reader (policy.py). The app
// reads them with gray-matter + js-yaml. If the two ever disagree, a guide could show the robot one slug and give
// the app another (QA round 2: an indented root block + a multi-line quoted value hid the real keys). So the robot
// asks the app's own parser and refuses on ANY difference.
//
// Usage (inside the test container: no network, no secrets, files mounted read-only, before any candidate code ran):
//   NODE_PATH=/w/node_modules node fm_app_read.cjs <dir>
// <dir>/fm_manifest.json = [{ "path": "content/blog/x.mdx", "file": "g0.new" }, ...]
// Prints ONE JSON object: { files: { "<path>": { ok, reason, keys: [..top-level keys..], values: { slug, title, date, updated, description } } } }
// Each value is { type: "string" | "date" | "missing" | "<other js type>", value: "<text>" }.
'use strict'
const fs = require('fs')
const path = require('path')
const matter = require('gray-matter')

const WANT = ['slug', 'title', 'date', 'updated', 'description']
const refuse = (name) => ({
  parse: () => {
    throw new Error('frontmatter engine "' + name + '" is not allowed')
  },
})
// YAML only: the code engines are replaced by ones that throw (same idea as the app's own fix in lib/blog.ts)
const OPTIONS = {
  language: 'yaml',
  engines: { javascript: refuse('javascript'), js: refuse('js'), coffee: refuse('coffee'), coffeescript: refuse('coffeescript'), cson: refuse('cson'), toml: refuse('toml'), json: refuse('json') },
}

function describe(v) {
  if (v === undefined) return { type: 'missing', value: '' }
  if (v instanceof Date) return { type: 'date', value: Number.isNaN(v.getTime()) ? 'invalid' : v.toISOString().slice(0, 10) }
  if (typeof v === 'string') return { type: 'string', value: v }
  let text = ''
  try {
    text = JSON.stringify(v)
  } catch (e) {
    text = String(v)
  }
  return { type: v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v, value: String(text).slice(0, 200) }
}

function readOne(raw) {
  const firstLine = raw.split('\n', 1)[0]
  if (firstLine !== '---') return { ok: false, reason: 'the first line is not a plain ---' }
  const parsed = matter(raw, OPTIONS)
  const data = parsed.data
  if (!data || typeof data !== 'object' || Array.isArray(data)) return { ok: false, reason: 'the frontmatter is not a key: value block' }
  const values = {}
  for (const k of WANT) values[k] = describe(data[k])
  return { ok: true, reason: '', keys: Object.keys(data).slice(0, 60), values, body_chars: String(parsed.content || '').length }
}

function main() {
  const dir = process.argv[2]
  const out = { files: {} }
  let manifest = []
  try {
    manifest = JSON.parse(fs.readFileSync(path.join(dir, 'fm_manifest.json'), 'utf8'))
  } catch (e) {
    process.stdout.write(JSON.stringify({ files: {}, error: 'no manifest' }))
    return
  }
  for (const entry of manifest) {
    try {
      out.files[entry.path] = readOne(fs.readFileSync(path.join(dir, entry.file), 'utf8'))
    } catch (e) {
      out.files[entry.path] = { ok: false, reason: 'the app parser failed: ' + String((e && e.message) || e).slice(0, 160) }
    }
  }
  process.stdout.write(JSON.stringify(out))
}

if (require.main === module) main()
module.exports = { readOne }
