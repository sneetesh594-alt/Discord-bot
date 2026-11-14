"""Small, self-contained PNG rank cards. No member images are persisted."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from aegis.levels import progress


def rank_card(
    name: str,
    xp: int,
    rank: int | None,
    *,
    avatar: bytes | None = None,
    server: str = "YOUR COMMUNITY",
    username: str = "",
    next_reward: str = "",
) -> bytes:
    image = Image.new("RGB", (900, 400), "#10101a")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (16, 16, 884, 384), radius=24, fill="#171723", outline="#373249", width=2
    )
    banner = Image.new("RGB", image.size, "#302747")
    banner_draw = ImageDraw.Draw(banner)
    banner_draw.ellipse((610, -105, 930, 215), outline="#493867", width=14)
    banner_draw.ellipse((670, -45, 870, 155), outline="#5c4380", width=3)
    banner_mask = Image.new("L", image.size)
    mask_draw = ImageDraw.Draw(banner_mask)
    mask_draw.rounded_rectangle((18, 18, 882, 160), radius=22, fill=255)
    mask_draw.rectangle((18, 130, 882, 160), fill=255)
    image.paste(banner, (0, 0), banner_mask)
    font_path = Path(__file__).parent / "assets/fonts/NotoSansDevanagari.ttf"
    if not font_path.is_file():
        font_path = Path("C:/Windows/Fonts/Nirmala.ttf")
    fonts = {
        size: ImageFont.truetype(str(font_path), size=size)
        if font_path.is_file()
        else ImageFont.load_default(size=size)
        for size in (15, 18, 24, 30, 42)
    }
    draw.text((48, 44), "ALPHA", font=fonts[24], fill="#d4c5fa")
    draw.text((48, 78), "COMMUNITY PROGRESS", font=fonts[15], fill="#aea0c8")
    draw.ellipse((43, 107, 177, 241), fill="#171723")
    draw.ellipse((50, 114, 170, 234), fill="#55436f")
    if avatar:
        try:
            with Image.open(BytesIO(avatar)) as original:
                if original.width * original.height <= 1_000_000:
                    photo = ImageOps.fit(original.convert("RGB"), (120, 120))
                    mask = Image.new("L", (120, 120))
                    ImageDraw.Draw(mask).ellipse((0, 0, 119, 119), fill=255)
                    image.paste(photo, (50, 114), mask)
        except (OSError, ValueError):
            pass
    display_name = (
        name[:100]
        if font_path.is_file()
        else name.encode("latin-1", errors="replace").decode("latin-1")[:28]
    )
    community = (
        server[:40]
        if font_path.is_file()
        else server.encode("latin-1", errors="replace").decode("latin-1")[:40]
    )

    def fit(text, font, maximum):
        if draw.textlength(text, font=font) <= maximum:
            return text
        while text and draw.textlength(text + "...", font=font) > maximum:
            text = text[:-1]
        return text + "..."

    display_name = fit(display_name, fonts[30], 470)
    community = fit(community, fonts[15], 470)
    draw.text((208, 177), display_name, font=fonts[30], fill="#f8f8ff")
    draw.text((208, 220), fit(username[:100], fonts[15], 440), font=fonts[15], fill="#9290a2")
    state = progress(xp)
    draw.text((58, 257), f"LEVEL {state.level}", font=fonts[18], fill="#f8f8ff")
    draw.text(
        (746, 257), f"{state.level + 1}" if state.needed else "MAX", font=fonts[18], fill="#f8f8ff"
    )
    draw.text((724, 179), "SERVER RANK", font=fonts[15], fill="#aaa0bc")
    draw.text((730, 204), f"#{rank}" if rank else "--", font=fonts[30], fill="#d4c5fa")
    draw.rounded_rectangle((58, 291, 842, 312), radius=10, fill="#292536")
    width = int(784 * state.fraction)
    if width > 1:
        draw.rounded_rectangle(
            (58, 291, 58 + width, 312), radius=min(10, width // 2), fill="#976af5"
        )
    label = (
        f"{state.earned:,} / {state.needed:,} XP to next level" if state.needed else "MAXIMUM LEVEL"
    )
    draw.text((58, 324), f"{xp:,} TOTAL XP", font=fonts[15], fill="#b3a6d1")
    draw.text((480, 324), fit(label, fonts[15], 360), font=fonts[15], fill="#b3a6d1")
    footer = community + (f" · Next reward: {next_reward}" if next_reward else "")
    draw.text((58, 357), fit(footer, fonts[15], 780), font=fonts[15], fill="#9290a2")
    stream = BytesIO()
    image.save(stream, format="PNG", optimize=True)
    return stream.getvalue()
