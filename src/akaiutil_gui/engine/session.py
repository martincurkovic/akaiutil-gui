"""A running `akaiutil` process that we feed commands to, one at a time.

akaiutil is an interactive REPL. When stdin is a pipe it still prints a
prompt of the form "<cwd> > " (and flushes it) before reading each command,
so "output ends with a line like '/disk0/A > '" is our end-of-response
marker, and the path in that prompt is the authoritative current directory.

stderr is merged into stdout so error messages arrive in order with
everything else.

The process is always started read-only (-r) in this proof of concept.
"""

import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# "<start of line>/disk0/A/MY VOL > " at the very end of the buffer
_PROMPT_RE = re.compile(rb"(?:\A|\n)(/[^\n]*?) > \Z")

TrafficCallback = Callable[[str, str], None]  # (direction "in"|"out", text)


class EngineError(Exception):
    pass


@dataclass
class Response:
    text: str  # everything printed in answer to the command, prompt removed
    cwd: str  # directory shown in the trailing prompt


class Session:
    def __init__(
        self,
        binary: str | os.PathLike,
        images: list[str | os.PathLike],
        on_traffic: TrafficCallback | None = None,
    ):
        self._binary = str(binary)
        self._images = [str(Path(p).resolve()) for p in images]
        self._on_traffic = on_traffic
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._chunks: queue.Queue[bytes | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        # akaiutil writes exports (get, sample2wav, ...) into its *own* working
        # directory. Its command parser splits on whitespace and `lcd` takes a
        # single token, so a destination like "/Users/me/My Samples" can't be
        # expressed. Run it inside a space-free scratch dir instead and move
        # the results where the user wants them.
        self.staging_dir = Path(tempfile.mkdtemp(prefix="akaiutil-gui-"))
        self.banner = ""
        self.cwd = "/"

    # ------------------------------------------------------------ lifecycle
    def start(self) -> str:
        args = [self._binary, "-r", *self._images]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash on Windows
        try:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                cwd=self.staging_dir,
                creationflags=flags,
            )
        except OSError as e:
            self.close()
            raise EngineError(f"Couldn't start akaiutil ({self._binary}): {e}") from e
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.banner = self._read_until_prompt(timeout=60).text
        return self.banner

    def close(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is not None:
                try:
                    proc.stdin.write(b"exit\n")
                    proc.stdin.close()
                    proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    proc.kill()
                    proc.wait()
            shutil.rmtree(self.staging_dir, ignore_errors=True)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------- commands
    def run(self, command: str, timeout: float = 120) -> Response:
        if "\n" in command or len(command) > 250:
            # akaiutil reads at most 256 bytes per command line
            raise EngineError(f"Invalid command: {command[:60]!r}")
        with self._lock:
            if not self.alive:
                raise EngineError("akaiutil is not running")
            self._emit("in", command + "\n")
            try:
                self._proc.stdin.write(command.encode("utf-8") + b"\n")
                self._proc.stdin.flush()
            except OSError as e:
                raise EngineError(f"Lost connection to akaiutil: {e}") from e
            return self._read_until_prompt(timeout)

    # ------------------------------------------------------------- plumbing
    def _read_loop(self) -> None:
        stdout = self._proc.stdout
        while True:
            data = stdout.read(65536)  # unbuffered: returns what's available
            if not data:
                self._chunks.put(None)  # EOF
                return
            self._chunks.put(data)

    def _read_until_prompt(self, timeout: float) -> Response:
        buf = b""
        while True:
            try:
                chunk = self._chunks.get(timeout=timeout)
            except queue.Empty:
                raise EngineError(f"akaiutil didn't answer within {timeout:.0f}s") from None
            if chunk is None:
                text = buf.decode("utf-8", errors="replace")
                raise EngineError(f"akaiutil exited unexpectedly.\n{text.strip()}")
            buf += chunk
            if buf.endswith(b" > "):  # cheap pre-check before the regex
                m = _PROMPT_RE.search(buf)
                if m:
                    break
        text = buf[: m.start(1)].decode("utf-8", errors="replace")
        self.cwd = m.group(1).decode("utf-8", errors="replace")
        self._emit("out", text + self.cwd + " > ")
        return Response(text=text, cwd=self.cwd)

    def _emit(self, direction: str, text: str) -> None:
        if self._on_traffic is not None:
            self._on_traffic(direction, text)
