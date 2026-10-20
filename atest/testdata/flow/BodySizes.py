"""Listener (v3) that reports how many loop iterations a finished test still
holds in memory: one line per loop on the console."""

ROBOT_LISTENER_API_VERSION = 3


def end_test(data, result):
    for item in result.body:
        if item.type in ('WHILE', 'FOR'):
            kept = sum(1 for i in item.body if i.type == 'ITERATION')
            print(f"BODY SIZE: {item.type} in '{result.name}' keeps {kept} iteration(s)")
