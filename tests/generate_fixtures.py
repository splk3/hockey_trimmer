"""
Utility script to generate synthetic test fixtures and video clips for CI.
"""

import os
from PIL import Image, ImageDraw, ImageFont


def create_synthetic_scoreboard_frame(
    output_path: str,
    period: int = 1,
    clock_str: str = "15:00",
    score_str: str = "0-0",
    has_scoreboard: bool = True,
    width: int = 1280,
    height: int = 720,
):
    """Generate a single 1280x720 video frame with or without scoreboard."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    img = Image.new("RGB", (width, height), color=(170, 175, 180))

    if has_scoreboard:
        draw = ImageDraw.Draw(img)
        # Blue tab at top center
        draw.rectangle([140, 10, 230, 35], fill=(0, 102, 255))
        # Away team white box
        draw.rectangle([40, 35, 140, 110], fill=(255, 255, 255))
        # Score black box
        draw.rectangle([140, 35, 230, 110], fill=(15, 15, 15))
        # Home team white box
        draw.rectangle([230, 35, 330, 110], fill=(255, 255, 255))
        # Clock white box
        draw.rectangle([140, 110, 230, 155], fill=(255, 255, 255))

        # Text
        try:
            font = ImageFont.load_default()
            draw.text((180, 15), str(period), fill=(255, 255, 255), font=font)
            draw.text((60, 65), "AWAY", fill=(0, 0, 0), font=font)
            draw.text((170, 65), score_str, fill=(255, 255, 255), font=font)
            draw.text((250, 65), "HOME", fill=(0, 0, 0), font=font)
            draw.text((165, 125), clock_str, fill=(0, 0, 0), font=font)
        except Exception:
            pass

    img.save(output_path)
    return output_path


if __name__ == "__main__":
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    create_synthetic_scoreboard_frame(
        os.path.join(fixtures_dir, "sample_scoreboard.png")
    )
    create_synthetic_scoreboard_frame(
        os.path.join(fixtures_dir, "sample_empty.png"), has_scoreboard=False
    )
    print(f"Generated fixtures in {fixtures_dir}")
