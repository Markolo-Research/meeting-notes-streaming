"""PulseAudio/PipeWire device discovery for recording."""

from collections.abc import Callable
from dataclasses import dataclass
import subprocess

from .logger import get_logger

logger = get_logger(__name__)

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class AudioSink:
    name: str
    sink_id: str


def _run_pactl(args: list[str], run: RunCommand) -> subprocess.CompletedProcess[str]:
    return run(["pactl", *args], capture_output=True, text=True, timeout=2)


def _description_for_device(
    device_name: str,
    list_args: list[str],
    run: RunCommand,
) -> str | None:
    try:
        result = _run_pactl(list_args, run)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not list audio device descriptions", exc_info=exc)
        return None

    if result.returncode != 0:
        return None

    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        if device_name not in line:
            continue
        for detail in lines[index : index + 20]:
            if detail.strip().startswith("Description:"):
                return detail.split("Description:", 1)[1].strip()
        return None
    return None


def default_source_description(run: RunCommand = subprocess.run) -> str:
    try:
        result = _run_pactl(["get-default-source"], run)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not query default audio source", exc_info=exc)
        return "System default"

    if result.returncode != 0:
        logger.warning("pactl get-default-source failed: %s", result.stderr.strip())
        return "System default"

    source_name = result.stdout.strip()
    description = _description_for_device(source_name, ["list", "sources"], run)
    return description or source_name or "System default"


def default_sink(run: RunCommand = subprocess.run) -> AudioSink | None:
    try:
        result = _run_pactl(["get-default-sink"], run)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not query default audio sink", exc_info=exc)
        return None

    if result.returncode != 0:
        logger.warning("pactl get-default-sink failed: %s", result.stderr.strip())
        return None

    sink_name = result.stdout.strip()
    if not sink_name:
        logger.warning("pactl returned an empty default sink name")
        return None

    try:
        result = _run_pactl(["list", "sinks", "short"], run)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not list audio sinks", exc_info=exc)
        return None

    if result.returncode != 0:
        logger.warning("pactl list sinks short failed: %s", result.stderr.strip())
        return None

    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[1] == sink_name:
            return AudioSink(name=sink_name, sink_id=fields[0])

    logger.warning("Default sink %s was not found in pactl sink list", sink_name)
    return None


def default_sink_description(run: RunCommand = subprocess.run) -> str:
    sink = default_sink(run)
    if sink is None:
        return "System default (monitor)"
    description = _description_for_device(sink.name, ["list", "sinks"], run)
    return f"{description or sink.name} (monitor)"


def audio_device_info(mode: str, run: RunCommand = subprocess.run) -> dict[str, str]:
    info = {"mode": mode}
    if mode in {"mic", "combined"}:
        info["mic_device"] = default_source_description(run)
    if mode in {"system", "combined"}:
        info["system_device"] = default_sink_description(run)
    return info
