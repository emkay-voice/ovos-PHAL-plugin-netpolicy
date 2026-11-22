"""Whether the device can hear and speak with everything blocked.

Local only blocks every OVOS service. If speech recognition or the voice is a
server plugin, the device would then neither hear nor speak – and "nothing
you say leaves the device" would be true only because nothing works. So the
plugin goes local only only when both are on-device plugins, and says why
otherwise. The lists are the same as ovos-skill-guardian's; a device with
another on-device plugin names it in ``local_speech``.
"""

LOCAL_STT = ("ovos-stt-plugin-whisper-browser", "ovos-stt-plugin-fasterwhisper",
             "ovos-stt-plugin-whispercpp", "ovos-stt-plugin-vosk", "ovos-stt-plugin-nemo",
             "ovos-stt-plugin-citrinet", "ovos-stt-plugin-wav2vec", "ovos-stt-plugin-chromium-local",
             "ovos-stt-plugin-pocketsphinx", "ovos-stt-plugin-moonshine")
LOCAL_TTS = ("ovos-tts-plugin-piper", "ovos-tts-plugin-piper-browser", "ovos-tts-plugin-mimic",
             "ovos-tts-plugin-espeakng", "ovos-tts-plugin-espeak", "ovos-tts-plugin-pico",
             "ovos-tts-plugin-sam", "ovos-tts-plugin-beepspeak", "ovos-tts-plugin-dummy",
             "ovos-tts-plugin-kokoro", "ovos-tts-plugin-matxa-multispeaker-cat")


def modules(config: dict) -> tuple:
    """The STT and TTS modules in use, from mycroft.conf."""
    return ((config.get("stt") or {}).get("module", ""),
            (config.get("tts") or {}).get("module", ""))


def is_local(module: str, kind: str, extra=()) -> bool:
    known = LOCAL_STT if kind == "stt" else LOCAL_TTS
    return bool(module) and (module in known or module in (extra or ()))


def check(config: dict, extra=()) -> dict:
    """{"stt", "tts", "stt_local", "tts_local", "local"}"""
    stt, tts = modules(config)
    stt_local, tts_local = is_local(stt, "stt", extra), is_local(tts, "tts", extra)
    return {"stt": stt, "tts": tts, "stt_local": stt_local, "tts_local": tts_local,
            "local": stt_local and tts_local}
