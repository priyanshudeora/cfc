"""AudioMixin: local video generation helpers."""

import re, wave
import numpy as np
import requests
import soundfile as sf
import imageio_ffmpeg
from ..config import *
from ..storage import save_json

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


class AudioMixin:
    def ensure_notebook_model(self, name, size):
        path = self.model_dir / name
        if path.exists() and path.stat().st_size == size:
            return path
        partial = path.with_suffix(path.suffix + ".part")
        self.note("Downloading local voice model: " + name)
        with requests.get(MODEL_BASE + name, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk)
        if partial.stat().st_size != size:
            raise RuntimeError("Incomplete model download: " + name)
        partial.replace(path)
        return path

    def spoken_text(self, text):
        substitutions = self.brief.get("pronunciations", {})
        for source, target in substitutions.items():
            text = re.sub("\\b" + re.escape(source) + "\\b", target, text)
        return text

    def read_pcm(self, path):
        with wave.open(str(path), "rb") as sound:
            channels, rate = (sound.getnchannels(), sound.getframerate())
            data = np.frombuffer(
                sound.readframes(sound.getnframes()), dtype="<i2"
            ).astype(np.float32)
        return (data.reshape(-1, channels) / 32768, rate)

    def write_pcm(self, path, samples, rate=AUDIO_SR):
        data = np.asarray(samples)
        if data.ndim == 1:
            data = data[:, None]
        if np.max(np.abs(data)) > 1.0:
            raise ValueError("Audio would clip")
        with wave.open(str(path), "wb") as sound:
            sound.setnchannels(data.shape[1])
            sound.setsampwidth(2)
            sound.setframerate(rate)
            sound.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())

    def generate_voice(self, ad, folder):
        results = []
        with self.voice_lock:
            engine = self.notebook_kokoro()
            for index, scene in enumerate(ad["scenes"]):
                path = folder / f"voice-raw-{index}.wav"
                spoken = self.spoken_text(scene["narration"])
                if not path.exists():
                    samples, rate = engine.create(
                        spoken,
                        voice=self.voice_id,
                        speed=self.voice_speed,
                        lang="en-us",
                    )
                    sf.write(path, samples, rate, subtype="PCM_16")
                samples, rate = sf.read(path)
                if rate != AUDIO_SR or len(samples) == 0:
                    raise RuntimeError("Invalid local speech output")
                results.append(
                    dict(
                        path=str(path),
                        seconds=len(samples) / rate,
                        text=scene["narration"],
                        spoken=spoken,
                    )
                )
                provider = (
                    "ElevenLabs"
                    if self.voice_backend == "elevenlabs"
                    else "local Kokoro"
                )
                self.note(
                    f"Voice scene {index + 1}/{len(ad['scenes'])} ready ({provider})."
                )
        save_json(folder / "voice-scenes.json", results)
        return results

    def fit_voice(self, raw, scene, index, folder):
        path = folder / f"voice-fit-{index}.wav"
        available = scene["seconds"] - 0.4
        speed = max(1.0, raw["seconds"] / available)
        if speed > 1.22:
            raise RuntimeError(
                f"Scene {index + 1} voice needs a gentler rewrite; refusing rushed speech."
            )
        self.ffmpeg(
            [
                "-i",
                raw["path"],
                "-af",
                f"atempo={speed:.6f}",
                "-ac",
                "1",
                "-ar",
                str(AUDIO_SR),
                "-c:a",
                "pcm_s16le",
                str(path),
            ],
            folder,
        )
        data, rate = self.read_pcm(path)
        data = data[:, 0]
        if len(data) / rate > available + 0.06:
            raise RuntimeError("Scene voice exceeds its timing slot.")
        return (
            data,
            dict(raw, speed_adjustment=speed, fitted_seconds=len(data) / rate),
        )

    def voice_timeline(self, ad, folder):
        raw = self.generate_voice(ad, folder)
        voice = np.zeros(20 * AUDIO_SR, dtype=np.float32)
        cues, elapsed = ([], 0)
        for index, scene in enumerate(ad["scenes"]):
            data, info = self.fit_voice(raw[index], scene, index, folder)
            start = elapsed + 0.2
            offset = round(start * AUDIO_SR)
            voice[offset : offset + len(data)] = data
            cues.append(
                dict(
                    start=start,
                    end=start + len(data) / AUDIO_SR,
                    caption=scene["caption"],
                    **info,
                )
            )
            elapsed += scene["seconds"]
            self.note(
                f"Voice {index + 1}/5 fitted: {len(data) / AUDIO_SR:.2f}s in {scene['seconds']}s scene."
            )
        self.write_pcm(folder / "voice-placed.wav", voice)
        self.ffmpeg(
            [
                "-i",
                "voice-placed.wav",
                "-af",
                "loudnorm=I=-16:TP=-2:LRA=9",
                "-ar",
                str(AUDIO_SR),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                "voice.wav",
            ],
            folder,
        )
        save_json(folder / "voice-cues.json", cues)
        return cues

    def add_tone(self, track, start, hz, seconds, amplitude, decay=6):
        offset = round(start * AUDIO_SR)
        count = min(round(seconds * AUDIO_SR), len(track) - offset)
        if count <= 0:
            return
        t = np.arange(count) / AUDIO_SR
        sound = np.sin(2 * np.pi * hz * t) + 0.18 * np.sin(4 * np.pi * hz * t)
        track[offset : offset + count] += amplitude * sound * np.exp(-decay * t)

    def music_beat(self, music, beat, rng):
        t = np.arange(round(0.23 * AUDIO_SR)) / AUDIO_SR
        kick = np.sin(2 * np.pi * (47 * t + 35 * (1 - np.exp(-30 * t)) / 30))
        kick *= np.exp(-20 * t) * 0.55
        offset = round(beat * AUDIO_SR)
        count = min(len(kick), len(music) - offset)
        music[offset : offset + count] += kick[:count]
        if count > 0:
            hat = rng.normal(0, 0.014, count) * np.exp(-70 * t[:count])
            music[offset : offset + count] += hat

    def make_music(self, folder, ad):
        music = np.zeros(20 * AUDIO_SR, dtype=np.float64)
        bpm = {
            "product_demo": 108,
            "student_commute": 100,
            "lifestyle_story": 100,
            "product_reveal": 112,
        }[ad["video_type"]]
        rng, beat = (np.random.default_rng(141 + bpm - 108), 60 / bpm)
        notes = [261.63, 329.63, 392.0, 329.63, 293.66, 392.0, 440.0, 392.0]
        for index, start in enumerate(np.arange(0, 20, beat)):
            self.music_beat(music, float(start), rng)
            self.add_tone(
                music, start, [65.41, 73.42, 87.31, 73.42][index // 4 % 4], 0.44, 0.1, 8
            )
            if index % 2 == 0:
                self.add_tone(
                    music, start + beat / 2, notes[index // 2 % 8], 0.42, 0.07, 9
                )
        music *= min(0.95 / max(abs(music)), 0.18 / np.sqrt(np.mean(music**2)))
        t = np.arange(len(music)) / AUDIO_SR
        music *= np.minimum(t / 0.15, 1) * np.minimum((20 - t) / 0.7, 1)
        stereo = np.column_stack([music, music * 0.94 + np.roll(music, 100) * 0.06])
        self.write_pcm(folder / "music-original.wav", stereo)
        save_json(
            folder / "music-provenance.json",
            dict(
                kind="Original procedural synthesis",
                bpm=bpm,
                seed=141 + bpm - 108,
                source_track=None,
                tools="NumPy",
                api_cost=0,
            ),
        )
        return stereo

    def mix_audio(self, folder, cues, ad):
        voice, rate = self.read_pcm(folder / "voice.wav")
        voice = voice[: 20 * AUDIO_SR]
        if len(voice) < 20 * AUDIO_SR:
            voice = np.pad(voice, ((0, 20 * AUDIO_SR - len(voice)), (0, 0)))
        music = self.make_music(folder, ad)
        gain = np.full(len(voice), 0.42)
        for cue in cues:
            start, end = (
                max(0, int((cue["start"] - 0.12) * rate)),
                min(len(gain), int((cue["end"] + 0.15) * rate)),
            )
            gain[start:end] = 0.18
        gain = np.convolve(
            np.pad(gain, (1200, 1200), mode="edge"), np.ones(2401) / 2401, mode="valid"
        )
        mixed = voice + music * gain[:, None]
        mixed *= min(1.0, 0.94 / np.max(np.abs(mixed)))
        self.write_pcm(folder / "mix.wav", mixed)
        self.note("Original music mixed under speech, with fades and voice ducking.")

    def subtitle_time(self, seconds, separator=","):
        millis = round(seconds * 1000)
        return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis // 1000 % 60:02d}{separator}{millis % 1000:03d}"

    def caption_files(self, cues, folder):
        srt, vtt = ([], ["WEBVTT", ""])
        for number, cue in enumerate(cues, 1):
            text = cue["caption"]
            srt += [
                str(number),
                self.subtitle_time(cue["start"])
                + " --> "
                + self.subtitle_time(cue["end"]),
                text,
                "",
            ]
            vtt += [
                self.subtitle_time(cue["start"], ".")
                + " --> "
                + self.subtitle_time(cue["end"], "."),
                text,
                "",
            ]
        (folder / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
        (folder / "captions.vtt").write_text("\n".join(vtt), encoding="utf-8")
