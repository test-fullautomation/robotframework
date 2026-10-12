// Completion in a flow file's text: where the cursor is, what fits there
// (plain text logic), and the keyword catalog of a flow with a real Robot
// Framework (the repository's own demo flows).
//
//   ROBOT_FLOW_PYTHON, ROBOT_FLOW_SRC (the fork's src)
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { placeAt, nodesOf, variablesOf, itemsFor, flowCatalog, catalogKey } = require('../out/backend/complete.js');

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYTHON = process.env.ROBOT_FLOW_PYTHON || 'python';
const FORK = process.env.ROBOT_FLOW_SRC || path.resolve(ROOT, '../../src');
const DEMO_FLOW = path.resolve(ROOT, '../../demo/flow/02_endurance_cycle.flow.json');
const haveFork = fs.existsSync(path.join(FORK, 'robot')) && fs.existsSync(DEMO_FLOW);
const skip = haveFork ? false : `needs the fork at ${FORK} and the demo flow`;

const FLOW = `{
  "flow": { "name": "Plan", "version": 1 },
  "imports": { "libraries": ["bench_keywords.py"] },
  "variables": { "RIG": "RIG_A", "\${CYCLES}": 3 },
  "nodes": [
    { "id": "start", "kind": "start" },
    { "id": "loop", "kind": "loop", "max_loops": "\${CYCLES}", "label": "Cycles" },
    { "id": "read", "kind": "keyword", "keyword": "Read Level", "args": ["ch1"], "assign": "\${LEVEL}",
      "retry": { "max_attempts": 3, "interval": "2s" } },
    { "id": "which", "kind": "decision", "condition": "$LEVEL > 1" },
    { "id": "ramp", "kind": "parallel" },
    { "id": "end", "kind": "end" }
  ],
  "edges": [
    ["start", "loop"],
    { "from": "loop", "to": "read", "label": "body" },
    { "from": "read", "to": "" }
  ]
}`;

const at = (piece, offset = 0) => FLOW.indexOf(piece) + offset;

test('the nodes of the text, read tolerantly, nested objects included', () => {
  const nodes = nodesOf(FLOW);
  assert.deepEqual(nodes.map((n) => [n.id, n.kind]),
                   [['start', 'start'], ['loop', 'loop'], ['read', 'keyword'], ['which', 'decision'], ['ramp', 'parallel'], ['end', 'end']]);
  assert.equal(nodes[1].label, 'Cycles');
  // Mid-edit text: a node without its closing brace yet is simply not there.
  const broken = FLOW.replace('{ "id": "which", "kind": "decision", "condition": "$LEVEL > 1" },', '{ "id": "which", "kind": "deci');
  assert.ok(nodesOf(broken).some((n) => n.id === 'read'));
});

test('the variables of the text: the variables section and the assigns', () => {
  assert.deepEqual(variablesOf(FLOW).sort(), ['${CYCLES}', '${LEVEL}', '${RIG}']);
});

test('where the cursor is: the key, the prefix, the object around', () => {
  const to = placeAt(FLOW, at('"to": ""', '"to": "'.length));
  assert.equal(to.key, 'to');
  assert.equal(to.prefix, '');
  assert.equal(to.from, 'read');
  const label = placeAt(FLOW, at('"label": "body"', '"label": "bo'.length));
  assert.deepEqual([label.key, label.prefix, label.from], ['label', 'bo', 'loop']);
  const keyword = placeAt(FLOW, at('"Read Level"', 3));
  assert.deepEqual([keyword.key, keyword.prefix, keyword.kind, keyword.inArray], ['keyword', 'Re', 'keyword', false]);
  const arg = placeAt(FLOW, at('"ch1"', 2));
  assert.deepEqual([arg.key, arg.inArray], ['args', true]);
  assert.equal(placeAt(FLOW, at('"keyword": "Read', 3)), null, 'a key is not completed here (the schema does it)');
  assert.equal(placeAt(FLOW, at('"max_loops"', 1)), null);
  const nested = placeAt(FLOW, at('"interval": "2s"', '"interval": "2'.length));
  assert.deepEqual([nested.key, nested.kind], ['interval', null], 'a nested object has no kind of its own');
});

