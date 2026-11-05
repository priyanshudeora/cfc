"""Brand-aware composition from each campaign's actual product images and footage."""

from pathlib import Path
from functools import lru_cache
import hashlib
from PIL import Image as PILImage, ImageFont
from ..config import INK, MUTED


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
