import subprocess

from meeting_notes.audio_devices import audio_device_info, default_sink


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["pactl"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_default_sink_uses_name_to_find_sink_id():
    def fake_run(cmd, **_kwargs):
        if cmd == ["pactl", "get-default-sink"]:
            return _completed("alsa_output.pci-0000_00_1f.3.analog-stereo\n")
        if cmd == ["pactl", "list", "sinks", "short"]:
            return _completed("1078 alsa_output.pci-0000_00_1f.3.analog-stereo PipeWire s16le 2ch 48000Hz\n")
        raise AssertionError(cmd)

    sink = default_sink(fake_run)

    assert sink is not None
    assert sink.name == "alsa_output.pci-0000_00_1f.3.analog-stereo"
    assert sink.sink_id == "1078"


def test_default_sink_falls_back_when_sink_listing_raises(caplog):
    def fake_run(cmd, **_kwargs):
        if cmd == ["pactl", "get-default-sink"]:
            return _completed("alsa_output.speakers\n")
        if cmd == ["pactl", "list", "sinks", "short"]:
            raise subprocess.TimeoutExpired(cmd, timeout=2)
        raise AssertionError(cmd)

    assert default_sink(fake_run) is None
    assert "Could not list audio sinks" in caplog.text


def test_audio_device_info_uses_descriptions_when_available():
    def fake_run(cmd, **_kwargs):
        if cmd == ["pactl", "get-default-source"]:
            return _completed("alsa_input.usb-mic\n")
        if cmd == ["pactl", "list", "sources"]:
            return _completed("Name: alsa_input.usb-mic\n\tDescription: USB Mic\n")
        if cmd == ["pactl", "get-default-sink"]:
            return _completed("alsa_output.speakers\n")
        if cmd == ["pactl", "list", "sinks", "short"]:
            return _completed("42 alsa_output.speakers PipeWire s16le 2ch 48000Hz\n")
        if cmd == ["pactl", "list", "sinks"]:
            return _completed("Name: alsa_output.speakers\n\tDescription: Desk Speakers\n")
        raise AssertionError(cmd)

    assert audio_device_info("combined", fake_run) == {
        "mode": "combined",
        "mic_device": "USB Mic",
        "system_device": "Desk Speakers (monitor)",
    }


def test_audio_device_info_falls_back_when_description_listing_raises(caplog):
    def fake_run(cmd, **_kwargs):
        if cmd == ["pactl", "get-default-source"]:
            return _completed("alsa_input.usb-mic\n")
        if cmd == ["pactl", "get-default-sink"]:
            return _completed("alsa_output.speakers\n")
        if cmd == ["pactl", "list", "sinks", "short"]:
            return _completed("42 alsa_output.speakers PipeWire s16le 2ch 48000Hz\n")
        if cmd == ["pactl", "list", "sources"] or cmd == ["pactl", "list", "sinks"]:
            raise subprocess.TimeoutExpired(cmd, timeout=2)
        raise AssertionError(cmd)

    assert audio_device_info("combined", fake_run) == {
        "mode": "combined",
        "mic_device": "alsa_input.usb-mic",
        "system_device": "alsa_output.speakers (monitor)",
    }
    assert "Could not list audio device descriptions" in caplog.text


def test_audio_device_info_falls_back_loudly_when_pactl_fails(caplog):
    def fake_run(_cmd, **_kwargs):
        return _completed(returncode=1, stderr="no server")

    info = audio_device_info("combined", fake_run)

    assert info == {
        "mode": "combined",
        "mic_device": "System default",
        "system_device": "System default (monitor)",
    }
    assert "pactl get-default-source failed" in caplog.text
    assert "pactl get-default-sink failed" in caplog.text
