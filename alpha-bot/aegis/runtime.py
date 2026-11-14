"""One local process per database, with no stale-PID killing or credentials."""

import json
import os
import sys


class InstanceLock:
    def __init__(self, database):
        self.path = database.resolve().with_suffix(".lock")
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b")
        if os.fstat(descriptor).st_size == 0:
            handle.write(b" ")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise RuntimeError(
                "Another Alpha bot process owns this database. Stop it first."
            ) from error
        self.handle = handle
        handle.seek(0)
        handle.write(json.dumps({"pid": os.getpid()}).encode())
        handle.truncate()
        handle.flush()
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            self.handle.close()  # OS releases the advisory lock even after a crash.
            self.handle = None
