#  Copyright 2008-2015 Nokia Networks
#  Copyright 2016-     Robot Framework Foundation
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

"""The signal server: the signals of runs whose flows are on several machines.

``python -m robot.flow server --port 8765`` on any machine of the bench; the
members of a run then use ``ROBOT_FLOW_SIGNALS=http://<that machine>:8765/<run>``
(one name per run, so two runs never read each other's signals). Nothing but
Python is needed on either side.

The HTTP interface, for other tools that want to read or set signals:

================================  ==========================================
``GET /<run>/``                   every entry of the run as a JSON object;
                                  ``?since=<seq>&wait=<seconds>`` returns as
                                  soon as an entry newer than ``seq`` exists
                                  (a long poll, at most 30 seconds)
``GET /<run>/<name>``             the entry, or 404
``PUT /<run>/<name>``             body ``{"value": ...}``; returns the entry
                                  with its sequence number
``DELETE /<run>/``                forgets the run
``GET /``                         the runs the server knows
================================  ==========================================

An entry is ``{"value": ..., "time": epoch seconds, "seq": n}``; ``seq``
counts every write to the server, so it orders the writes of all machines
without comparing their clocks. With ``--file`` the server keeps its state
in that file across restarts.

The server listens on the loopback interface unless ``--host`` says
otherwise, and with a token (``--token``, or ROBOT_FLOW_SIGNALS_TOKEN in the
environment of the server and of every client) every request must carry
``Authorization: Bearer <token>``: whoever can reach the server can pause,
stop and change a run, so on a bench network the token is the fence.
"""

import argparse
import hmac
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


MAX_WAIT_S = 30.0
MAX_BODY_BYTES = 64 * 1024
TOKEN_VARIABLE = 'ROBOT_FLOW_SIGNALS_TOKEN'
DEFAULT_HOST = '127.0.0.1'


def token_from_environment():
    return os.environ.get(TOKEN_VARIABLE) or None


class SignalServer:
    """The state behind the HTTP interface; usable in-process by tests.

    ``token``: required of every request when given (``None``: the
    environment's ROBOT_FLOW_SIGNALS_TOKEN, if set; ``''``: none).
    """

    def __init__(self, host=DEFAULT_HOST, port=8765, file=None, token=None):
        self._runs = {}             # run name -> {signal name -> entry}
        self._seq = 0
        self._cond = threading.Condition()
        self._file = os.path.abspath(file) if file else None
        self.token = token_from_environment() if token is None else (token or None)
        self._load()
        handler = type('SignalHandler', (_Handler,), {'signals': self})
        self._server = ThreadingHTTPServer((host, port), handler)
        self._server.daemon_threads = True
        self.host, self.port = self._server.server_address[:2]

    @property
    def url(self):
        host = 'localhost' if self.host in ('0.0.0.0', '::', '') else self.host
        return f'http://{host}:{self.port}'

    # ------------------------------------------------------------- state

    def get(self, run, name):
        with self._cond:
            return self._runs.get(run, {}).get(name)

    def set(self, run, name, value):
        with self._cond:
            self._seq += 1
            entry = {'value': value, 'time': time.time(), 'seq': self._seq}
            self._runs.setdefault(run, {})[name] = entry
            self._cond.notify_all()
            self._save()
            return entry

    def read(self, run):
        with self._cond:
            return dict(self._runs.get(run, {}))

    def wait(self, run, since, timeout):
        """The entries of ``run`` once one newer than ``since`` exists, or after ``timeout``."""
        timeout = max(0.0, min(float(timeout), MAX_WAIT_S))
        with self._cond:
            self._cond.wait_for(lambda: self._newest(run) > since, timeout)
            return dict(self._runs.get(run, {}))

    def clear(self, run):
        with self._cond:
            self._runs.pop(run, None)
            self._save()

    def runs(self):
        with self._cond:
            return {run: {'signals': len(entries), 'seq': self._newest(run)}
                    for run, entries in self._runs.items()}

    def _newest(self, run):
        return max((e.get('seq', 0) for e in self._runs.get(run, {}).values()), default=0)

    def _load(self):
        if not self._file or not os.path.exists(self._file):
            return
        with open(self._file, encoding='UTF-8') as file:
            data = json.load(file)
        self._runs = data.get('runs', {})
        self._seq = int(data.get('seq', 0))

    def _save(self):
        if not self._file:
            return
        temp = f'{self._file}.{os.getpid()}.tmp'
        with open(temp, 'w', encoding='UTF-8') as file:
            json.dump({'seq': self._seq, 'runs': self._runs}, file)
        os.replace(temp, self._file)

    # ------------------------------------------------------------ serving

    def serve_forever(self):
        self._serving = True
        try:
            self._server.serve_forever()
        finally:
            self._serving = False

    def start(self):
        """Serve on a thread; for tests and for embedding."""
        thread = threading.Thread(target=self.serve_forever, name='FlowSignalServer',
                                  daemon=True)
        thread.start()
        return thread

    def shutdown(self):
        # Python's shutdown() waits for serve_forever to notice; it never
        # returns for a server that was not serving.
        if getattr(self, '_serving', False):
            self._server.shutdown()
        self._server.server_close()


class _BadRequest(Exception):

    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