test('what fits: node ids in edges, labels by the kind of the source node', () => {
  const to = placeAt(FLOW, at('"to": ""', '"to": "'.length));
  assert.deepEqual(itemsFor(FLOW, to, null).map((i) => i.label), ['loop', 'read', 'which', 'ramp', 'end'], 'not the start node');
  const fromItems = itemsFor(FLOW, { ...to, key: 'from' }, null).map((i) => i.label);
  assert.ok(fromItems.includes('start') && !fromItems.includes('end'));
  const label = placeAt(FLOW, at('"label": "body"', '"label": "'.length));
  assert.deepEqual(itemsFor(FLOW, label, null).map((i) => i.insert), ['body', 'done', 'next', 'continue', 'abort', 'on_failure']);
  const fromRamp = { ...label, from: 'ramp' };
  assert.ok(itemsFor(FLOW, fromRamp, null).some((i) => i.insert === 'branch'));
  const fromWhich = { ...label, from: 'which' };
  assert.deepEqual(itemsFor(FLOW, fromWhich, null).map((i) => i.insert), ['yes', 'no']);
  const unknownFrom = { ...label, from: null };
  assert.ok(itemsFor(FLOW, unknownFrom, null).length > 6, 'every label when the source is not known');
});

test('what fits: keywords from the catalog, variables where ${ was typed', () => {
  const catalog = { ok: true,
    keywords: [{ name: 'Set Signal', owner: 'FlowSignals', owner_type: 'library', shortdoc: 'Publish', args: [
                  { name: 'name', kind: 'POSITIONAL_OR_NAMED', required: true, default: null, type: null },
                  { name: 'value', kind: 'POSITIONAL_OR_NAMED', required: false, default: '1', type: null }] },
               { name: 'Log', owner: 'BuiltIn', owner_type: 'library', shortdoc: 'Logs', args: [] }],
    variables: [{ name: '${PEER}', value: 'RIG_B', source: 'bench.resource' }] };
  const keyword = placeAt(FLOW, at('"Read Level"', 3));
  const items = itemsFor(FLOW, keyword, catalog);
  assert.deepEqual(items.map((i) => i.label), ['Set Signal', 'Log']);
  assert.equal(items[0].detail, 'FlowSignals · name, value=1');
  assert.ok(items[0].sort < items[1].sort, 'BuiltIn sorts last');
  const typing = FLOW.replace('"args": ["ch1"]', '"args": ["${"]');
  const arg = placeAt(typing, typing.indexOf('"${"') + 3);
  const vars = itemsFor(typing, arg, catalog);
  assert.deepEqual(vars.map((i) => i.label).sort(), ['${CYCLES}', '${LEVEL}', '${PEER}', '${RIG}']);
  assert.equal(vars.find((i) => i.label === '${PEER}').insert, '${PEER}');
  const typed = typing.replace('"args": ["${"]', '"args": ["a ${LE"]');
  const mid = placeAt(typed, typed.indexOf('"a ${LE"') + 6);
  assert.equal(itemsFor(typed, mid, catalog).find((i) => i.label === '${LEVEL}').insert, 'a ${LEVEL}');
});

