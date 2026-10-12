// Small pieces both kinds of views use: the toolbar's state and finding a
// line or a flow node in a file's text.
import * as vscode from 'vscode';
import { describeFlow } from './backend/control';
import { RunState } from './runs';

export type Zoom = 'fit' | 'natural' | number;
export type Motion = 'tail' | 'hop' | 'off';

export interface LiveOptions {
  zoom: Zoom;
  motion: Motion;
}

/** A toolbar option from the webview ('0.75' -> 0.75). */
export function setOption(opts: LiveOptions, name: string, value: string): void {
  if (name === 'zoom') opts.zoom = value === 'fit' || value === 'natural' ? value : Number(value) || 'fit';
  if (name === 'motion' && (value === 'tail' || value === 'hop' || value === 'off')) opts.motion = value;
}

function seconds(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} min`;
}

export function statusText(state: RunState | null): string {
  if (!state) return '';
  const took = seconds((state.ended ?? Date.now()) - state.started);
  if (state.paused && state.status === 'running') {
    const why = state.paused.reason === 'breakpoint' ? 'breakpoint' : state.paused.reason === 'exception' ? 'failure' : 'step';
    return `Paused (${why})${state.paused.node ? ' at ' + state.paused.node : ''} · ${took}`;
  }
  const flow = state.status === 'running' && state.members.length === 1 ? describeFlow(state.flow) : null;
  if (flow && flow.paused) return `${state.step ? 'Step mode: paused' : 'Paused'}${flow.text.replace(/^paused/, '')} · ${took}`;
  switch (state.status) {
    case 'running': return `Running${flow && flow.text.includes(' · ') ? flow.text.slice(flow.text.indexOf(' · ')) : ''} · ${took}`;
    case 'stopping': return 'Stopping…';
    case 'passed': return `Passed · ${took}`;
    case 'failed': return `Failed · ${took}`;
    case 'stopped': return `Stopped · ${took}`;
    default: return 'Could not run';
  }
}

export function toolbar(state: RunState | null, opts: LiveOptions & { canRun: boolean; runTitle: string; live: boolean; hasLog: boolean;
                                                                       hasFlowReport?: boolean; canDebug?: boolean; canStep?: boolean }) {
  const running = state?.status === 'running';
  const flow = running && state!.pausable ? describeFlow(state!.flow) : null;
  const group = !!state && (state.members.length > 1 || !state.members.includes('main'));
  return {
    canRun: opts.canRun,
    canDebug: !!opts.canDebug,
    /** Step mode is offered: a flow file. */
    canStep: !!opts.canStep,
    /** Pause / Resume: a running flow whose processes have said they are there. */
    pausable: !!flow,
    /** Every process is paused: Resume (in step mode: the next step). */
    flowPaused: !!flow && flow.paused === flow.total,
    step: !!state?.step,
    /** Stop goes through the flow (a checkpoint is written). */
    flowStop: running && !!state!.pausable && !state!.debug,
    /** A new run can continue the last one from its checkpoint. */
    canContinue: !!state?.checkpoint && !running && state.status !== 'stopping',
    /** A group run's members, each paused and resumed on its own. */
    members: group && flow ? state!.members.map((id) => {
      const m = describeFlow(state!.flow, id);
      return { id, text: m ? m.text : 'starting', paused: !!m && m.paused > 0 };
    }) : [],
    debugging: !!state?.debug && (state.status === 'running' || state.status === 'stopping'),
    paused: !!state?.paused,
    runTitle: opts.runTitle,
    status: state?.status ?? null,
    text: statusText(state),
    live: opts.live,
    zoom: String(opts.zoom),
    motion: opts.motion,
    hasLog: opts.hasLog,
    /** A flow run wrote flow.html: the plan as drawn with the results on it. */
    hasFlowReport: !!opts.hasFlowReport,
  };
}

/** The 1-based line of a flow node's `"id": "<node>"` in a flow file's text, or null. */
export function nodeLine(text: string, node: string): number | null {
  const id = String(node).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`"id"\\s*:\\s*"${id}"`).exec(text);
  return m ? text.slice(0, m.index).split('\n').length : null;
}

/** Show a line (1-based) or a flow node of a document in a text editor, beside the views. */
export async function revealIn(document: vscode.TextDocument, target: { line?: unknown; node?: unknown }): Promise<void> {
  let line = typeof target.line === 'number' ? target.line : null;
  if (line === null && target.node != null) line = nodeLine(document.getText(), String(target.node));
  const pos = new vscode.Position(Math.max(0, (line || 1) - 1), 0);
  const shown = vscode.window.visibleTextEditors.find((e) => e.document === document);
  const editor = await vscode.window.showTextDocument(document, {
    viewColumn: shown ? shown.viewColumn : vscode.ViewColumn.Beside,
    preserveFocus: false,
    selection: new vscode.Range(pos, pos),
  });
  editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenterIfOutsideViewport);
}
