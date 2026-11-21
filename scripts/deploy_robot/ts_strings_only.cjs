// AUTO-DEPLOY ROBOT: proves that a change to a TypeScript "copy" file changed ONLY words.
//
// Usage (inside the test container, before any candidate code has run):
//   NODE_PATH=/w/node_modules node ts_strings_only.cjs <dir>
// <dir>/manifest.json = [{ "path": "lib/x.ts", "old": "0.old", "new": "0.new", "tables": ["NAME"] }, ...]
// Prints ONE JSON object: { files: { "<path>": { ok, reason, changed: [{old, new, key, kind}], table_changed } } }
//
// How: both versions are parsed with the real TypeScript parser. Every token of the file (keywords, names, numbers,
// punctuation, imports, call arguments ...) goes into a "skeleton", except the TEXT of string literals that sit in a
// plain data position (object value, array item, variable value, returned value, text parts of a template). If the
// two skeletons are identical, nothing but those texts changed: no new code, import, call or expression is possible.
// Comments are not part of the skeleton (they cannot run).
// A named "table" (toolCards GUIDE_TOPIC_TOOLS) may gain, lose or reorder rows, but each row must have the one fixed
// shape [ /simple-words/, [P.a, P.b, P.c] ] with P.<name> taken from the names the OLD file already has.
'use strict'
const fs = require('fs')
const path = require('path')
const ts = require('typescript')

const SAFE_REGEX = /^\/[a-z0-9|-]{2,80}\/$/
const MAX_ROWS = 40

function parse(name, text) {
  const sf = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  if (sf.parseDiagnostics && sf.parseDiagnostics.length) throw new Error('does not parse as TypeScript')
  return sf
}

// Walk up through wrappers that do not change what a string is used for.
function dataParent(node) {
  let child = node
  let p = node.parent
  while (p && (ts.isParenthesizedExpression(p) || ts.isAsExpression(p) || (ts.isSatisfiesExpression && ts.isSatisfiesExpression(p)) ||
               ts.isTemplateSpan(p) || ts.isTemplateExpression(p) ||
               (ts.isConditionalExpression(p) && p.condition !== child) ||
               (ts.isBinaryExpression(p) && [ts.SyntaxKind.PlusToken, ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken].includes(p.operatorToken.kind)))) {
    child = p
    p = p.parent
  }
  return { parent: p, child }
}

function maskable(node) {
  const { parent: p, child } = dataParent(node)
  if (!p) return false
  if (ts.isPropertyAssignment(p)) return p.initializer === child
  if (ts.isArrayLiteralExpression(p)) return true
  if (ts.isVariableDeclaration(p)) return p.initializer === child
  if (ts.isReturnStatement(p)) return true
  if (ts.isArrowFunction(p)) return p.body === child
  return false
}

function keyOf(node) {
  let p = node.parent
  while (p) {
    if (ts.isPropertyAssignment(p)) return p.name.getText()
    if (ts.isVariableDeclaration(p)) return p.name.getText()
    p = p.parent
  }
  return ''
}

function pNames(sf) {
  const names = new Set()
  sf.forEachChild(function visit(n) {
    if (ts.isVariableDeclaration(n) && n.name.getText(sf) === 'P' && n.initializer) {
      let init = n.initializer
      while (ts.isAsExpression(init) || ts.isParenthesizedExpression(init)) init = init.expression
      if (ts.isObjectLiteralExpression(init)) for (const pr of init.properties) if (ts.isPropertyAssignment(pr)) names.add(pr.name.getText(sf))
    }
    n.forEachChild(visit)
  })
  return names
}

function tableRows(sf, init, allowedP) {
  if (!ts.isArrayLiteralExpression(init)) throw new Error('table is not an array')
  if (init.elements.length > MAX_ROWS || init.elements.length < 1) throw new Error('table has ' + init.elements.length + ' rows')
  return init.elements.map((row) => {
    if (!ts.isArrayLiteralExpression(row) || row.elements.length !== 2) throw new Error('table row is not [pattern, tools]')
    const [rx, tools] = row.elements
    if (rx.kind !== ts.SyntaxKind.RegularExpressionLiteral || !SAFE_REGEX.test(rx.getText(sf))) throw new Error('table pattern must be simple words like /hashtag|caption/')
    if (!ts.isArrayLiteralExpression(tools) || tools.elements.length !== 3) throw new Error('table row needs exactly 3 tools')
    const names = tools.elements.map((t) => {
      if (!ts.isPropertyAccessExpression(t) || t.expression.getText(sf) !== 'P' || !allowedP.has(t.name.getText(sf))) throw new Error('table tools must be P.<existing name>')
      return t.name.getText(sf)
    })
    return rx.getText(sf) + '=' + names.join(',')
  })
}

