# Signal stores — where the flows of a run meet

**One store per run; a file on one PC, a server for several PCs, or a system
of your own.**
{ .lead }

Two flows that run together -- the members of a run group -- coordinate
through [signals](flow.md#two-flows): one *sets* a name, the other *waits*
for it with a gate. The signals live in the run's **store**, named by the
`ROBOT_FLOW_SIGNALS` environment variable, and the same store is the run's
control channel: `python -m robot.flow control <store> pause | resume |
stop | set | snapshot | status` works on every kind of store the same way.

A store is *shared state*, not a message stream: it keeps the **last value
of each name**, with the time it was set and a **sequence number** the store
assigns to every write. A flow that joins late reads what was set before it
came; the control channel uses the sequence numbers to tell "given since this
process started" without comparing the clocks of two machines.

| `ROBOT_FLOW_SIGNALS` | store | needs |
|---|---|---|
| a path, e.g. `results/run42/signals.json` | a JSON file shared by the processes of the run on one machine -- the default | nothing |
| `http://<host>:8765/run42` | the [signal server](#the-signal-server) on any machine of the bench | nothing (standard library on both sides) |
| `redis://<host>:6379/0?ns=run42` | a [Redis](#redis) server | `pip install redis` |
| `mqtt://[user:pw@]<host>:1883/run42` | an [MQTT broker](#mqtt-and-rabbitmq) -- Mosquitto, or RabbitMQ with its MQTT plugin | `pip install paho-mqtt` |
| anything, with `ROBOT_FLOW_SIGNALS_BACKEND=package.module:Class` | [a store of your own](#a-store-of-your-own) | your class |

Whatever the store, use a **fresh name per run** (`run42`, `run43`; a new
file): two runs must never read each other's signals.

## The signal server

For flows on several machines with nothing to install: start the server on
any machine of the bench and point every member at it.

```bash
# on bench PC "hub": reachable from the bench network, with a token
export ROBOT_FLOW_SIGNALS_TOKEN=bench-7-secret
python -m robot.flow server --host 0.0.0.0 --port 8765    # --file signals.json keeps the state across restarts

# on every machine of the run, the run's own name in the URL and the same token
export ROBOT_FLOW_SIGNALS_TOKEN=bench-7-secret
export ROBOT_FLOW_SIGNALS=http://hub:8765/run42
robot --parser robot.flow --variable FLOW_RIG:RIG_A plan_a.flow.json     # on PC A
robot --parser robot.flow --variable FLOW_RIG:RIG_B plan_b.flow.json     # on PC B

# from anywhere
python -m robot.flow control http://hub:8765/run42 status
python -m robot.flow control http://hub:8765/run42 pause --rig RIG_B
```

The server assigns one sequence number to every write, keeps one namespace
per run, and answers *long polls*, so a command reaches a flow as soon as it
is given instead of at the next poll. Its interface is plain JSON for any
other tool of the bench:

| | |
|---|---|
| `GET /run42/` | every entry; `?since=<seq>&wait=<seconds>` returns as soon as something newer exists (at most 30 s) |
| `GET /run42/<name>` | one entry, or 404 |
| `PUT /run42/<name>` with `{"value": ...}` | sets it; returns the entry with its `seq` |
| `DELETE /run42/` | forgets the run |
| `GET /` | the runs the server knows |

A member that cannot reach the server fails its signal step with a message
naming the server; a gate keeps polling until its timeout, so a server that
comes back in time is not a failure.

Whoever can reach the server can pause, stop and change every run on it, so
by default it listens on `127.0.0.1` only, for flows on the same machine.
`--host 0.0.0.0` opens it to the network, and then a token is required:
`--token` or `ROBOT_FLOW_SIGNALS_TOKEN` in its environment, and the same
variable on every machine of the run and wherever `robot.flow control` is
used. Every request then carries `Authorization: Bearer <token>`, and one
without it is refused (401). Without a token the server refuses to serve a
network interface, unless `--open` says that is meant. A request body is
limited to 64 KiB, and a connection that stays silent longer than a long
poll is dropped.

A `set` from outside becomes the value of a variable in the running flow,
and a condition written as `${LIMIT} > 3` inlines that value into the
Python expression Robot evaluates. Write conditions with the `$LIMIT` form,
which passes the value as an object, when the store is open to others.

## Redis

`redis://host:6379/0?ns=run42` (`rediss://` for TLS). The run's signals are
one hash, `robot.flow:run42`; the sequence numbers come from the counter
`robot.flow:run42:seq`, which the server increments, so writes from every
machine are ordered by the server. Redis keeps the state across runs: the
`ns` name is what keeps runs apart.

## MQTT and RabbitMQ

`mqtt://[user:pw@]host:1883/run42` (`mqtts://` for TLS, port 8883). Each
signal is the **retained message** of the topic `run42/<name>`, so a member
that subscribes late receives every current value at once. This works with
any broker that keeps retained messages: Mosquitto, or **RabbitMQ with its
MQTT plugin**:

```bash
# on the RabbitMQ host, once
rabbitmq-plugins enable rabbitmq_mqtt                      # listens on 1883
rabbitmqctl add_user flow <password>
rabbitmqctl set_permissions -p / flow ".*" ".*" ".*"       # the plugin uses vhost "/" by default

# on every machine of the run
pip install paho-mqtt
export ROBOT_FLOW_SIGNALS=mqtt://flow:<password>@<rabbit host>:1883/run42
```

Two limits of the MQTT route, which the other stores do not have:

- **The order comes from the publishers' clocks.** A broker assigns no
  sequence number, so the store uses the publishing machine's time in
  nanoseconds. Keep the machines of a run on NTP.
- **Retained messages are node-local in a RabbitMQ cluster.** Point every
  member at the same node. A single node, or Mosquitto, has no such limit.

Retained messages of an old run stay on the broker; the fresh run name is
what keeps them apart.

!!! note "Check the store against your broker once"
    Run the store contract against the broker before relying on it:
    `ROBOT_FLOW_TEST_MQTT=mqtt://flow:<password>@<host>:1883 python utest/run.py flow`
    runs the same tests every shipped store passes (see below). It takes a
    minute and tells you whether the broker keeps retained messages the way
    the store expects.

## A store of your own

Any other system -- a message bus the bench already has, a database, a
service of the product under test -- goes in a class of yours. Subclass
`robot.flow.signals.SignalStore`, implement three methods, name the class
in `ROBOT_FLOW_SIGNALS_BACKEND`, and the flows, the gates and the control
channel use it like any other store.

```python
# benchstore.py -- somewhere on PYTHONPATH of every machine of the run
import json
import threading
import time

from robot.flow.signals import SignalStore


class BusStore(SignalStore):
    """Signals on the bench's own bus. ``url`` is the ROBOT_FLOW_SIGNALS value."""

    supports_wait = True            # wait() blocks until something changes

    def __init__(self, url):
        self.where = url            # shown in the log: "Signal 'ready' = 1 (<where>)"
        self._bus = connect(url)    # whatever your system needs
        self._cache = {}            # name -> entry, kept current by _on_message
        self._changed = threading.Condition()
        self._bus.subscribe('#', self._on_message)
        self._bus.replay()          # a late joiner needs every current value

    # --- the three required methods

    def get(self, name):
        """The entry of ``name``, or None."""
        with self._changed:
            return self._cache.get(name)

    def set(self, name, value):
        """Store ``value`` (a JSON-safe scalar or dict) under ``name``.

        The entry must carry a sequence number that is higher than that of
        every earlier write to this store, by any process: a counter of the
        server, a log offset -- or, when the system has none, the writer's
        clock (then keep the machines on NTP)."""
        seq = self._bus.next_seq()                  # or time.time_ns()
        self._bus.publish(name, json.dumps(self.entry(value, seq)))

    def read(self):
        """Every entry, as a dict name -> entry."""
        with self._changed:
            return dict(self._cache)

    # --- optional

    def wait(self, seq, timeout):
        """Return when an entry newer than ``seq`` exists, or after ``timeout`` seconds."""
        with self._changed:
            self._changed.wait_for(lambda: self.last_seq() > seq, timeout)

    def close(self):
        self._bus.disconnect()

    def _on_message(self, name, payload):
        with self._changed:
            self._cache[name] = json.loads(payload)
            self._changed.notify_all()
```

- `SignalStore.entry(value, seq)` builds the entry dict
  (`{'value', 'time', 'seq', 'pid'}`); keep that shape, the control channel
  and `control status` read it.
- `last_seq()` is inherited (the newest `seq` in `read()`); override it when
  the system can answer faster.
- `supports_wait = True` makes the control poller call `wait()` instead of
  sleeping between polls, so a command reaches the flow at once. Without it
  the store is polled every half second, which is fine too.
- A class with only `get` and `set` -- the first version of this contract --
  still works for signals and gates, but the `set` and `snapshot` commands
  of the control channel need `read()`.

Select it on every machine of the run:

```bash
export ROBOT_FLOW_SIGNALS_BACKEND=benchstore:BusStore
export ROBOT_FLOW_SIGNALS=bus://hub:5000/run42        # passed to BusStore(url) as it is
```

or in the flow file / suite: `Library    robot.flow.signals.FlowSignals    store=bus://hub:5000/run42    backend=benchstore:BusStore`.

### Prove it with the contract

Every shipped store passes the same test suite, `StoreContract` in
`utest/flow/test_signals.py`: last value wins and a late joiner sees it,
entries carry the time and increasing sequence numbers, values keep their
type, eighty concurrent writes keep eighty distinct numbers, `wait` returns
on a change or after its timeout, and the control commands given through the
store reach a flow while those given before it started are ignored. Run it
against your class with a test file of a few lines:

```python
# test_busstore.py
import unittest
from test_signals import StoreContract          # utest/flow on sys.path
from benchstore import BusStore


class TestBusStoreContract(StoreContract, unittest.TestCase):

    def store(self):
        return BusStore('bus://hub:5000/contract-test')     # a new instance each time
```

```bash
python -m unittest -v test_busstore
```

A class that passes it behaves, for the flows, the gates and the operator's
commands, exactly like the file, the server, Redis and MQTT do.
