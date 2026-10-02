"""Import the actual dependency stack before checking for a silently enabled GIL."""
import json
import sqlite3
import sys
import sysconfig
import fastapi  # noqa: F401
import pydantic_core  # noqa: F401
import httpx  # noqa: F401
import uvicorn  # noqa: F401
import jsonschema  # noqa: F401
from .config import require_free_threading


def main():
    require_free_threading()
    print(json.dumps({"python": sys.version.split()[0], "free_threaded_build": bool(sysconfig.get_config_var("Py_GIL_DISABLED")), "gil_enabled": sys._is_gil_enabled(), "sqlite_threadsafety": sqlite3.threadsafety}))


if __name__ == "__main__":
    main()
