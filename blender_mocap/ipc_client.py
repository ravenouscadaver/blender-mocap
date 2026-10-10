# blender_mocap/ipc_client.py
"""TCP client for the Blender addon. Reads pose data, sends commands."""
import json
import select
import socket


class IPCClient:
    """Connects to the local capture server over loopback TCP."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._buffer = ""

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.connect((self._host, self._port))
        self._sock.setblocking(False)

    def read_message(self, timeout: float = 5.0) -> dict | None:
        """Read a single JSON message. Blocks up to timeout."""
        if not self._sock:
            return None
        deadline = timeout
        while deadline > 0:
            if "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    return json.loads(line)
            ready, _, _ = select.select([self._sock], [], [], min(deadline, 0.5))
            if ready:
                try:
                    data = self._sock.recv(8192)
                except OSError:
                    return None
                if not data:
                    return None
                self._buffer += data.decode("utf-8")
            deadline -= 0.5
        return None

    def drain_latest_pose(self) -> tuple[dict | None, list[dict]]:
        """Read all available messages. Returns (latest_pose, other_messages)."""
        if not self._sock:
            return None, []

        while True:
            ready, _, _ = select.select([self._sock], [], [], 0)
            if not ready:
                break
            try:
                data = self._sock.recv(65536)
            except OSError:
                break
            if not data:
                break
            self._buffer += data.decode("utf-8")

        latest_pose = None
        other_messages = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                msg = json.loads(line)
                if msg.get("type") == "pose":
                    latest_pose = msg
                else:
                    other_messages.append(msg)

        return latest_pose, other_messages

    def send_command(self, action: str) -> None:
        if self._sock:
            msg = json.dumps({"type": "command", "action": action}) + "\n"
            self._sock.sendall(msg.encode("utf-8"))

    def is_connected(self) -> bool:
        return self._sock is not None

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
