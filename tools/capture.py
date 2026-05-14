from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ADBConfig:
    adb_path: str = r"C:\LDPlayer\LDPlayer9\adb.exe"
    device_id: str = "emulator-5554"
    timeout: float = 15.0


def capture_screen(config: ADBConfig, output_path: str | Path = "runtime/screenshots/current.png") -> Path:
    """Capture the current LDPlayer screen to a local PNG path using adb screencap."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    result = run_adb(config, ["exec-out", "screencap", "-p"], text=False)
    if not result.stdout:
        raise RuntimeError("ADB screencap returned no image data.")

    output.write_bytes(result.stdout)
    return output


def run_adb(config: ADBConfig, args: list[str], *, text: bool = True) -> subprocess.CompletedProcess:
    command = [config.adb_path, "-s", config.device_id, *args]
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=text,
            timeout=config.timeout,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"ADB executable not found: {config.adb_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"ADB command timed out after {config.timeout}s: {' '.join(command)}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        detail = stderr or stdout or str(exc)
        raise RuntimeError(f"ADB command failed: {' '.join(command)}\n{detail}") from exc
