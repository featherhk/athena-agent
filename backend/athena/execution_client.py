"""Private single-send IPC. This file also runs as the protected shell shim."""
import asyncio
import base64
import json
import os
import socket
import sys
import uuid

FRAME_LIMIT = 2 * 1024 * 1024


def response_object(raw):
    if not raw or len(raw) > FRAME_LIMIT or not raw.endswith(b"\n"):
        raise ValueError("Execution outcome is unknown; do not automatically repeat it.")
    try:
        frame = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid execution response; do not automatically repeat the operation.") from exc
    if not isinstance(frame, dict):
        raise ValueError("Invalid execution response; do not automatically repeat the operation.")
    return frame


async def control(path, payload, timeout=45):
    async with asyncio.timeout(timeout):
        reader, writer = await asyncio.open_unix_connection(path, limit=FRAME_LIMIT)
        try:
            writer.write(json.dumps(payload).encode() + b"\n")
            await writer.drain()
            reply = response_object(await reader.readline())
            if reply.get("ok") is not True:
                raise ValueError(str(reply.get("error", "Execution service unavailable."))[:1000])
            return reply.get("data")
        finally:
            writer.close()
            await writer.wait_closed()


def main():
    path, owner, timeout, mode, command = sys.argv[1:]
    incoming = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(incoming) > 1024 * 1024:
        raise ValueError("Extension input exceeds 1 MiB.")
    request = {"op": "exec", "id": uuid.uuid4().hex, "owner": owner,
               "timeout": int(timeout), "mode": mode, "cwd": os.getcwd(),
               "command": command, "stdin": base64.b64encode(incoming).decode()}
    # No reconnect: a lost reply never causes a repeated side effect. Closing
    # this connection on stop tells the broker to remove the execution container.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(int(timeout) + 45)
        connection.connect(path)
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as stream:
            total = 0
            while line := stream.readline(FRAME_LIMIT + 1):
                frame = response_object(line)
                if frame.get("type") == "output":
                    if not isinstance(frame.get("data"), str):
                        raise ValueError("Invalid execution output.")
                    data = base64.b64decode(frame["data"], validate=True)
                    total += len(data)
                    if total > 33 * 1024 * 1024:
                        raise ValueError("Execution response exceeded its output limit.")
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                elif frame.get("type") == "exit":
                    if type(frame.get("code")) is not int or not -255 <= frame["code"] <= 255:
                        raise ValueError("Invalid execution exit status.")
                    if mode == "policy":
                        # This stream belongs to the trusted shim. Worker stderr
                        # is forwarded as output and cannot forge this receipt.
                        print(json.dumps({"execution_status": frame.get("status")}), file=sys.stderr)
                    elif frame.get("error"):
                        print("\n" + str(frame["error"])[:1000], file=sys.stderr)
                    return frame["code"]
                else:
                    raise ValueError(str(frame.get("error", "Invalid execution response."))[:1000])
    raise ValueError("Execution outcome is unknown; do not automatically repeat it.")


if __name__ == "__main__":
    try:
        code = main()
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        code = 125
    sys.exit(code)
