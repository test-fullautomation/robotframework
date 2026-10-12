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

"""Signal stores over a network, chosen by the scheme of ``ROBOT_FLOW_SIGNALS``.

- :class:`HttpStore` -- ``http://host:port/<run>``: the signal server of
  ``python -m robot.flow server`` (standard library only, on both sides).
- :class:`RedisStore` -- ``redis://host:port/0?ns=<run>``: a Redis server
  (needs the ``redis`` package).
- :class:`MqttStore` -- ``mqtt://host:port/<run>``: an MQTT broker with
  retained messages (needs the ``paho-mqtt`` package).

All three keep the contract of :class:`robot.flow.signals.SignalStore`. A
system of your own goes in a class named by ``ROBOT_FLOW_SIGNALS_BACKEND``.
"""

import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .signals import SignalStore, _seq


class HttpStore(SignalStore):
    """Signals on the signal server; ``url`` is ``http://host:port/<run>``."""

    TIMEOUT_S = 5.0
    supports_wait = True

    def __init__(self, url):
        parts = urlsplit(str(url).strip())
        run = parts.path.strip('/')
        if not parts.netloc or not run or '/' in run:
            raise ValueError(f"Signal store '{url}' must be http://host:port/<run name>.")
        self.where = urlunsplit((parts.scheme, parts.netloc, '/' + run, '', ''))
        self._base = self.where + '/'

    def get(self, name):
        try:
            return self._request('GET', self._base + quote(name, safe=''))
        except HTTPError as err:
            if err.code == 404:
                return None
            raise RuntimeError(f"Signal server {self.where}: {err}") from None
        except URLError as err:
            raise RuntimeError(f"Signal server {self.where} cannot be reached: {err.reason}") from None

    def set(self, name, value):
        try:
            self._request('PUT', self._base + quote(name, safe=''), {'value': value})
        except HTTPError as err:
            raise RuntimeError(f"Signal server {self.where}: {err}") from None
        except URLError as err:
            raise RuntimeError(f"Signal server {self.where} cannot be reached: {err.reason}") from None

    def read(self):
        try:
            return self._request('GET', self._base)
        except URLError as err:
            raise RuntimeError(f"Signal server {self.where} cannot be reached: "
                               f"{getattr(err, 'reason', err)}") from None

    def wait(self, seq, timeout):
        try:
            self._request('GET', f'{self._base}?since={int(seq)}&wait={float(timeout)}',
                          timeout=self.TIMEOUT_S + float(timeout))
        except (URLError, OSError, ValueError):
            time.sleep(min(float(timeout), 1.0))     # the server is away: do not spin

    def _request(self, method, url, payload=None, timeout=None):
        data = json.dumps(payload).encode('UTF-8') if payload is not None else None
        request = Request(url, data=data, method=method,
                          headers={'Content-Type': 'application/json'} if data else {})
        with urlopen(request, timeout=timeout or self.TIMEOUT_S) as response:
            return json.loads(response.read().decode('UTF-8'))


class RedisStore(SignalStore):
    """Signals in a Redis hash; ``url`` is ``redis://host:port/<db>?ns=<run>``.

    The hash ``robot.flow:<run>`` holds the entries, ``robot.flow:<run>:seq``
    is the counter every write increments, so the server orders the writes.
    """

    def __init__(self, url):
        try:
            import redis
        except ImportError:
            raise ImportError("A redis:// signal store needs the 'redis' package "
                              "(pip install redis).") from None
        parts = urlsplit(str(url).strip())
        query = parse_qs(parts.query)
        run = (query.pop('ns', None) or query.pop('run', None) or ['default'])[0]
        plain = urlunsplit((parts.scheme, parts.netloc, parts.path,
                            '&'.join(f'{k}={v[-1]}' for k, v in query.items()), ''))
        self._redis = redis.Redis.from_url(plain, decode_responses=True)
        self._hash = f'robot.flow:{run}'
        self._counter = f'robot.flow:{run}:seq'
        self.where = f'{plain}#{run}'

    def get(self, name):
        raw = self._redis.hget(self._hash, name)
        return json.loads(raw) if raw else None

    def set(self, name, value):
        seq = self._redis.incr(self._counter)
        self._redis.hset(self._hash, name, json.dumps(self.entry(value, seq)))

    def read(self):
        return {name: json.loads(raw) for name, raw in self._redis.hgetall(self._hash).items()}

    def last_seq(self):
        return int(self._redis.get(self._counter) or 0)

    def close(self):
        self._redis.close()


class MqttStore(SignalStore):
    """Signals as retained MQTT messages; ``url`` is ``mqtt://[user:pw@]host:port/<run>``.

    Each signal is the retained message of topic ``<run>/<name>``, so a
    process that joins late receives every current value on subscribing.
    The broker assigns no sequence number, so the publisher's clock is used
    (nanoseconds): machines of a run should share NTP.
    """

    CONNECT_TIMEOUT_S = 5.0
    SETTLE_S = 0.3          # time for the retained messages to arrive after subscribing
    supports_wait = True

    def __init__(self, url):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise ImportError("An mqtt:// signal store needs the 'paho-mqtt' package "
                              "(pip install paho-mqtt).") from None
        parts = urlsplit(str(url).strip())
        run = parts.path.strip('/')
        if not parts.hostname or not run:
            raise ValueError(f"Signal store '{url}' must be mqtt://host:port/<run name>.")
        self._prefix = run + '/'
        self.where = f'{parts.scheme}://{parts.hostname}:{parts.port or 1883}/{run}'
        self._data = {}
        self._cond = threading.Condition()
        self._connected = threading.Event()
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:                      # paho-mqtt 1.x
            self._client = mqtt.Client()
        if parts.username:
            self._client.username_pw_set(parts.username, parts.password)
        if parts.scheme == 'mqtts':
            self._client.tls_set()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(parts.hostname, parts.port or (8883 if parts.scheme == 'mqtts' else 1883))
        self._client.loop_start()
        if not self._connected.wait(self.CONNECT_TIMEOUT_S):
            self._client.loop_stop()
            raise RuntimeError(f'MQTT broker {self.where} did not answer in '
                               f'{self.CONNECT_TIMEOUT_S} s.')
        time.sleep(self.SETTLE_S)

    def _on_connect(self, client, *args):
        client.subscribe(self._prefix + '#', qos=1)
        self._connected.set()

    def _on_message(self, client, userdata, message):
        name = message.topic[len(self._prefix):].replace('%2F', '/')
        try:
            entry = json.loads(message.payload.decode('UTF-8')) if message.payload else None
        except ValueError:
            return
        with self._cond:
            if entry is None:
                self._data.pop(name, None)
            elif _seq(entry) >= _seq(self._data.get(name)):
                self._data[name] = entry
            self._cond.notify_all()

    def get(self, name):
        with self._cond:
            return self._data.get(name)

    def set(self, name, value):
        entry = self.entry(value, time.time_ns())
        topic = self._prefix + name.replace('/', '%2F')
        info = self._client.publish(topic, json.dumps(entry), qos=1, retain=True)
        info.wait_for_publish(self.CONNECT_TIMEOUT_S)
        with self._cond:                 # visible to this process at once
            self._data[name] = entry
            self._cond.notify_all()

    def read(self):
        with self._cond:
            return dict(self._data)

    def wait(self, seq, timeout):
        with self._cond:
            self._cond.wait_for(lambda: self.last_seq() > seq, float(timeout))

    def last_seq(self):
        return max((_seq(entry) for entry in self._data.values()), default=0)

    def close(self):
        self._client.loop_stop()
        self._client.disconnect()
