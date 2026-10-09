// Copy the parts shared with the Manager GUI into this extension:
//   media/vendor/flow-view/   the Diagram view (web/plugins/flow-view)
//   media/vendor/robot-grid/  the Grid view    (web/plugins/robot-grid)
//   python/                   the runner's helpers (adapters/test_project):
//                             reading and editing, robot_boot.py (a run that
//                             stops gracefully), flow_position.py (where a
//                             flow run is, for the live Diagram) and
//                             flow_debug.py (the debugger's listener)
// They stay one code base: change them in the Manager GUI repo
// (python-microservice-base), then run
//   MB_GUI_REPO=<its checkout> npm run sync
// media/vendor/SOURCE.json records the commit they came from. The copies are
// committed here, so building the extension needs only this repository.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
if (!process.env.MB_GUI_REPO) {
  console.error('Set MB_GUI_REPO to a checkout of python-microservice-base (the Manager GUI).');
  process.exit(1);
}
const REPO = path.resolve(process.env.MB_GUI_REPO);

const COPIES = [
  { from: 'MicroserviceBase/MicroserviceManagerGUI/web/plugins/flow-view', to: 'media/vendor/flow-view', only: /\.js$/ },
  { from: 'MicroserviceBase/MicroserviceManagerGUI/web/plugins/robot-grid', to: 'media/vendor/robot-grid', only: /\.js$/ },
  {
    from: 'MicroserviceBase/adapters/test_project', to: 'python',
    only: /^(robot_grid|flow_inspect|flow_edit|flow_position|flow_debug|robot_boot)\.py$/,
  },
];

if (!fs.existsSync(path.join(REPO, 'MicroserviceBase'))) {
  console.error(`Not a python-microservice-base checkout: ${REPO} (set MB_GUI_REPO)`);
  process.exit(1);
}

const git = (...args) => {
  try { return execFileSync('git', ['-C', REPO, ...args], { encoding: 'utf8' }).trim(); } catch { return null; }
};

const copied = [];
for (const c of COPIES) {
  const src = path.join(REPO, c.from);
  const dst = path.join(ROOT, c.to);
  fs.mkdirSync(dst, { recursive: true });
  for (const name of fs.readdirSync(src).sort()) {
    if (!c.only.test(name)) continue;
    fs.copyFileSync(path.join(src, name), path.join(dst, name));
    copied.push(`${c.to}/${name}`);
  }
}

const dirty = git('status', '--porcelain', '--', ...COPIES.map((c) => c.from));
const source = {
  repo: 'https://github.com/test-fullautomation/python-microservice-base',
  commit: git('rev-parse', 'HEAD'),
  uncommittedChanges: dirty ? dirty.split('\n').length : 0,
  syncedAt: new Date().toISOString(),
  files: copied,
};
fs.writeFileSync(path.join(ROOT, 'media/vendor/SOURCE.json'), JSON.stringify(source, null, 2) + '\n');
console.log(`Copied ${copied.length} files from ${REPO} @ ${source.commit?.slice(0, 9)}` +
            (source.uncommittedChanges ? ` (+${source.uncommittedChanges} uncommitted)` : ''));
