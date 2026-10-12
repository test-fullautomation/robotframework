"""Stand-in bench for the flow demos.

Everything a real bench would provide - a climate chamber, power, cycle
tests, an ECU to flash - is simulated here so the flows run offline. The
bench keeps its state in a small JSON file (FLOW_DEMO_BENCH, default in the
temporary directory) so it survives across the steps of a flow.

This is the *bench*, not the coordination between flows: flows that talk to
each other use the runner's own library, ``robot.flow.signals`` (see
04_rendezvous.flow.json). Nothing here is a keyword named like one of its
keywords, so a flow can import both.
"""

import json
import os
import tempfile
import time

from robot.api import logger


STATE = os.environ.get('FLOW_DEMO_BENCH',
                       os.path.join(tempfile.gettempdir(), 'flow_demo_bench.json'))


# --- bench state (private) ----------------------------------------------------

def _read():
    for _ in range(20):
        try:
            with open(STATE, encoding='UTF-8') as file:
                return json.load(file)
        except FileNotFoundError:
            return {}
        except (ValueError, PermissionError):   # being replaced right now
            time.sleep(0.02)
    return {}


def _update(change):
    """Read-modify-write under a lock file: two rigs share one bench."""
    lock = f'{STATE}.lock'
    for _ in range(200):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            time.sleep(0.01)
    else:
        raise RuntimeError(f'Bench state lock {lock} is stuck; delete it.')
    try:
        state = _read()
        change(state)
        temp = f'{STATE}.{os.getpid()}.tmp'
        with open(temp, 'w', encoding='UTF-8') as file:
            json.dump(state, file)
        os.replace(temp, STATE)
    finally:
        os.remove(lock)


def _get(name, default=None):
    return _read().get(name, default)


def _set(name, value):
    _update(lambda state: state.__setitem__(name, value))


def reset_bench():
    """Puts the simulated bench back to its initial state."""
    _update(lambda state: state.clear())
    logger.info('Bench reset.')


# --- climate chamber ----------------------------------------------------------

def start_chamber(ready_after='2s'):
    """Asks the chamber for its set point; it reports ready after ``ready_after``."""
    seconds = float(str(ready_after).rstrip('s'))
    _set('chamber.ready_at', time.time() + seconds)
    logger.info(f'Chamber set point requested, ready in {seconds:.1f} s.')


def chamber_should_be_ready():
    """Passes once the chamber has reached its set point."""
    remaining = _get('chamber.ready_at', 0) - time.time()
    if remaining > 0:
        raise AssertionError(f'Chamber still settling, {remaining:.1f} s to go.')
    logger.info('Chamber at set point.')


# --- power and cycle tests ------------------------------------------------------

def power_on_dut(rig, voltage='12V', current='5A'):
    logger.info(f'{rig}: supply on, {voltage} / {current}.')


def run_cycle_tests(rig, fail_on=3):
    """Runs one cycle and returns its number; the cycle numbered ``fail_on``
    fails on purpose."""
    counted = {}

    def increment(state):
        counted['n'] = state.get(f'cycle.{rig}', 0) + 1
        state[f'cycle.{rig}'] = counted['n']

    _update(increment)
    count = counted['n']
    logger.info(f'{rig}: cycle {count} tests running.')
    if count == int(fail_on):
        raise AssertionError(f'{rig}: cycle {count} failed - CAN bus timeout.')
    logger.info(f'{rig}: cycle {count} passed.')
    return count


def recover_dut(rig):
    logger.warn(f'{rig}: recovery - resetting CAN interface and re-arming.')


def release_bench():
    logger.info('Bench released: supply off, chamber to ambient.')


# --- ECU flashing -----------------------------------------------------------------

def power_on_ecu():
    _set('ecu.alive', 1)
    logger.info('ECU powered on.')


def ecu_should_be_alive():
    if _get('ecu.alive') != 1:
        raise AssertionError('ECU does not answer on UDS.')
    logger.info('ECU answers on UDS.')


def read_ecu_version():
    """Returns the version the ECU reports over UDS."""
    version = _get('ecu.version', 'fw-1.0')
    logger.info(f'ECU reports {version}.')
    return version


def flash_image(image):
    """Flashes ``image``; anything containing 'bad' fails the checksum."""
    logger.info(f'Flashing {image} ...')
    time.sleep(0.3)
    if 'bad' in image:
        raise AssertionError(f'Flash failed: checksum mismatch in {image}.')
    _set('ecu.version', image)


def verify_via_uds(expected):
    version = read_ecu_version()
    if version != expected:
        raise AssertionError(f'ECU reports {version}, expected {expected}.')


def restore_golden_image():
    logger.warn('Restoring golden image fw-1.0.')
    _set('ecu.version', 'fw-1.0')
