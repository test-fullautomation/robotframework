// Dialogs that edit the run groups of a test project: add a flow to a group
// (or a new group), add a member from the group's panel, rename, change a
// member's variables, remove. The edits go through backend/groupedit, which
// touches only the "groups" value of testproject.json.
import * as path from 'node:path';
import * as vscode from 'vscode';
import { GROUP_ID, MEMBER_ID, addMember, removeGroup, removeMember, renameGroup, setMemberVariables, targetOf } from './backend/groupedit';
import { Project, RunGroup } from './backend/project';
import { formatVariables, parseVariables } from './backend/run';

async function input(title: string, value: string, validate?: (v: string) => string | null, prompt?: string): Promise<string | undefined> {
  return vscode.window.showInputBox({ title, value, prompt, validateInput: validate ? (v) => validate(v.trim()) : undefined });
}

async function variablesInput(title: string, current: Record<string, string>): Promise<Record<string, string> | undefined> {
  const text = await vscode.window.showInputBox({
    title,
    prompt: 'NAME=value pairs separated by commas, passed as --variable to this member (optional).',
    value: formatVariables(current),
    placeHolder: 'RIG=A, PEER=B',
    validateInput: (v) => { try { parseVariables(v); return null; } catch (e) { return (e as Error).message; } },
  });
  return text === undefined ? undefined : parseVariables(text);
}

async function memberIdInput(group: RunGroup | undefined, file: string): Promise<string | undefined> {
  const stem = path.basename(file).replace(/\.flow\.json$/i, '').replace(/[^A-Za-z0-9_.-]+/g, '_').toUpperCase();
  const id = await input('Member id: the rig name of this flow in the group', stem, (v) =>
    !MEMBER_ID.test(v) ? "Letters, digits, '_', '.' and '-'." : group?.members.some((m) => m.id === v) ? 'The group has this member already.' : null);
  return id?.trim();
}

/** Add a flow file to a group of its project (picked, or created). Returns the fresh project and the group's id. */
export async function addFlowToGroup(project: Project, file: string): Promise<{ project: Project; groupId: string } | null> {
  const items: (vscode.QuickPickItem & { id: string })[] = project.groups.map((g) => ({
    label: g.title, description: g.members.map((m) => m.id).join(' + '), id: g.id,
  }));
  items.push({ label: '$(add) New run group…', id: '' });
  const pick = await vscode.window.showQuickPick(items, {
    title: `Add ${path.basename(file)} to a run group of ${project.name}`, placeHolder: 'The group to add it to',
  });
  if (!pick) return null;
  let groupId = pick.id;
  let newGroup: { title: string } | undefined;
  if (!groupId) {
    const id = await input('New run group: id', 'group', (v) =>
      !GROUP_ID.test(v) ? "Letters, digits, '_', '.' and '-'." : project.groups.some((g) => g.id === v) ? 'A group with this id exists.' : null);
    if (!id) return null;
    groupId = id.trim();
    const title = await input('New run group: title', groupId);
    if (title === undefined) return null;
    newGroup = { title: title.trim() || groupId };
  }
  const group = project.groups.find((g) => g.id === groupId);
  const memberId = await memberIdInput(group, file);
  if (!memberId) return null;
  const variables = await variablesInput(`Variables of ${memberId}`, {});
  if (variables === undefined) return null;
  const fresh = addMember(project, groupId, { id: memberId, target: targetOf(project, file), variables }, newGroup);
  return { project: fresh, groupId };
}

/** Pick a flow file of the project and add it to `group`. */
export async function addMemberToGroup(project: Project, group: RunGroup): Promise<Project | null> {
  const files = await vscode.workspace.findFiles(new vscode.RelativePattern(project.root, '**/*.flow.json'), '**/{node_modules,results,.git}/**', 200);
  const taken = new Set(group.members.map((m) => m.target));
  const items = files.map((f) => ({ label: targetOf(project, f.fsPath), file: f.fsPath }))
    .filter((i) => !taken.has(i.label)).sort((a, b) => a.label.localeCompare(b.label));
  if (!items.length) {
    void vscode.window.showInformationMessage(`Every flow file of ${project.name} is in ${group.title} already.`);
    return null;
  }
  const pick = await vscode.window.showQuickPick(items, { title: `Add a member to ${group.title}`, placeHolder: 'The flow file' });
  if (!pick) return null;
  const memberId = await memberIdInput(group, pick.file);
  if (!memberId) return null;
  const variables = await variablesInput(`Variables of ${memberId}`, {});
  if (variables === undefined) return null;
  return addMember(project, group.id, { id: memberId, target: pick.label, variables });
}

/** Rename the group, change a member's variables, remove a member or the group. */
export async function editGroup(project: Project, group: RunGroup): Promise<{ project: Project; removed: boolean } | null> {
  type Item = vscode.QuickPickItem & { action: string; member?: string };
  const items: Item[] = [{ label: '$(edit) Rename the group', description: group.title, action: 'rename' }];
  for (const m of group.members) {
    items.push({ label: `$(symbol-variable) Variables of ${m.id}`, description: formatVariables(m.variables) || '(none)', action: 'variables', member: m.id });
    items.push({ label: `$(trash) Remove ${m.id}`, description: m.target, action: 'remove', member: m.id });
  }
  items.push({ label: '$(trash) Delete the group', action: 'delete' });
  const pick = await vscode.window.showQuickPick(items, { title: `Edit run group ${group.title}` });
  if (!pick) return null;
  if (pick.action === 'rename') {
    const title = await input('Title of the group', group.title);
    if (title === undefined) return null;
    return { project: renameGroup(project, group.id, title.trim() || group.id), removed: false };
  }
  if (pick.action === 'variables') {
    const member = group.members.find((m) => m.id === pick.member)!;
    const variables = await variablesInput(`Variables of ${member.id}`, member.variables);
    if (variables === undefined) return null;
    return { project: setMemberVariables(project, group.id, member.id, variables), removed: false };
  }
  if (pick.action === 'remove') {
    const ok = await vscode.window.showWarningMessage(`Remove ${pick.member} from ${group.title}?`, { modal: true }, 'Remove');
    if (ok !== 'Remove') return null;
    const fresh = removeMember(project, group.id, pick.member!);
    return { project: fresh, removed: !fresh.groups.some((g) => g.id === group.id) };
  }
  const ok = await vscode.window.showWarningMessage(`Delete the run group ${group.title}? The flow files stay.`, { modal: true }, 'Delete');
  if (ok !== 'Delete') return null;
  return { project: removeGroup(project, group.id), removed: true };
}