test('the Diagram field editor: keyword matching, variables at the caret, the parameter hint', async () => {
  const { matchKeywords, variableAt, matchVariables, argsHint, findKeyword } = await import('../media/host/complete.js');
  const keywords = [
    { name: 'Set Signal', owner: 'FlowSignals', args: [{ name: 'name', kind: 'POSITIONAL_OR_NAMED', required: true },
                                                      { name: 'value', kind: 'POSITIONAL_OR_NAMED', required: false, default: '1' },
                                                      { name: 'extra', kind: 'VAR_NAMED' }] },
    { name: 'Signal Should Be', owner: 'FlowSignals', args: [] },
    { name: 'Log', owner: 'BuiltIn', args: [] },
    { name: 'Run Cycle Tests', owner: 'bench_keywords', args: [] },
  ];
  assert.deepEqual(matchKeywords(keywords, 'sig').map((k) => k.name), ['Signal Should Be', 'Set Signal'], 'a prefix first, then a part');
  assert.deepEqual(matchKeywords(keywords, 'set_signal').map((k) => k.name), ['Set Signal'], 'underscores and case do not count');
  assert.deepEqual(matchKeywords(keywords, 'builtin').map((k) => k.name), ['Log'], 'the owner matches too');
  assert.equal(matchKeywords(keywords, '').length, 4, 'everything while nothing is typed');
  assert.deepEqual(matchKeywords(keywords, '', 2).map((k) => k.name), ['Set Signal', 'Signal Should Be'], 'at most max');
  assert.deepEqual(variableAt('a ${LE', 6), { start: 2, prefix: '${LE' });
  assert.equal(variableAt('${DONE} and', 11), null, 'a closed variable is not being typed');
  assert.deepEqual(variableAt('x\n@{li', 6), { start: 2, prefix: '@{li' });
  const variables = [{ name: '${LEVEL}', source: 'f' }, { name: '${RIG}', source: 'f' }, { name: '${LEVEL}', source: 'r' }];
  assert.deepEqual(matchVariables(variables, '${le').map((v) => v.name), ['${LEVEL}'], 'once, by name');
  assert.deepEqual(matchVariables(variables, '${').map((v) => v.name), ['${LEVEL}', '${RIG}']);
  assert.equal(argsHint(keywords[0]), 'name\nvalue=1\n**extra');
  assert.equal(argsHint(keywords[2]), '');
  assert.equal(findKeyword(keywords, 'set  signal').name, 'Set Signal');
  assert.equal(findKeyword(keywords, 'nothing'), null);
});

test('the catalog key is the imports section', () => {
  assert.equal(catalogKey(FLOW), '"imports": { "libraries": ["bench_keywords.py"] }');
  assert.equal(catalogKey(FLOW.replace('"RIG": "RIG_A"', '"RIG": "RIG_B"')), catalogKey(FLOW));
});

test('the shipped schema is the fork\'s', { skip: fs.existsSync(path.join(FORK, 'robot/flow/flow.schema.json')) ? false : 'needs the fork' }, () => {
  const shipped = fs.readFileSync(path.join(ROOT, 'schemas/flow.schema.json'), 'utf8');
  const fork = fs.readFileSync(path.join(FORK, 'robot/flow/flow.schema.json'), 'utf8');
  assert.equal(shipped, fork, 'copy src/robot/flow/flow.schema.json to schemas/flow.schema.json');
});

test('the keywords of a demo flow: its library, the flow library, BuiltIn; mid-edit text too', { skip }, async () => {
  const env = { python: PYTHON, robotSource: FORK, pythonPath: [], cwd: path.dirname(DEMO_FLOW), timeoutMs: 90000,
                helpersDir: path.join(ROOT, 'python') };
  const text = fs.readFileSync(DEMO_FLOW, 'utf8');
  const catalog = await flowCatalog(env, DEMO_FLOW, text);
  assert.equal(catalog.ok, true, catalog.error);
  const names = catalog.keywords.map((k) => k.name);
  assert.ok(names.includes('Run Cycle Tests'), 'from bench_keywords.py');
  assert.ok(names.includes('Flow Snapshot') && names.includes('Flow Pause'), 'the flow library');
  assert.ok(!names.includes('Flow Gate') && !names.includes('Flow Join'), 'not the bookkeeping');
  assert.ok(names.includes('Log'), 'BuiltIn');
  const cycle = catalog.keywords.find((k) => k.name === 'Run Cycle Tests');
  assert.deepEqual(cycle.args.map((a) => a.name), ['rig', 'fail_on']);
  assert.ok(catalog.variables.some((v) => v.name === '${RIG}'), 'the file\'s variables');
  // The same with the nodes half typed: the imports are still read.
  const broken = text.replace('"edges"', '"edg');
  const again = await flowCatalog(env, DEMO_FLOW, broken);
  assert.ok(again.keywords.some((k) => k.name === 'Run Cycle Tests'));
});
