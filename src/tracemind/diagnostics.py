"""Failure locations without exception messages, locals, source lines or secrets."""

import traceback

from .benchmark.builder import write


def record_failure(path, error):
    record = {
        "error_type": type(error).__name__,
        "frames": [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(error.__traceback__)
        ],
        "message_omitted": "Exception messages, locals and code lines may contain credentials",
    }
    write(path, record)
    return record
