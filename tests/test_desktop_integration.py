from pathlib import Path

from meeting_notes.desktop_integration import copy_text_to_clipboard, open_in_new_terminal, show_path_in_file_manager


def test_copy_text_to_clipboard_uses_first_available_command():
    calls = []

    class Process:
        def communicate(self, data):
            calls.append(("communicate", data))

    def fake_which(command):
        return command if command == "xclip" else None

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    assert copy_text_to_clipboard("hello", which=fake_which, popen=fake_popen)
    assert calls == [
        (["xclip", "-selection", "clipboard"], {"stdin": -1}),
        ("communicate", b"hello"),
    ]


def test_open_in_new_terminal_prefers_tmux():
    calls = []

    assert open_in_new_terminal(
        "nvim",
        "notes/example.md",
        environ={"TMUX": "/tmp/tmux-1000/default,1,0"},
        which=lambda _command: None,
        popen=lambda command: calls.append(command),
    )

    assert calls == [["tmux", "new-window", "--", "nvim", "notes/example.md"]]


def test_open_in_new_terminal_uses_configured_terminal_before_autodetect():
    calls = []

    assert open_in_new_terminal(
        "nvim",
        "notes/example.md",
        environ={"TERMINAL": "/usr/bin/ghostty"},
        which=lambda command: command if command == "alacritty" else None,
        popen=lambda command: calls.append(command),
    )

    assert calls == [["ghostty", "-e", "nvim", "notes/example.md"]]


def test_show_path_in_file_manager_prefers_configured_terminal_browser(tmp_path):
    calls = []
    note_path = tmp_path / "notes" / "example.md"
    note_path.parent.mkdir()
    note_path.write_text("content", encoding="utf-8")

    result = show_path_in_file_manager(
        note_path,
        "yazi",
        environ={"TERMINAL": "kitty"},
        which=lambda command: command if command in {"yazi", "kitty"} else None,
        popen=lambda command: calls.append(command),
    )

    assert result.label == "yazi"
    assert not result.opened_folder_only
    assert calls == [["kitty", "yazi", str(note_path.parent)]]


def test_show_path_in_file_manager_uses_desktop_file_manager(tmp_path):
    calls = []
    note_path = tmp_path / "notes" / "example.md"
    note_path.parent.mkdir()
    note_path.write_text("content", encoding="utf-8")

    result = show_path_in_file_manager(
        note_path,
        which=lambda command: command if command == "nautilus" else None,
        popen=lambda command: calls.append(command),
    )

    assert result.label == "nautilus"
    assert calls == [["nautilus", "--select", str(note_path.absolute())]]


def test_show_path_in_file_manager_falls_back_to_xdg_open(tmp_path):
    calls = []
    note_path = tmp_path / "notes" / "example.md"
    note_path.parent.mkdir()
    note_path.write_text("content", encoding="utf-8")

    result = show_path_in_file_manager(
        note_path,
        which=lambda _command: None,
        popen=lambda command: calls.append(command),
    )

    assert result.label == "folder"
    assert result.opened_folder_only
    assert calls == [["xdg-open", str(note_path.parent)]]
