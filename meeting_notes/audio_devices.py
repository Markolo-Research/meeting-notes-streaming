from collections.abc import Callable
import subprocess

from .logger import get_logger

logger = get_logger(__name__)

RunCommand = Callable[..., subprocess.CompletedProcess[str]]
AudioSink = tuple[str, str]


def _run_pactl(
    args: list[str],
    run: RunCommand,
    failure_message: str,
) -> str:
    try:
        result = run(["pactl", *args], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(failure_message, exc_info=exc)
        return ""
    if result.returncode != 0:
        logger.warning("pactl %s failed: %s", " ".join(args), result.stderr.strip())
        return ""
    return result.stdout.strip()


def _description_for_device(device_name: str, list_args: list[str], run: RunCommand) -> str | None:
    if not device_name:
        return None
    lines = _run_pactl(list_args, run, "Could not list audio device descriptions").splitlines()
    for index, line in enumerate(lines):
        if device_name in line:
            return next(
                (
                    detail.split("Description:", 1)[1].strip()
                    for detail in lines[index : index + 20]
                    if detail.strip().startswith("Description:")
                ),
                None,
            )
    return None


def default_source_description(run: RunCommand = subprocess.run) -> str:
    source_name = _run_pactl(["get-default-source"], run, "Could not query default audio source")
    return _description_for_device(source_name, ["list", "sources"], run) or source_name or "System default"


def default_sink(run: RunCommand = subprocess.run) -> AudioSink | None:
    sink_name = _run_pactl(["get-default-sink"], run, "Could not query default audio sink")
    if not sink_name:
        logger.warning("pactl returned an empty default sink name")
        return None

    sink = next(
        (
            (sink_name, fields[0])
            for fields in (
                line.split()
                for line in _run_pactl(["list", "sinks", "short"], run, "Could not list audio sinks").splitlines()
            )
            if len(fields) >= 2 and fields[1] == sink_name
        ),
        None,
    )
    if sink is not None:
        return sink

    logger.warning("Default sink %s was not found in pactl sink list", sink_name)
    return None


def default_sink_description(run: RunCommand = subprocess.run) -> str:
    sink = default_sink(run)
    if sink is None:
        return "System default (monitor)"
    return f"{_description_for_device(sink[0], ['list', 'sinks'], run) or sink[0]} (monitor)"


def audio_device_info(mode: str, run: RunCommand = subprocess.run) -> dict[str, str]:
    info = {"mode": mode}
    if mode in {"mic", "combined"}:
        info["mic_device"] = default_source_description(run)
    if mode in {"system", "combined"}:
        info["system_device"] = default_sink_description(run)
    return info
