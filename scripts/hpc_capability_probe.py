"""One-shot Saga deployment experiment. Accept a single protocol query on stdin."""

import json
import sys


def main():
    message = sys.stdin.buffer.read(257)
    try:
        request = json.loads(message) if len(message) <= 256 else None
    except (UnicodeError, ValueError, RecursionError):
        request = None
    if request != {"operation": "capabilities"}:
        print('{"ok":false,"error":"Unsupported request"}')
        return 1
    print('{"ok":true,"protocol":"chembook3d-probe-v1","operations":["capabilities"]}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
