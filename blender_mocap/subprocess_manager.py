# blender_mocap/subprocess_manager.py
"""Manages the capture server subprocess and its private Python environment."""
import os
import shutil
import socket
import subprocess
import sys


ADDON_VERSION = "0.41.0-win-alpha"
BASE_DIR = os.environ.get("BLENDER_MOCAP_HOME", r"E:\\blender-mocap" if os.name == "nt" else os.path.expanduser("~/.blender-mocap"))
VENV_DIR = os.path.join(BASE_DIR, "venv")
RECORDINGS_DIR = os.path.join(BASE_DIR, "recordings")
IPC_HOST = "127.0.0.1"


def get_venv_path() -> str:
    return VENV_DIR


def get_recordings_path() -> str:
    return RECORDINGS_DIR


def _venv_executable(venv_path: str, name: str) -> str:
    if os.name == "nt":
        suffix = ".exe" if not name.endswith(".exe") else ""
        return os.path.join(venv_path, "Scripts", name + suffix)
    return os.path.join(venv_path, "bin", name)


def _candidate_python_commands() -> list[list[str]]:
    candidates: list[list[str]] = []

    # Prefer Blender's bundled Python when Blender exposes its Python prefix.
    # This keeps first-run setup self-contained on machines with no system
    # Python installation.
    bundled_names = ["python.exe"] if os.name == "nt" else ["python3", "python"]
    for name in bundled_names:
        bundled = os.path.join(sys.prefix, "bin", name)
        if os.path.isfile(bundled):
            candidates.append([bundled])

    # Blender may itself be launched through a normal Python executable.
    if sys.executable and os.path.basename(sys.executable).lower().startswith("python"):
        candidates.append([sys.executable])

    # Fall back to system CPython.
    if os.name == "nt":
        for version in ("3.12", "3.11", "3.10"):
            candidates.append(["py", f"-{version}"])
        candidates.extend([["python"], ["python3"]])
    else:
        candidates.extend([["python3"], ["python"]])

    # Preserve order while removing duplicate commands.
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in candidates:
        key = tuple(command)
        if key not in seen:
            seen.add(key)
            unique.append(command)
    return unique


def find_compatible_python() -> list[str] | None:
    """Return a Python command for CPython 3.10-3.12, or None."""
    for command in _candidate_python_commands():
        try:
            result = subprocess.run(
                command + ["--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = (result.stdout or result.stderr).strip()
            version_str = output.split()[-1]
            major, minor = (int(x) for x in version_str.split(".")[:2])
            if major == 3 and 10 <= minor <= 12:
                return command
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            continue
    return None


def check_python_version() -> bool:
    return find_compatible_python() is not None


def needs_venv_update(venv_path: str) -> bool:
    marker_path = os.path.join(venv_path, ".addon-version")
    if not os.path.exists(marker_path):
        return True
    try:
        with open(marker_path, encoding="utf-8") as f:
            return f.read().strip() != ADDON_VERSION
    except OSError:
        return True


def create_venv(venv_path: str) -> None:
    """Create a venv and install capture server requirements."""
    python_cmd = find_compatible_python()
    if python_cmd is None:
        raise RuntimeError(
            "No compatible Python 3.10-3.12 found. "
            "Install 64-bit CPython from python.org, then restart Blender."
        )

    if os.path.exists(venv_path):
        shutil.rmtree(venv_path)

    os.makedirs(BASE_DIR, exist_ok=True)

    subprocess.run(
        python_cmd + ["-m", "venv", venv_path],
        check=True,
        timeout=60,
    )

    req_path = os.path.join(
        os.path.dirname(__file__), "capture_server", "requirements.txt"
    )
    python_path = _venv_executable(venv_path, "python")
    subprocess.run(
        [python_path, "-m", "pip", "install", "--upgrade", "pip"],
        check=True,
        timeout=180,
    )
    subprocess.run(
        [python_path, "-m", "pip", "install", "-r", req_path],
        check=True,
        timeout=600,
    )

    marker_path = os.path.join(venv_path, ".addon-version")
    with open(marker_path, "w", encoding="utf-8") as f:
        f.write(ADDON_VERSION)


def ensure_venv() -> str:
    venv_path = get_venv_path()
    if needs_venv_update(venv_path):
        create_venv(venv_path)
    return _venv_executable(venv_path, "python")


def _reserve_loopback_port() -> int:
    """Ask the OS for a currently free loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((IPC_HOST, 0))
        return int(sock.getsockname()[1])


class CaptureProcess:
    """Manages the capture server subprocess lifecycle."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._host = IPC_HOST
        self._port: int | None = None

    def start(
        self,
        camera_index: int,
        audio_device: int | None = None,
        smoothing: float = 0.3,
        pid: int | None = None,
    ) -> tuple[str, int]:
        """Launch capture server. Returns (host, port)."""
        python_path = ensure_venv()
        self._port = _reserve_loopback_port()

        addon_dir = os.path.dirname(os.path.dirname(__file__))
        env = os.environ.copy()
        env["PYTHONPATH"] = addon_dir + os.pathsep + env.get("PYTHONPATH", "")

        cmd = [
            python_path,
            "-m",
            "blender_mocap.capture_server",
            "--host",
            self._host,
            "--port",
            str(self._port),
            "--camera",
            str(camera_index),
            "--smoothing",
            str(smoothing),
        ]
        if audio_device is not None:
            cmd.extend(["--audio-device", str(audio_device)])

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        return self._host, self._port

    @property
    def endpoint(self) -> tuple[str, int] | None:
        if self._port is None:
            return None
        return self._host, self._port

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def get_stderr(self) -> str:
        """Return stderr once the child exits.

        Windows pipes cannot be polled with select(), so live stderr collection is
        intentionally avoided here. Server-side errors should travel over IPC.
        """
        if self._process is None or self._process.stderr is None:
            return ""
        if self._process.poll() is None:
            return ""
        try:
            return self._process.stderr.read().decode("utf-8", errors="replace")
        except (OSError, ValueError):
            return ""

    def stop(self, timeout: float = 3.0) -> None:
        if self._process is None:
            return

        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1.0)

        self._process = None
        self._port = None
