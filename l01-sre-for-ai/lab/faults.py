"""Switch incidents on and off, and set the degradation level.

    docker compose exec gateway python -m lab.faults list
    docker compose exec gateway python -m lab.faults on triage-v2
    docker compose exec gateway python -m lab.faults off triage-v2
    docker compose exec gateway python -m lab.faults off all
    docker compose exec gateway python -m lab.faults level 2

Every service reads these switches within a second; nothing restarts.
"""

import sys

from lab.common import FAULTS, REDIS

LEVELS = {
    1: "full service",
    2: "reply drafts from a fixed template, not the LLM",
    3: "triage by keyword rules, not the LLM",
    4: "no automated triage: every complaint goes to the dispatch desk",
}


def show() -> None:
    # The switches are a Redis set; the level a Redis key (lab/common.py).
    active = REDIS.smembers("faults")
    for name, what in FAULTS.items():
        print(f"  [{'ON ' if name in active else 'off'}] {name:15} {what}")
    level = int(REDIS.get("degradation_level") or 1)
    print(f"\n  degradation level {level}: {LEVELS[level]}")


def main(args: list[str]) -> None:
    if not args or args[0] == "list":
        return show()
    command = args[0]   # list, on, off or level
    if command == "level" and len(args) == 2 and int(args[1]) in LEVELS:
        REDIS.set("degradation_level", int(args[1]))
    elif command == "off" and args[1:] == ["all"]:
        REDIS.delete("faults")
    elif command in ("on", "off") and len(args) == 2 and args[1] in FAULTS:
        (REDIS.sadd if command == "on" else REDIS.srem)("faults", args[1])
    else:
        # Anything else: say how to call it, and stop.
        sys.exit("usage: python -m lab.faults [list | on <fault> | off <fault> | off all | level 1-4]")
    show()


if __name__ == "__main__":
    main(sys.argv[1:])
