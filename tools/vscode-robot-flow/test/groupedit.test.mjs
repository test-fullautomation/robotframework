// Editing run groups in testproject.json: only the "groups" value changes,
// the rest of the file stays byte for byte.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { addMember, removeMember, setMemberVariables, renameGroup, removeGroup, groupsSpan, readRawGroups, targetOf } =
  require('../out/backend/groupedit.js');
const { readProject } = require('../out/backend/project.js');

// Odd formatting on purpose: 4-space indent, a key with "groups" in a string,
// a group with keys the extension does not know, CRLF.
const TEXT = [
  '{',
  '    "schema": 1,',
  '    "runner": "robotframework-aio",',
  '    "note": "the \\"groups\\" word in a string, with ] and }",',
  '    "run": { "python": "", "pythonpath": [ "../src" ], "args": [], "env": {} },',
  '    "groups": [',
  '        { "id": "pair", "title": "The pair", "owner": "gui", "members": [',
  '            { "id": "A", "target": "flows/a.flow.json", "variables": { "RIG": "A" }, "color": "red" }',
  '        ], "env": { "X": "1" } }',
  '    ],',
  '    "services": {}',
  '}',
].join('\r\n') + '\r\n';

function project() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'robot-flow-group-'));
  fs.mkdirSync(path.join(dir, 'flows'));
  for (const f of ['a', 'b']) fs.writeFileSync(path.join(dir, 'flows', `${f}.flow.json`), '{}');
  const file = path.join(dir, 'testproject.json');
  fs.writeFileSync(file, TEXT);
  return { dir, file, project: readProject(file) };
}

function outside(text) {
  const span = groupsSpan(text);
  return text.slice(0, span.start) + text.slice(span.end);
}

test('the groups span skips strings and nesting', () => {
  const span = groupsSpan(TEXT);
  assert.equal(TEXT.slice(span.start, span.start + 1), '[');
  assert.equal(TEXT.slice(span.end - 1, span.end), ']');
  assert.equal(JSON.parse(TEXT.slice(span.start, span.end))[0].id, 'pair');
  assert.equal(groupsSpan('{ "a": { "groups": [1] } }'), null, 'only the top level counts');
});

test('add, edit and remove members and groups; everything else stays byte for byte', () => {
  const { dir, file, project: p0 } = project();
  const before = outside(TEXT);
  let p = addMember(p0, 'pair', { id: 'B', target: targetOf(p0, path.join(dir, 'flows', 'b.flow.json')), variables: { RIG: 'B' } });
  assert.deepEqual(p.groups[0].members.map((m) => [m.id, m.target, m.variables]),
    [['A', 'flows/a.flow.json', { RIG: 'A' }], ['B', 'flows/b.flow.json', { RIG: 'B' }]]);
  let text = fs.readFileSync(file, 'utf8');
  assert.equal(outside(text), before, 'nothing outside "groups" changed');
  assert.ok(text.includes('\r\n'), 'line endings kept');
  const raw = readRawGroups(file);
  assert.equal(raw[0].owner, 'gui', 'unknown group keys kept');
  assert.equal(raw[0].members[0].color, 'red', 'unknown member keys kept');
  assert.equal(raw[0].env.X, '1');
  assert.match(text, /\r\n        \{\r\n            "id": "pair"/, 'indented like the file');

  p = addMember(p, 'solo', { id: 'ONLY', target: 'flows/a.flow.json' }, { title: 'A alone' });
  assert.deepEqual(p.groups.map((g) => [g.id, g.title]), [['pair', 'The pair'], ['solo', 'A alone']]);
  assert.throws(() => addMember(p, 'pair', { id: 'A', target: 'x' }), /already has a member 'A'/);
  assert.throws(() => addMember(p, 'bad id!', { id: 'X', target: 'x' }, { title: 't' }), /not a group id/);
  assert.throws(() => addMember(p, 'pair', { id: 'no spaces', target: 'x' }), /not a member id/);

  p = setMemberVariables(p, 'pair', 'B', { RIG: 'B2', MODE: 'EMC' });
  assert.deepEqual(p.groups[0].members[1].variables, { RIG: 'B2', MODE: 'EMC' });
  p = setMemberVariables(p, 'pair', 'B', {});
  assert.deepEqual(p.groups[0].members[1].variables, {});
  p = renameGroup(p, 'pair', 'The renamed pair');
  assert.equal(p.groups[0].title, 'The renamed pair');
  p = removeMember(p, 'pair', 'B');
  assert.deepEqual(p.groups[0].members.map((m) => m.id), ['A']);
  p = removeMember(p, 'solo', 'ONLY');
  assert.deepEqual(p.groups.map((g) => g.id), ['pair'], 'an emptied group is gone');
  p = removeGroup(p, 'pair');
  assert.deepEqual(p.groups, []);
  text = fs.readFileSync(file, 'utf8');
  assert.equal(outside(text), before);
  assert.throws(() => targetOf(p, path.join(os.tmpdir(), 'elsewhere.flow.json')), /not inside the project/);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('a project without groups gets the key added at the end of the object', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'robot-flow-group-'));
  const file = path.join(dir, 'testproject.json');
  fs.writeFileSync(file, '{\n  "schema": 1,\n  "run": { "python": "" }\n}\n');
  const p = addMember(readProject(file), 'g', { id: 'M', target: 'm.flow.json' }, { title: 'G' });
  assert.deepEqual(p.groups.map((g) => [g.id, g.members[0].id]), [['g', 'M']]);
  const text = fs.readFileSync(file, 'utf8');
  assert.ok(text.startsWith('{\n  "schema": 1,\n  "run": { "python": "" },\n  "groups": ['), text);
  assert.ok(text.endsWith(']\n}\n'), JSON.stringify(text.slice(-10)));
  fs.rmSync(dir, { recursive: true, force: true });
});
