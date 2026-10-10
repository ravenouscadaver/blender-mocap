# blender_mocap/capture_server/ipc_server.py
"""Loopback TCP server for the capture process."""
import json
import queue
import select
import socket
import threading


PROTOCOL_VERSION = 1


class IPCServer:
    """Newline-delimited JSON server over loopback TCP."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._running = False
        self._accept_thread: threading.Thread | None = None
        self._command_queue: queue.Queue = queue.Queue()
        self._recv_buffer = ""

    def start(self) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self._host, self._port))
        self._server_sock.listen(1)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        if self._server_sock is None:
            return
        self._server_sock.settimeout(1.0)
        while self._running:
            try:
                client, _ = self._server_sock.accept()
                self._client_sock = client
                self._client_sock.setblocking(False)
                self._raw_send({"type": "hello", "protocol_version": PROTOCOL_VERSION})
                self._read_commands_loop()
                try:
                    client.close()
                except OSError:
                    pass
                self._client_sock = None
            except socket.timeout:
                continue
            except OSError:
                break

    def _read_commands_loop(self) -> None:
        while self._running and self._client_sock:
            sock = self._client_sock
            if sock is None:
                break
            try:
                ready, _, _ = select.select([sock], [], [], 0.5)
                if ready:
                    data = sock.recv(4096)
                    if not data:
                        break
                    self._recv_buffer += data.decode("utf-8")
                    while "\n" in self._recv_buffer:
                        line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                        if line.strip():
                            msg = json.loads(line)
                            self._command_queue.put(msg)
            except (OSError, ConnectionError, AttributeError):
                break

    def _raw_send(self, msg: dict) -> None:
        if self._client_sock:
            data = json.dumps(msg) + "\n"
            self._client_sock.sendall(data.encode("utf-8"))

    def send(self, msg: dict) -> None:
        self._raw_send(msg)

    def send_heartbeat(self) -> None:
        self.send({"type": "heartbeat"})

    def read_command(self, timeout: float = 0.0) -> dict | None:
        try:
            return self._command_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def has_client(self) -> bool:
        return self._client_sock is not None

    def stop(self) -> None:
        self._running = False
        if self._client_sock:
            try:
                self._client_sock.close()
            except OSError:
                pass
            self._client_sock = None
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        if self._accept_thread:
            self._accept_thread.join(timeout=3.0)
