"""Switch incidents on and off, and set the degradation level.

    docker compose exec gateway python -m lab.faults list
    docker compose exec gateway python -m lab.faults on triage-v2
    docker compose exec gateway python -m lab.faults off triage-v2
    docker compose exec gateway python -m lab.faults off all
    docker compose exec gateway python -m lab.faults level 2

Lesson 2, an incident you are not told about (Exercise 1):

    docker compose exec gateway python -m lab.faults mystery
    docker compose exec gateway python -m lab.faults reveal

Every service reads these switches within a second; nothing restarts.
"""

import random
import sys
import time

from lab.common import FAULTS, REDIS

LEVELS = {
    1: "full service",
    2: "reply drafts from a fixed template, not the LLM",
    3: "triage by keyword rules, not the LLM",
    4: "no automated triage: every complaint goes to the dispatch desk",
}

# The mystery incident is one of these, drawn at random. Each is an incident
# the lecture describes; none is named on screen until `reveal`.
MYSTERIES = [
    ["llm-malformed"],                      # I2
    ["llm-slow", "trace-context-lost"],     # I3, as the lecture opened on it
    ["llm-overloaded"],
]


def show() -> None:
    # The switches are a Redis set; the level a Redis key (lab/common.py).
    active = REDIS.smembers("faults")
    for name, what in FAULTS.items():
        print(f"  [{'ON ' if name in active else 'off'}] {name:19} {what}")
    if REDIS.exists("mystery:faults"):
        # Its faults live in another key, so the list above cannot show them.
        print("\n  a mystery incident is set: find it from the telemetry")
    level = int(REDIS.get("degradation_level") or 1)
    print(f"\n  degradation level {level}: {LEVELS[level]}")


def mystery() -> None:
    """Set one incident at random, starting 1 to 4 minutes from now."""
    REDIS.delete("mystery:faults")
    REDIS.sadd("mystery:faults", *random.choice(MYSTERIES))
    REDIS.set("mystery:at", time.time() + random.uniform(60, 240))
    print("  a mystery incident will start within the next four minutes.")
    print("  Nothing on screen will say which, or when: that is the exercise.\n")


def reveal() -> None:
    """For the debrief: what the mystery incident was, and when it started."""
    names = sorted(REDIS.smembers("mystery:faults"))
    if not names:
        return print("  no mystery incident is set")
    at = float(REDIS.get("mystery:at"))
    started = time.strftime("%H:%M:%S UTC", time.gmtime(at))
    state = "started" if time.time() >= at else "starts"
    print(f"  {' + '.join(names)}, {state} at {started}")
    for name in names:
        print(f"    {name}: {FAULTS[name]}")


def main(args: list[str]) -> None:
    if not args or args[0] == "list":
        return show()
    command = args[0]   # list, on, off, level, mystery or reveal
    if command == "level" and len(args) == 2 and int(args[1]) in LEVELS:
        REDIS.set("degradation_level", int(args[1]))
    elif command == "off" and args[1:] == ["all"]:
        REDIS.delete("faults", "mystery:faults", "mystery:at")
    elif command in ("on", "off") and len(args) == 2 and args[1] in FAULTS:
        (REDIS.sadd if command == "on" else REDIS.srem)("faults", args[1])
    elif command == "mystery" and len(args) == 1:
        mystery()
    elif command == "reveal" and len(args) == 1:
        return reveal()
    else:
        # Anything else: say how to call it, and stop.
        sys.exit("usage: python -m lab.faults [list | on <fault> | off <fault> | off all"
                 " | level 1-4 | mystery | reveal]")
    show()


if __name__ == "__main__":
    main(sys.argv[1:])
