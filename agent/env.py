from __future__ import annotations

import subprocess
import time
from pathlib import Path


class GameEnvironment:
    def __init__(
        self,
        adb_path: str = r"C:\LDPlayer\LDPlayer9\adb.exe",
        device: str = "emulator-5554",
        timeout: float = 15.0,
    ) -> None:
        self.adb_path = adb_path
        self.device = device
        self.timeout = timeout

    def capture_screenshot(self, output_path: str | Path = "screenshot.png") -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        result = self._run_adb(
            ["exec-out", "screencap", "-p"],
            capture_output=True,
            text=False,
        )

        if not result.stdout:
            raise RuntimeError("ADB screenshot command returned no image data.")

        output.write_bytes(result.stdout)
        return output

    def tap(self, x: int, y: int) -> None:
        self._run_adb(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._run_adb(
            [
                "shell",
                "input",
                "swipe",
                str(x1),
                str(y1),
                str(x2),
                str(y2),
                str(duration_ms),
            ]
        )

    def back(self) -> None:
        self._run_adb(["shell", "input", "keyevent", "BACK"])

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def _run_adb(
        self,
        args: list[str],
        *,
        capture_output: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        command = [self.adb_path, "-s", self.device, *args]

        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=capture_output,
                text=text,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"ADB executable not found: {self.adb_path}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
            detail = stderr or stdout or str(exc)
            raise RuntimeError(f"ADB command failed: {' '.join(command)}\n{detail}") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"ADB command timed out after {self.timeout}s: {' '.join(command)}") from exc
