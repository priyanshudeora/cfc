"""Brand-aware composition from each campaign's actual product images and footage."""

from pathlib import Path
from functools import lru_cache
import hashlib
import imageio_ffmpeg
from PIL import Image as PILImage, ImageDraw, ImageFont
from ..config import VIDEO_SIZE, INK, MUTED


class VisualMixin:
    @lru_cache(maxsize=128)
    def get_font(self, size, bold=False):
        windows = "segoeuib.ttf" if bold else "segoeui.ttf"
        linux = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        for path in [
            Path("C:/Windows/Fonts") / windows,
            Path("/usr/share/fonts/truetype/dejavu") / linux,
        ]:
            if path.exists():
                return ImageFont.truetype(str(path), size)
        return ImageFont.load_default(size=size)

    def wrap_text(self, draw, text, font, width):
        lines, line = [], ""
        for word in str(text).split():
            trial = (line + " " + word).strip()
            if line and draw.textlength(trial, font=font) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        return lines + ([line] if line else [])

    def draw_text(self, draw, text, xy, width, height, size, color, bold=False):
        for current_size in range(size, 9, -1):
            font = self.get_font(current_size, bold)
            lines = self.wrap_text(draw, text, font, width)
            if len(lines) * (current_size + 10) <= height and all(
                draw.textlength(line, font=font) <= width for line in lines
            ):
                break
        for i, line in enumerate(lines):
            if (i + 1) * (current_size + 10) <= height:
                draw.text(
                    (xy[0], xy[1] + i * (current_size + 10)),
                    line,
                    font=font,
                    fill=color,
                )

    @property
    def accent(self):
        return self.brief.get("accent_color", "#00AABD")

    def identity_images(self):
        images = {}
        for key, asset in self.assets.items():
            if asset["use"] != "identity":
                continue
            path = Path(asset["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
                raise RuntimeError("Product identity file changed: " + key)
            with PILImage.open(path) as source:
                from PIL import ImageOps

                images[key] = ImageOps.exif_transpose(source).convert("RGBA")
        if not images:
            raise ValueError(
                "Upload a product image in the campaign brief before rendering."
            )
        return images

    def draw_identity(self, draw, dark=False):
        color = "white" if dark else INK
        product = self.brief.get("product", "Product")
        brand = self.brief.get("brand", "")
        self.draw_text(draw, brand or product, (84, 91), 912, 88, 58, color, True)
        subtitle = " · ".join(
            filter(None, [product if brand else "", self.brief.get("variant", "")])
        )
        if subtitle:
            self.draw_text(
                draw, subtitle, (84, 185), 912, 72, 30, "#C5E6ED" if dark else MUTED
            )
        draw.line((84, 265, 996, 265), fill=self.accent, width=3)

    def place_photo(self, frame, photo, center, box, scale=1.0):
        ratio = min(box[0] / photo.width, box[1] / photo.height) * scale
        size = max(1, round(photo.width * ratio)), max(1, round(photo.height * ratio))
        resized = photo.resize(size, PILImage.Resampling.LANCZOS)
        frame.alpha_composite(
            resized, (round(center[0] - size[0] / 2), round(center[1] - size[1] / 2))
        )

    def still_frame(self, ad, scene, index, local, photos):
        dark = ad["video_type"] == "product_reveal"
        lifestyle = ad["video_type"] in ["lifestyle_story", "student_commute"]
        background = "#101D2B" if dark else "#FFF9F1" if lifestyle else "#F2F7FA"
        frame = PILImage.new("RGBA", VIDEO_SIZE, background)
        d = ImageDraw.Draw(frame)
        if dark:
            for radius in [425, 470, 515]:
                d.ellipse(
                    (540 - radius, 980 - radius, 540 + radius, 980 + radius),
                    outline=self.accent,
                    width=2,
                )
        elif lifestyle:
            d.rounded_rectangle((720, 440, 1120, 1490), 70, fill=self.accent)
        else:
            d.ellipse((650, 380, 1670, 1400), outline=self.accent, width=3)
        self.draw_identity(d, dark)
        shift = round(20 * (1 - min(local / 0.4, 1)) ** 3)
        self.draw_text(
            d,
            scene["headline"],
            (84, 314 + shift),
            912,
            234,
            92,
            "white" if dark else INK,
            True,
        )
        d.rounded_rectangle((84, 570, 996, 1355), 36, fill="white")
        key = next((key for key in scene["asset_ids"] if key in photos), None)
        if key is None:
            raise ValueError(
                "Scene needs its selected product image or uploaded footage."
            )
        progress = min(local / scene["seconds"], 1)
        self.place_photo(
            frame,
            photos[key],
            (540 - 7 * progress, 962),
            (840, 720),
            1 + 0.012 * progress,
        )
        if dark and index == 0:
            # Reveal only the provided image. Never invent a new product angle.
            d = ImageDraw.Draw(frame)
            edge = round(84 + 912 * (0.12 + 0.88 * min(local / 2.5, 1) ** 0.7))
            if edge < 996:
                d.rectangle((edge, 570, 996, 1355), fill=background)
                d.line((edge, 570, edge, 1355), fill=self.accent, width=5)
        return frame, dark

    def footage_readers(self, used):
        readers = {}
        try:
            for key in used:
                path = self.commute_files.get(key)
                if not path or not Path(path).is_file():
                    raise ValueError("Upload the planned lifestyle footage: " + key)
                reader = imageio_ffmpeg.read_frames(
                    path,
                    pix_fmt="rgb24",
                    input_params=["-stream_loop", "-1"],
                    output_params=[
                        "-vf",
                        "scale=540:960:force_original_aspect_ratio=increase,crop=540:960,setsar=1",
                        "-r",
                        "30",
                    ],
                )
                readers[key] = reader
                next(reader)
            return readers
        except Exception:
            for reader in readers.values():
                reader.close()
            raise

    def footage_frame(self, scene, key, readers):
        raw = next(readers[key])
        frame = (
            PILImage.frombytes("RGB", (540, 960), raw)
            .resize(VIDEO_SIZE)
            .convert("RGBA")
        )
        overlay = PILImage.new("RGBA", VIDEO_SIZE)
        d = ImageDraw.Draw(overlay)
        d.rectangle((0, 0, 1080, 570), fill=(6, 20, 27, 195))
        d.rectangle((0, 1355, 1080, 1920), fill=(6, 20, 27, 175))
        frame.alpha_composite(overlay)
        d = ImageDraw.Draw(frame)
        self.draw_identity(d, True)
        self.draw_text(d, scene["headline"], (84, 314), 912, 234, 92, "white", True)
        return frame, True

    def burn_caption(self, frame, text, opacity=1.0):
        overlay = PILImage.new("RGBA", VIDEO_SIZE)
        d = ImageDraw.Draw(overlay)
        for size in range(48, 35, -1):
            font = self.get_font(size, True)
            lines = self.wrap_text(d, text, font, 800)
            if len(lines) <= 2:
                break
        if len(lines) > 2:
            raise RuntimeError("Caption cannot fit in two readable lines.")
        height, y = len(lines) * (size + 10) + 38, 1500
        d.rounded_rectangle(
            (84, y, 944, y + height), 22, fill=(16, 39, 46, round(245 * opacity))
        )
        for index, line in enumerate(lines):
            x = 514 - d.textlength(line, font=font) / 2
            d.text(
                (x, y + 15 + index * (size + 10)),
                line,
                font=font,
                fill=(255, 255, 255, round(255 * opacity)),
            )
        frame.alpha_composite(overlay)

    def concept_frame(self, ad, cues, photos, clock, readers):
        elapsed = 0
        for index, scene in enumerate(ad["scenes"]):
            if clock < elapsed + scene["seconds"]:
                break
            elapsed += scene["seconds"]
        footage = next((key for key in scene["asset_ids"] if key in readers), None)
        if footage:
            frame, dark = self.footage_frame(scene, footage, readers)
        else:
            frame, dark = self.still_frame(ad, scene, index, clock - elapsed, photos)
        d = ImageDraw.Draw(frame)
        if scene.get("footnote"):
            self.draw_text(
                d,
                scene["footnote"],
                (94, 1380),
                866,
                105,
                28,
                "#D2EEF1" if dark else MUTED,
            )
        cue = cues[index]
        if cue["start"] <= clock <= cue["end"] + 0.08:
            self.burn_caption(
                frame, cue["caption"], min(1.0, max(0.0, (clock - cue["start"]) / 0.1))
            )
        d = ImageDraw.Draw(frame)
        d.line((84, 1692, 996, 1692), fill="#39505B" if dark else "#D6E0E8", width=3)
        d.line(
            (84, 1692, 84 + 912 * min(clock / 20, 1), 1692), fill=self.accent, width=4
        )
        return frame.convert("RGB")
