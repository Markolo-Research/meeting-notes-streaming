import os
from collections.abc import Callable, Mapping
from pathlib import Path
import shutil
import subprocess
from typing import Any, NamedTuple

Which = Callable[[str], str | None]
Popen = Callable[..., Any]

TERMINAL_COMMANDS = {
    "alacritty": ["alacritty", "-e"],
    "kitty": ["kitty"],
    "ghostty": ["ghostty", "-e"],
    "wezterm": ["wezterm", "start", "--"],
    "foot": ["foot"],
    "gnome-terminal": ["gnome-terminal", "--"],
    "konsole": ["konsole", "-e"],
    "xterm": ["xterm", "-e"],
    "urxvt": ["urxvt", "-e"],
    "st": ["st", "-e"],
}

FILE_MANAGERS = (
    (["dolphin", "--select"], "dolphin"),
    (["nautilus", "--select"], "nautilus"),
    (["nemo"], "nemo"),
    (["thunar"], "thunar"),
    (["pcmanfm", "--select"], "pcmanfm"),
)

TERMINAL_FILE_BROWSERS = ("ranger", "yazi", "lf", "nnn", "vifm", "mc", "vidir", "joshuto", "broot")


class FolderOpenResult(NamedTuple):
    label: str
    opened_folder_only: bool = False


def copy_text_to_clipboard(
    text: str,
    *,
    which: Which = shutil.which,
    popen: Popen = subprocess.Popen,
) -> bool:
    """Copy text using the first available Linux clipboard command."""
    for command in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard"]):
        if not which(command[0]):
            continue
        process = popen(command, stdin=subprocess.PIPE)
        process.communicate(text.encode())
        return True
    return False


def _try_popen(command: list[str], popen: Popen) -> bool:
    try:
        popen(command)
        return True
    except OSError:
        return False


def open_in_new_terminal(
    command: str,
    target: str,
    *,
    environ: Mapping[str, str] = os.environ,
    which: Which = shutil.which,
    popen: Popen = subprocess.Popen,
) -> bool:
    if environ.get("TMUX") and _try_popen(["tmux", "new-window", "--", command, target], popen):
        return True

    terminal = environ.get("TERMINAL")
    terminal_command = TERMINAL_COMMANDS.get(Path(terminal).name) if terminal else None
    if terminal_command is not None and _try_popen([*terminal_command, command, target], popen):
        return True

    for terminal_name, terminal_command in TERMINAL_COMMANDS.items():
        if which(terminal_name) and _try_popen([*terminal_command, command, target], popen):
            return True

    return False


def show_path_in_file_manager(
    note_path: Path,
    terminal_browser: str = "",
    *,
    environ: Mapping[str, str] = os.environ,
    which: Which = shutil.which,
    popen: Popen = subprocess.Popen,
) -> FolderOpenResult:
    file_path = str(note_path.absolute())
    folder = str(note_path.parent)

    if (
        terminal_browser
        and which(terminal_browser)
        and open_in_new_terminal(terminal_browser, folder, environ=environ, which=which, popen=popen)
    ):
        return FolderOpenResult(terminal_browser)

    for command_prefix, label in FILE_MANAGERS:
        if which(command_prefix[0]):
            popen([*command_prefix, file_path])
            return FolderOpenResult(label)

    for browser in TERMINAL_FILE_BROWSERS:
        if which(browser) and open_in_new_terminal(browser, folder, environ=environ, which=which, popen=popen):
            return FolderOpenResult(browser)

    popen(["xdg-open", folder])
    return FolderOpenResult("folder", opened_folder_only=True)
