"""FFmpeg executes validated scene edits, transitions, captions and audio mixing."""

from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import imageio_ffmpeg
from PIL import Image, ImageDraw
from ..timeline import SceneEdit, AudioEdit, media_edit, generation_dependency
from ..storage import digest, save_json
from ..cache import file_hash

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args, cwd, timeout=180):
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
        cwd=cwd,
        capture_output=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode:
        raise RuntimeError(
            "FFmpeg editing failed: " + result.stderr.decode(errors="replace")[-900:]
        )


def filter_path(path):
    return (
        str(Path(path).resolve())
        .replace("\\", "/")
        .replace(":", r"\:")
        .replace("'", r"\'")
    )


class FFmpegEditor:
    def __init__(self, renderer, cache, size=(1080, 1920), fps=30, workers=2):
        self.renderer, self.cache, self.size, self.fps = renderer, cache, size, fps
        self.workers = max(1, min(4, int(workers)))

    def scene_key(self, scene, brief):
        assets = self.renderer.assets
        hashes = {}
        for key in scene["asset_ids"]:
            path = self.renderer.commute_files.get(key) or assets.get(key, {}).get(
                "path"
            )
            if path and Path(path).is_file():
                hashes[key] = file_hash(path)
        return digest(
            [
                "ffmpeg-scene-v2",
                self.size,
                self.fps,
                {k: scene[k] for k in ["headline", "footnote", "seconds", "asset_ids"]},
                media_edit(scene.get("edit", {})),
                hashes,
                {
                    k: brief.get(k)
                    for k in ["brand", "product", "variant", "accent_color"]
                },
                generation_dependency(brief)
                if scene.get("edit", {}).get("mode", "reused") != "reused"
                else None,
            ]
        )

    def verify_scene(self, result):
        probe = self.renderer.probe(result["path"])
        stream = next(
            (item for item in probe["streams"] if item["codec_type"] == "video"), None
        )
        if (
            not stream
            or (stream["width"], stream["height"]) != self.size
            or abs(float(probe["format"]["duration"]) - result["duration"]) > 0.15
        ):
            raise ValueError(
                "Cached scene is not a valid clip of the expected size and duration."
            )

    def scene(self, scene, index, brief):
        edit = SceneEdit.model_validate(scene.get("edit", {}))
        extra = edit.transition_seconds if edit.transition == "fade" and index else 0
        duration = scene["seconds"] + extra
        key = self.scene_key(scene, brief) + ("-entry" if extra else "")

        def build(folder):
            self.cache.control.check_cancelled(self.cache.cid)
            width, height = self.size
            # Pillow draws static brand graphics; all temporal editing is performed by FFmpeg.
            base = Image.new("RGB", (1080, 1920), "#F2F7FA")
            draw = ImageDraw.Draw(base)
            self.renderer.draw_identity(draw)
            layout = edit.text
            minimum_font = self.renderer.get_font(36, True)
            lines = self.renderer.wrap_text(
                draw, scene["headline"], minimum_font, round(1080 * layout.width)
            )
            if len(lines) * 46 > 250 or any(
                draw.textlength(line, font=minimum_font) > round(1080 * layout.width)
                for line in lines
            ):
                raise ValueError(
                    "The headline cannot fit at the minimum readable size; shorten it or widen its layout."
                )
            self.renderer.draw_text(
                draw,
                scene["headline"],
                (round(1080 * layout.x), round(1920 * layout.y)),
                round(1080 * layout.width),
                250,
                layout.size,
                "#10272E",
                True,
            )
            if scene["footnote"]:
                self.renderer.draw_text(
                    draw, scene["footnote"], (84, 1390), 900, 105, 28, "#526A73"
                )
            base.resize(self.size).save(folder / "background.png")
            asset = scene["asset_ids"][0]
            source = self.renderer.commute_files.get(asset) or self.renderer.assets.get(
                asset, {}
            ).get("path")
            if edit.mode in ["generated", "hybrid"]:
                source = self.renderer.generated_source(scene, key)
            if not source or not Path(source).is_file():
                raise ValueError("Missing planned visual asset: " + asset)
            is_still = Path(source).suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]
            inputs = [
                "-loop",
                "1",
                "-framerate",
                self.fps,
                "-i",
                folder / "background.png",
            ]
            if is_still:
                inputs += ["-loop", "1", "-framerate", self.fps, "-i", source]
            else:
                info = self.renderer.probe(source)
                source_duration = float(info["format"]["duration"])
                end = edit.trim_end or min(source_duration, edit.trim_start + duration)
                if not 0 <= edit.trim_start < end <= source_duration + 0.02:
                    raise ValueError("Clip boundaries lie outside the uploaded video.")
                run_ffmpeg(
                    [
                        "-ss",
                        edit.trim_start,
                        "-i",
                        source,
                        "-t",
                        end - edit.trim_start,
                        "-an",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-threads",
                        "2",
                        folder / "selection.mp4",
                    ],
                    folder,
                )
                inputs += ["-stream_loop", "-1", "-i", folder / "selection.mp4"]
            boxw, boxh = (
                max(2, round(width * edit.width) // 2 * 2),
                max(2, round(height * edit.height) // 2 * 2),
            )
            x, y = round(width * edit.x), round(height * edit.y)
            frames = max(1, round(duration * self.fps))
            fit = "decrease" if edit.fit == "contain" else "increase"
            fit_filter = f"scale={boxw}:{boxh}:force_original_aspect_ratio={fit}"
            fit_filter += (
                f",pad={boxw}:{boxh}:(ow-iw)/2:(oh-ih)/2:color=white"
                if edit.fit == "contain"
                else f",crop={boxw}:{boxh}"
            )
            zoom = f"{edit.zoom_start}+({edit.zoom_end}-{edit.zoom_start})*on/{max(frames - 1, 1)}"
            # Input is pre-scaled; d=1 advances uploaded clips, d=frames animates a still.
            d = frames if is_still else 1
            movement = f",zoompan=z='{zoom}':x='iw/2-iw/zoom/2+iw*{edit.pan_x}*on/{frames}':y='ih/2-ih/zoom/2+ih*{edit.pan_y}*on/{frames}':d={d}:s={boxw}x{boxh}:fps={self.fps}"
            mask = ""
            if edit.mask == "wipe_left":
                mask = ",format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lte(X,W*min(T/0.8,1)),255,0)'"
            elif edit.mask == "circle":
                mask = ",format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lte(hypot(X-W/2,Y-H/2),hypot(W,H)*min(T/0.8,1)),255,0)'"
            graph = f"[1:v]{fit_filter}{movement}{mask},setsar=1[asset];[0:v][asset]overlay={x}:{y}:shortest=1,format=yuv420p,fps={self.fps},trim=duration={duration},setpts=PTS-STARTPTS[out]"
            if edit.mode == "hybrid":
                product = self.renderer.assets.get(asset, {}).get("path")
                if not product:
                    raise ValueError(
                        "Hybrid footage requires an uploaded product identity image."
                    )
                inputs += ["-loop", "1", "-framerate", self.fps, "-i", product]
                graph = graph.replace("[out]", "[ambient]")
                graph += f";[2:v]scale={boxw // 2}:{boxh // 2}:force_original_aspect_ratio=decrease[product];[ambient][product]overlay={x + boxw // 4}:{y + boxh // 4},format=yuv420p[out]"
            run_ffmpeg(
                [
                    *inputs,
                    "-filter_complex_threads",
                    "1",
                    "-filter_complex",
                    graph,
                    "-map",
                    "[out]",
                    "-an",
                    "-t",
                    duration,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-threads",
                    "2",
                    "-crf",
                    "20",
                    folder / "scene.mp4",
                ],
                folder,
            )
            return dict(
                path=str(folder / "scene.mp4"),
                duration=duration,
                _files=[folder / "scene.mp4"],
            )

        result, hit = self.cache.run(
            "scene", key, build, self.verify_scene, {"render_seconds": scene["seconds"]}
        )
        self.renderer.note(
            f"Scene {index + 1}/5: {'reused verified clip' if hit else 'edited with FFmpeg'}"
        )
        edit = SceneEdit.model_validate(scene.get("edit", {}))
        if edit.mode != "reused":
            from ..generation_contract import generation_key

            source = dict(self.renderer.generated_records[generation_key(scene, brief)])
            source["source"] = (
                "compositing" if edit.mode == "hybrid" else "generated footage"
            )
            source.update(
                requested_seconds=scene["seconds"],
                duration_fill="loop",
                model_clip_seconds=edit.generation_settings.frames
                / edit.generation_settings.fps,
            )
            if edit.mode == "hybrid":
                source["overlay_asset"] = scene["asset_ids"][0]
        else:
            asset = scene["asset_ids"][0]
            path = self.renderer.commute_files.get(asset) or self.renderer.assets.get(
                asset, {}
            ).get("path")
            if not path or not Path(path).is_file():
                raise ValueError("Missing planned visual asset: " + asset)
            source = dict(
                source="image animation"
                if Path(path).suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]
                else "uploaded footage",
                asset=asset,
                sha256=file_hash(path),
                requested_seconds=scene["seconds"],
            )
        return dict(result, source=source)

    def encode(self, ad, brief, folder):
        scenes = ad["scenes"]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [
                pool.submit(self.scene, scene, i, brief)
                for i, scene in enumerate(scenes)
            ]
            clips = [future.result() for future in futures]
        self.scene_sources = [clip["source"] for clip in clips]
        inputs = [value for clip in clips for value in ["-i", clip["path"]]]
        graph = [
            f"[{i}:v]settb=AVTB,fps={self.fps},format=yuv420p[v{i}]"
            for i in range(len(clips))
        ]
        previous, elapsed = "v0", scenes[0]["seconds"]
        for i in range(1, len(clips)):
            edit = SceneEdit.model_validate(scenes[i].get("edit", {}))
            name = f"joined{i}"
            if edit.transition == "fade":
                graph.append(
                    f"[{previous}][v{i}]xfade=transition=fade:duration={edit.transition_seconds}:offset={elapsed - edit.transition_seconds}[{name}]"
                )
            else:
                graph.append(f"[{previous}][v{i}]concat=n=2:v=1:a=0[{name}]")
            elapsed += scenes[i]["seconds"]
            previous = name
        run_ffmpeg(
            [
                *inputs,
                "-filter_complex_threads",
                "1",
                "-filter_complex",
                ";".join(graph),
                "-map",
                f"[{previous}]",
                "-an",
                "-t",
                20,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                folder / "silent.mp4",
            ],
            folder,
        )
        return clips

    def mux(self, ad, cues, folder):
        audio = AudioEdit.model_validate(ad.get("audio", {}))
        events = []
        measure = ImageDraw.Draw(Image.new("RGB", (1080, 1920)))
        font = self.renderer.get_font(46, True)
        for cue in cues:
            chunks = []
            words = []
            for word in cue["caption"].split():
                if measure.textlength(word, font=font) > 840:
                    raise ValueError(
                        "A caption word is too wide to read safely; shorten the product label or copy."
                    )
                trial = " ".join([*words, word])
                if (
                    words
                    and len(self.renderer.wrap_text(measure, trial, font, 840)) > 2
                ):
                    chunks.append(" ".join(words))
                    words = [word]
                else:
                    words.append(word)
            if words:
                chunks.append(" ".join(words))
            weights = [len(part) for part in chunks]
            total = max(1, sum(weights))
            elapsed = cue["start"]
            for i, part in enumerate(chunks):
                end = (
                    cue["end"]
                    if i == len(chunks) - 1
                    else elapsed + (cue["end"] - cue["start"]) * weights[i] / total
                )
                events.append(dict(start=elapsed, end=end, caption=part))
                elapsed = end
        self.renderer.caption_files(events, folder)
        ass = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {self.size[0]}",
            f"PlayResY: {self.size[1]}",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Alignment, MarginL, MarginR, MarginV, BorderStyle, Outline, Shadow",
            f"Style: Caption,Arial,{max(18, round(self.size[0] * 0.043))},&H00FFFFFF,&H001A1A1A,&H80000000,-1,2,50,50,{round(self.size[1] * 0.15)},3,2,0",
            "[Events]",
            "Format: Layer, Start, End, Style, Text",
        ]

        def stamp(value):
            return (
                f"{int(value) // 3600}:{int(value) // 60 % 60:02d}:{value % 60:05.2f}"
            )

        for cue in events:
            text = (
                cue["caption"]
                .replace("\\", " ")
                .replace("{", "(")
                .replace("}", ")")
                .replace("\n", r"\N")
            )
            ass.append(
                f"Dialogue: 0,{stamp(cue['start'])},{stamp(cue['end'])},Caption,{text}"
            )
        (folder / "captions.ass").write_text("\n".join(ass), encoding="utf-8-sig")
        graph = (
            f"[1:a]volume={audio.voice_gain},asplit=2[voice][side];"
            f"[2:a]volume={audio.music_gain}[music];"
            "[music][side]sidechaincompress=threshold=0.04:ratio=6:attack=15:release=180[ducked];"
            "[voice][ducked]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=9[mix];"
            f"[0:v]ass=filename='{filter_path(folder / 'captions.ass')}'[video]"
        )
        run_ffmpeg(
            [
                "-i",
                folder / "silent.mp4",
                "-i",
                folder / "voice.wav",
                "-i",
                folder / "music-original.wav",
                "-filter_complex_threads",
                "1",
                "-filter_complex",
                graph,
                "-map",
                "[video]",
                "-map",
                "[mix]",
                "-t",
                20,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                48000,
                "-ac",
                2,
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                folder / "final.mp4",
            ],
            folder,
        )
        save_json(
            folder / "edit_manifest.json",
            dict(
                editor="FFmpeg",
                scene_sources=getattr(self, "scene_sources", []),
                timeline=ad,
                size=self.size,
                fps=self.fps,
                inputs=[
                    dict(role=key, sha256=value)
                    for key, value in self.renderer.brief.get(
                        "asset_hashes", {}
                    ).items()
                ],
                captions=cues,
                caption_events=events,
                human_review_required=[
                    "Product appearance",
                    "Readability on a phone",
                    "Narration and caption correspondence",
                ],
            ),
        )
