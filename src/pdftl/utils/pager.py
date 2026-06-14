import sys
import subprocess
import os
import platform
import logging

logger = logging.getLogger(__name__)


class ThresholdPagerStream:
    """
    A file-like proxy that spools output to memory until a line threshold is hit.
    Once the threshold is exceeded, it seamlessly starts a system pager and
    streams all future output directly to it.
    """

    def __init__(self, threshold):
        self.threshold = threshold
        self.buffer = []
        self.lines = 0
        self.pager_proc = None
        self.pager_failed = False

    def isatty(self):
        # We must trick Rich into thinking this stream is a terminal
        # so it continues generating ANSI color codes.
        return True

    def write(self, text: str):
        if self.pager_failed:
            try:
                sys.stdout.write(text)
            except OSError as e:
                raise BrokenPipeError() from e
            return

        if self.pager_proc:
            # We are already paging, stream directly to less
            try:
                self.pager_stdin.write(text)
            except OSError as e:
                # The user hit 'q' and closed the pager.
                # Raise to instantly stop Rich from rendering the rest of the document.
                raise BrokenPipeError() from e
            return

        self.buffer.append(text)
        self.lines += text.count("\n")

        if self.lines >= self.threshold:
            self._start_pager()

    def _start_pager(self):
        current_os = platform.system()

        # Piping to interactive subprocesses on Windows is notoriously flaky.
        # For safety in the streaming model, we fall back to raw output on Windows.
        if current_os not in ("Linux", "Darwin"):
            self._fallback()
            return

        env = os.environ.copy()
        user_less = env.get("LESS", "FRX")
        if "R" not in user_less and "r" not in user_less:
            user_less = f"{user_less} -R".strip()
        env["LESS"] = user_less
        pager_cmd = env.get("PAGER", "less")
        try:
            self.pager_proc = subprocess.Popen(
                [pager_cmd], stdin=subprocess.PIPE, env=env, text=True
            )
            # Flush the spooled history into the pager
            self.pager_stdin.write("".join(self.buffer))
            self.buffer.clear()
        except OSError as e:
            logger.warning("Failed to start streaming pager: %s", e)
            self._fallback()

    @property
    def pager_stdin(self):
        if self.pager_proc is None:
            pager_stdin = None
        else:
            pager_stdin = getattr(self.pager_proc, "stdin", None)
        if pager_stdin is None:
            raise OSError("Pager stdin is unavailable")
        return pager_stdin

    def _fallback(self):
        self.pager_failed = True
        sys.stdout.write("".join(self.buffer))
        self.buffer.clear()

    def flush(self):
        if self.pager_failed:
            sys.stdout.flush()
        elif self.pager_proc:
            try:
                self.pager_stdin.flush()
            except OSError:
                # Pipe already closed (user quit pager); nothing to flush.
                pass

    def close(self):
        if self.pager_proc:
            try:
                self.pager_stdin.close()
            except OSError:
                # Pipe already closed; proceed to wait() regardless.
                pass
            self.pager_proc.wait()  # Wait for the user to close less
        elif not self.pager_failed:
            # Reached EOF without hitting the threshold. Print the buffer.
            sys.stdout.write("".join(self.buffer))
            sys.stdout.flush()
