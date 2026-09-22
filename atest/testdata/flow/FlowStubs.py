"""Stub bench keywords for the flow runner acceptance tests.

They stand in for application libraries (signals, power, cycle tests) so the
flows run offline and deterministically.
"""

from robot.api import logger


# Each signal is a list of values returned on successive reads; the last
# value sticks. 'bench.chamber.state' needs three reads to become ready.
SIGNALS = {
    'bench.chamber.state': [0, 0, 1],
    'bench.pair.mode': [3],
    'bench.never': [0],
}
COUNTS = {'cycle': 0, 'recovery': 0, 'iteration': 0}
OPERATORS = {
    '==': lambda a, b: a == b,
    '!=': lambda a, b: a != b,
    '<': lambda a, b: a < b,
    '<=': lambda a, b: a <= b,
    '>': lambda a, b: a > b,
    '>=': lambda a, b: a >= b,
}


def signal_should_be(name, op, value):
    values = SIGNALS.setdefault(name, [0])
    current = values.pop(0) if len(values) > 1 else values[0]
    if op not in OPERATORS:
        raise ValueError(f"Unknown operator '{op}'.")
    if not OPERATORS[op](float(current), float(value)):
        raise AssertionError(f'{name} is {current}, expected {op} {value}.')
    logger.info(f'{name} is {current}.')


def set_signal(name, value):
    SIGNALS[name] = [value]


def set_power_and_current(blade):
    logger.info(f'Power on for {blade}.')


def run_cycle_tests(blade):
    COUNTS['cycle'] += 1
    logger.info(f"Cycle {COUNTS['cycle']} for {blade}.")
    if COUNTS['cycle'] == 2:
        raise AssertionError('Cycle test 2 failed on purpose.')


def execute_recovery_strategy(blade):
    COUNTS['recovery'] += 1
    logger.info(f"Recovery {COUNTS['recovery']} for {blade}.")


def before_suite_tear_down():
    logger.info('Bench released.')


def break_something(message='Broken on purpose.'):
    raise AssertionError(message)


def count_iteration():
    COUNTS['iteration'] += 1
    logger.info(f"Iteration {COUNTS['iteration']}.")


def log_branch(name):
    logger.info(f'Took the {name} branch.')