function flatten(sf, tables, allowedP) {
  const skeleton = []
  const strings = []
  const tableOut = {}
  ;(function walk(node) {
    if (ts.isVariableDeclaration(node) && tables.includes(node.name.getText(sf)) && node.initializer) {
      tableOut[node.name.getText(sf)] = tableRows(sf, node.initializer, allowedP)
      for (const ch of node.getChildren(sf)) {
        if (ch === node.initializer) skeleton.push('TABLE')
        else walk(ch)
      }
      return
    }
    const k = node.kind
    if (k === ts.SyntaxKind.StringLiteral || k === ts.SyntaxKind.NoSubstitutionTemplateLiteral) {
      if (maskable(node)) {
        skeleton.push('S')
        strings.push({ text: node.text, key: keyOf(node), kind: 'string' })
      } else skeleton.push(k + ':' + node.getText(sf))
      return
    }
    if (k === ts.SyntaxKind.TemplateHead || k === ts.SyntaxKind.TemplateMiddle || k === ts.SyntaxKind.TemplateTail) {
      const tpl = k === ts.SyntaxKind.TemplateHead ? node.parent : node.parent.parent
      if (tpl && ts.isTemplateExpression(tpl) && maskable(tpl)) {
        skeleton.push('T' + k)
        strings.push({ text: node.text, key: keyOf(node), kind: 'template' })
      } else skeleton.push(k + ':' + node.getText(sf))
      return
    }
    const kids = node.getChildren(sf)
    if (kids.length === 0) {
      skeleton.push(k + ':' + node.getText(sf))
      return
    }
    skeleton.push('<' + k)
    for (const ch of kids) walk(ch)
    skeleton.push('>')
  })(sf)
  return { skeleton, strings, tables: tableOut }
}

function compare(entry, dir) {
  const oldText = fs.readFileSync(path.join(dir, entry.old), 'utf8')
  const newText = fs.readFileSync(path.join(dir, entry.new), 'utf8')
  if (/\r/.test(newText) || /\0/.test(newText)) return { ok: false, reason: 'Windows line ends or NUL byte' }
  const tables = Array.isArray(entry.tables) ? entry.tables : []
  const oldSf = parse('old.ts', oldText)
  const newSf = parse('new.ts', newText)
  const allowedP = pNames(oldSf)
  const a = flatten(oldSf, tables, allowedP)
  const b = flatten(newSf, tables, allowedP)
  if (a.skeleton.length !== b.skeleton.length) return { ok: false, reason: 'the code structure changed (' + a.skeleton.length + ' -> ' + b.skeleton.length + ' tokens)' }
  for (let i = 0; i < a.skeleton.length; i++) {
    if (a.skeleton[i] !== b.skeleton[i]) return { ok: false, reason: 'code changed near: ' + String(b.skeleton[i]).slice(0, 80) }
  }
  if (a.strings.length !== b.strings.length) return { ok: false, reason: 'string count changed' }
  const changed = []
  for (let i = 0; i < a.strings.length; i++) {
    if (a.strings[i].text !== b.strings[i].text) changed.push({ old: a.strings[i].text, new: b.strings[i].text, key: b.strings[i].key, kind: b.strings[i].kind })
  }
  for (const t of tables) if (!(t in a.tables) || !(t in b.tables)) return { ok: false, reason: 'table ' + t + ' not found' }
  const tableChanged = JSON.stringify(a.tables) !== JSON.stringify(b.tables)
  return { ok: true, reason: '', changed, table_changed: tableChanged, tables: tableChanged ? b.tables : undefined }
}

function main() {
  const dir = process.argv[2]
  const out = { files: {} }
  let manifest = []
  try {
    manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'))
  } catch (e) {
    process.stdout.write(JSON.stringify({ files: {}, error: 'no manifest' }))
    return
  }
  for (const entry of manifest) {
    try {
      out.files[entry.path] = compare(entry, dir)
    } catch (e) {
      out.files[entry.path] = { ok: false, reason: String((e && e.message) || e).slice(0, 200) }
    }
  }
  process.stdout.write(JSON.stringify(out))
}

if (require.main === module) main()
module.exports = { compare, flatten, parse, maskable }
