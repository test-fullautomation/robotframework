// The Robot Framework grammar, tokenized by VS Code's own TextMate engine
// (vscode-textmate + vscode-oniguruma from the installed VS Code, run with
// its executable as Node so the .asar is readable):
//
//   npm run test:grammar           (VSCODE_EXE: Code.exe, else the usual install)
//
// Each check names a piece of text on a line and the scope it must get.
'use strict';

const fs = require('fs');
const path = require('path');

const app = process.env.MB_VSCODE_APP;   // resources/app of the VS Code running this
const textmate = require(path.join(app, 'node_modules.asar', 'vscode-textmate'));
const onig = require(path.join(app, 'node_modules.asar', 'vscode-oniguruma'));

const GRAMMAR = path.join(__dirname, '..', 'syntaxes', 'robotframework.tmLanguage.json');

const SAMPLE = [
  '*** Settings ***',                                                   // 0
  'Documentation     Signals smoke: the bench publishes.',               // 1
  'Library           Collections',                                      // 2
  'Resource          ../resources/bench_signals.resource',              // 3
  'Suite Setup       Resolve Signal Owners    prefix=bench.',           // 4
  '',
  '*** Variables ***',                                                  // 6
  '${MAX_TEMP}       85',                                               // 7
  '',
  '*** Test Cases ***',                                                 // 9
  'Temperature Is In Range',                                            // 10
  '    [Documentation]    Reads the sensor.',                           // 11
  '    [Setup]    Open Bench    ${PORT}',                               // 12
  '    ${value}=    Get Signal    bench.dut.temp    timeout=2s    # read it', // 13
  '    Should Be True    ${value} <= ${MAX_TEMP}',                      // 14
  '    FOR    ${i}    IN RANGE    3',                                   // 15
  '        Log    tick ${i}',                                           // 16
  '    END',                                                            // 17
  '    ...    continued',                                               // 18
  '',
  '*** Keywords ***',                                                   // 20
  'Open Bench',                                                         // 21
  '    [Arguments]    ${port}    ${speed}=9600',                        // 22
  '    IF    ${port} == 0',                                             // 23
  '        Fail    no port',                                            // 24
  '    ELSE',                                                           // 25
  '        Connect    ${port}',                                         // 26
  '    END',                                                            // 27
  '# a comment line',                                                   // 28
];

// [line, text, scope it must carry]
const CHECKS = [
  [0, '*** Settings ***', 'entity.name.section'],
  [1, 'Documentation', 'storage.type.setting'],
  [1, 'Signals smoke', 'string.unquoted.documentation'],
  [2, 'Library', 'storage.type.setting'],
  [2, 'Collections', 'entity.name.namespace'],
  [3, '../resources/bench_signals.resource', 'entity.name.namespace'],
  [4, 'Suite Setup', 'storage.type.setting'],
  [4, 'Resolve Signal Owners', 'support.function'],
  [4, 'prefix', 'variable.parameter'],
  [7, '${MAX_TEMP}', 'variable.other.definition'],
  [9, '*** Test Cases ***', 'entity.name.section'],
  [10, 'Temperature Is In Range', 'entity.name.function'],
  [11, '[Documentation]', 'keyword.other.setting'],
  [11, 'Reads the sensor.', 'string.unquoted.documentation'],
  [12, '[Setup]', 'keyword.other.setting'],
  [12, 'Open Bench', 'support.function'],
  [12, '${PORT}', 'variable.other'],
  [13, '${value}', 'variable.other'],
  [13, 'Get Signal', 'support.function'],
  [13, 'timeout', 'variable.parameter'],
  [13, '# read it', 'comment.line'],
  [14, 'Should Be True', 'support.function'],
  [14, '${MAX_TEMP}', 'variable.other'],
  [15, 'FOR', 'keyword.control'],
  [15, 'IN RANGE', 'keyword.control'],
  [16, 'Log', 'support.function'],
  [16, '${i}', 'variable.other'],
  [17, 'END', 'keyword.control'],
  [18, '...', 'punctuation.separator.continuation'],
  [21, 'Open Bench', 'entity.name.function'],
  [22, '[Arguments]', 'keyword.other.setting'],
  [22, '${speed}', 'variable.other'],
  [23, 'IF', 'keyword.control'],
  [24, 'Fail', 'support.function'],
  [25, 'ELSE', 'keyword.control'],
  [26, 'Connect', 'support.function'],
  [28, '# a comment line', 'comment.line'],
];

// Never: a keyword name taken for a call, an argument for a keyword.
const NOT = [
  [13, 'bench.dut.temp', 'support.function'],
  [14, '${value} <= ', 'support.function'],
  [16, 'tick', 'support.function'],
];

async function main() {
  const wasm = fs.readFileSync(path.join(app, 'node_modules.asar.unpacked', 'vscode-oniguruma', 'release', 'onig.wasm'));
  await onig.loadWASM(wasm.buffer.slice(wasm.byteOffset, wasm.byteOffset + wasm.byteLength));
  const registry = new textmate.Registry({
    onigLib: Promise.resolve({ createOnigScanner: (p) => new onig.OnigScanner(p), createOnigString: (s) => new onig.OnigString(s) }),
    loadGrammar: async () => textmate.parseRawGrammar(fs.readFileSync(GRAMMAR, 'utf8'), GRAMMAR),
  });
  const grammar = await registry.loadGrammar('source.robotframework');
  let state = textmate.INITIAL;
  const lines = SAMPLE.map((text) => {
    const r = grammar.tokenizeLine(text, state);
    state = r.ruleStack;
    return { text, tokens: r.tokens };
  });
  const scopesOf = (line, piece) => {
    const { text, tokens } = lines[line];
    const at = text.indexOf(piece);
    if (at < 0) throw new Error(`"${piece}" is not on line ${line}`);
    const covering = tokens.filter((t) => t.startIndex < at + piece.length && t.endIndex > at);
    return covering.map((t) => t.scopes.join(' '));
  };
  let failed = 0;
  for (const [line, piece, scope] of CHECKS) {
    const scopes = scopesOf(line, piece);
    const ok = scopes.length && scopes.every((s) => s.includes(scope));
    if (!ok) failed++;
    console.log(`${ok ? 'ok  ' : 'FAIL'} line ${line}: ${JSON.stringify(piece)} is ${scope}${ok ? '' : '  -- got ' + JSON.stringify(scopes)}`);
  }
  for (const [line, piece, scope] of NOT) {
    const scopes = scopesOf(line, piece);
    const ok = scopes.every((s) => !s.includes(scope));
    if (!ok) failed++;
    console.log(`${ok ? 'ok  ' : 'FAIL'} line ${line}: ${JSON.stringify(piece)} is not ${scope}${ok ? '' : '  -- got ' + JSON.stringify(scopes)}`);
  }
  console.log(`${CHECKS.length + NOT.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
