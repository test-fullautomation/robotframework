// Editing the run groups of a test project (testproject.json) from the
// editor. Only the "groups" value changes: the rest of the file -- the Manager
// GUI's own settings, formatting, key order -- stays byte for byte, so both
// tools can keep writing the same file. No VS Code API here; test/ runs it
// with plain Node.
import * as fs from 'node:fs';
import * as path from 'node:path';
import { Project, readProject } from './project';

/** A group as it is in the file: known keys typed, other keys kept. */
export interface RawMember { id: string; target: string; variables?: Record<string, string>; [k: string]: unknown }
export interface RawGroup { id: string; title?: string; members: RawMember[]; env?: Record<string, string>; [k: string]: unknown }

export const GROUP_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;
export const MEMBER_ID = /^[A-Za-z0-9][A-Za-z0-9_.-]*$/;

/** The span of the top-level "groups" array in the text, or null when there is none. */
export function groupsSpan(text: string): { start: number; end: number } | null {
  // Walk the top-level object: strings are skipped, nesting is tracked.
  let depth = 0, i = 0;
  const n = text.length;
  const skipString = () => { i++; while (i < n) { if (text[i] === '\\') i += 2; else if (text[i] === '"') { i++; return; } else i++; } };
  while (i < n) {
    const c = text[i];
    if (c === '"') {
      const keyStart = i;
      skipString();
      if (depth === 1 && text.slice(keyStart, i) === '"groups"') {
        let j = i;
        while (j < n && /\s/.test(text[j])) j++;
        if (text[j] !== ':') continue;
        j++;
        while (j < n && /\s/.test(text[j])) j++;
        if (text[j] !== '[') return null;
        // the value: match brackets, skipping strings
        let d = 0, k = j;
        for (; k < n; k++) {
          const ch = text[k];
          if (ch === '"') { i = k; skipString(); k = i - 1; continue; }
          if (ch === '[' || ch === '{') d++;
          else if (ch === ']' || ch === '}') { d--; if (d === 0) return { start: j, end: k + 1 }; }
        }
        return null;
      }
      continue;
    }
    if (c === '{' || c === '[') depth++;
    else if (c === '}' || c === ']') depth--;
    i++;
  }
  return null;
}

/** The groups as they are in the file (unknown keys included). */
export function readRawGroups(projectFile: string): RawGroup[] {
  const text = fs.readFileSync(projectFile, 'utf8');
  const span = groupsSpan(text);
  if (!span) return [];
  const value = JSON.parse(text.slice(span.start, span.end));
  return Array.isArray(value) ? value : [];
}

function indentOf(text: string): string {
  const m = /\n([ \t]+)"/.exec(text);
  return m ? m[1] : '  ';
}

/** Replace the "groups" value in the file (added at the end of the object when missing). */
export function writeRawGroups(projectFile: string, groups: RawGroup[]): void {
  const text = fs.readFileSync(projectFile, 'utf8');
  const unit = indentOf(text);
  const eol = text.includes('\r\n') ? '\r\n' : '\n';
  const json = JSON.stringify(groups, null, unit).split('\n').map((line, i) => (i ? unit + line : line)).join(eol);
  const span = groupsSpan(text);
  let out: string;
  if (span) {
    out = text.slice(0, span.start) + json + text.slice(span.end);
  } else {
    const close = text.lastIndexOf('}');
    if (close < 0) throw new Error(`${projectFile} is not a JSON object.`);
    const before = text.slice(0, close).replace(/\s+$/, '');
    const comma = /\{\s*$/.test(before) ? '' : ',';
    out = `${before}${comma}${eol}${unit}"groups": ${json}${eol}${text.slice(close)}`;
  }
  JSON.parse(out);     // never write a file the Manager GUI could not read
  fs.writeFileSync(projectFile, out, 'utf8');
}

/** A file's "target" for a member: relative to the project root, '/'-separated. */
export function targetOf(project: Project, file: string): string {
  const rel = path.relative(project.root, path.resolve(file)).split(path.sep).join('/');
  if (rel.startsWith('..') || path.isAbsolute(rel)) throw new Error(`${file} is not inside the project ${project.root}.`);
  return rel;
}

export function projectFileOf(project: Project): string {
  return path.join(project.root, 'testproject.json');
}

function edit(project: Project, change: (groups: RawGroup[]) => void): Project {
  const file = projectFileOf(project);
  const groups = readRawGroups(file);
  change(groups);
  writeRawGroups(file, groups);
  const fresh = readProject(file);
  if (!fresh) throw new Error(`${file} could not be read back.`);
  return fresh;
}

function find(groups: RawGroup[], groupId: string): RawGroup {
  const group = groups.find((g) => g.id === groupId);
  if (!group) throw new Error(`No run group '${groupId}'.`);
  return group;
}

/** Add a member to a group; `newGroup` creates the group first. */
export function addMember(project: Project, groupId: string, member: RawMember, newGroup?: { title: string }): Project {
  if (!MEMBER_ID.test(member.id)) throw new Error(`'${member.id}' is not a member id: letters, digits, '_', '.', '-'.`);
  return edit(project, (groups) => {
    let group = groups.find((g) => g.id === groupId);
    if (!group) {
      if (!newGroup) throw new Error(`No run group '${groupId}'.`);
      if (!GROUP_ID.test(groupId)) throw new Error(`'${groupId}' is not a group id: letters, digits, '_', '.', '-'.`);
      group = { id: groupId, title: newGroup.title || groupId, members: [] };
      groups.push(group);
    }
    if (!Array.isArray(group.members)) group.members = [];
    if (group.members.some((m) => m.id === member.id)) throw new Error(`Group '${groupId}' already has a member '${member.id}'.`);
    const entry: RawMember = { id: member.id, target: member.target };
    if (member.variables && Object.keys(member.variables).length) entry.variables = member.variables;
    group.members.push(entry);
  });
}

export function removeMember(project: Project, groupId: string, memberId: string): Project {
  return edit(project, (groups) => {
    const group = find(groups, groupId);
    const n = group.members.length;
    group.members = group.members.filter((m) => m.id !== memberId);
    if (group.members.length === n) throw new Error(`Group '${groupId}' has no member '${memberId}'.`);
    if (!group.members.length) groups.splice(groups.indexOf(group), 1);   // an empty group is no group
  });
}

export function setMemberVariables(project: Project, groupId: string, memberId: string, variables: Record<string, string>): Project {
  return edit(project, (groups) => {
    const member = find(groups, groupId).members.find((m) => m.id === memberId);
    if (!member) throw new Error(`Group '${groupId}' has no member '${memberId}'.`);
    if (Object.keys(variables).length) member.variables = variables;
    else delete member.variables;
  });
}

export function renameGroup(project: Project, groupId: string, title: string): Project {
  return edit(project, (groups) => { find(groups, groupId).title = title; });
}

export function removeGroup(project: Project, groupId: string): Project {
  return edit(project, (groups) => { groups.splice(groups.indexOf(find(groups, groupId)), 1); });
}