class _Handler(BaseHTTPRequestHandler):
    signals = None          # the SignalServer, set on the subclass
    protocol_version = 'HTTP/1.1'
    # A connection that sends nothing for longer than a long poll is dropped:
    # the thread it holds is not kept for ever.
    timeout = MAX_WAIT_S + 5

    def log_message(self, format, *args):
        pass                # quiet: a gate polls every two seconds

    def handle(self):
        try:
            super().handle()
        except (TimeoutError, OSError):
            pass            # the client went away, or sat silent too long

    def _authorized(self):
        token = self.signals.token
        if not token:
            return True
        given = self.headers.get('Authorization') or ''
        # Compared as bytes: the constant-time comparison takes ASCII text only.
        if hmac.compare_digest(given.strip().encode('UTF-8'), f'Bearer {token}'.encode('UTF-8')):
            return True
        # The body, if any, is not read: the connection goes, so it cannot
        # be taken for the next request.
        self.close_connection = True
        self._send(401, {'error': 'a token is required: Authorization: Bearer <token>'})
        return False

    def _parts(self):
        parts = urlsplit(self.path)
        segments = [unquote(s) for s in parts.path.split('/') if s]
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        return segments, query

    def _send(self, status, payload):
        body = json.dumps(payload).encode('UTF-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise _BadRequest(400, 'Content-Length must be an integer')
        if length < 0:
            raise _BadRequest(400, 'Content-Length must not be negative')
        if length > MAX_BODY_BYTES:
            raise _BadRequest(413, f'the body may hold at most {MAX_BODY_BYTES} bytes')
        raw = self.rfile.read(length) if length else b''
        try:
            return json.loads(raw.decode('UTF-8')) if raw else {}
        except ValueError:
            return None

    def do_GET(self):
        if not self._authorized():
            return None
        segments, query = self._parts()
        if not segments:
            return self._send(200, self.signals.runs())
        run = segments[0]
        if len(segments) == 1:
            if 'since' in query:
                try:
                    since, wait = int(query['since']), float(query.get('wait', 0))
                except ValueError:
                    return self._send(400, {'error': 'since must be an integer, wait a number'})
                return self._send(200, self.signals.wait(run, since, wait))
            return self._send(200, self.signals.read(run))
        entry = self.signals.get(run, '/'.join(segments[1:]))
        if entry is None:
            return self._send(404, {'error': 'no such signal'})
        return self._send(200, entry)

    def do_PUT(self):
        if not self._authorized():
            return None
        segments, _ = self._parts()
        if len(segments) < 2:
            return self._send(400, {'error': 'PUT /<run>/<name>'})
        try:
            body = self._body()
        except _BadRequest as err:
            self.close_connection = True    # the body was not read: nothing follows it
            return self._send(err.status, {'error': err.error})
        if not isinstance(body, dict) or 'value' not in body:
            return self._send(400, {'error': 'the body must be {"value": ...}'})
        entry = self.signals.set(segments[0], '/'.join(segments[1:]), body['value'])
        return self._send(200, entry)

    do_POST = do_PUT

    def do_DELETE(self):
        if not self._authorized():
            return None
        segments, _ = self._parts()
        if len(segments) != 1:
            return self._send(400, {'error': 'DELETE /<run>/'})
        self.signals.clear(segments[0])
        return self._send(200, {'cleared': segments[0]})


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m robot.flow server',
                                     description='The signal server for flows on several machines.')
    parser.add_argument('--host', default=DEFAULT_HOST,
                        help=f'interface to listen on (default: {DEFAULT_HOST}, this machine '
                             f'only; 0.0.0.0: every interface, then set a token)')
    parser.add_argument('--port', type=int, default=8765, help='port (default: 8765; 0: any free one)')
    parser.add_argument('--file', help='keep the signals in this file across restarts')
    parser.add_argument('--token', help=f'the token every request must carry as '
                                        f'"Authorization: Bearer <token>" (default: '
                                        f'{TOKEN_VARIABLE} of the environment, if set)')
    parser.add_argument('--open', action='store_true',
                        help='serve the network without a token (anyone who reaches '
                             'the server may then control every run on it)')
    args = parser.parse_args(argv)
    return serve(args.host, args.port, args.file, args.token, args.open)


def _loopback(host):
    # '' binds every interface, as 0.0.0.0 does.
    return host in ('127.0.0.1', 'localhost', '::1')


def serve(host=DEFAULT_HOST, port=8765, file=None, token=None, open_=False):
    """Run the server until Ctrl+C; the exit code of the command line."""
    if not _loopback(host) and not (token or token_from_environment()) and not open_:
        sys.stderr.write(f'Refusing to serve {host} without a token: anyone who reaches '
                         f'the server could control every run on it. Give --token (or '
                         f'{TOKEN_VARIABLE}), or --open to serve without one.\n')
        return 1
    try:
        server = SignalServer(host, port, file, token)
    except OSError as err:
        sys.stderr.write(f'Cannot listen on {host}:{port}: {err}\n')
        return 1
    fence = 'a token is required' if server.token else 'no token: anyone who reaches it may control the runs'
    print(f'Signal server at {server.url}/<run>  (ROBOT_FLOW_SIGNALS={server.url}/<run> '
          f'on every machine of the run; {fence}; Ctrl+C stops it)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
