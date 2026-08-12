"""Animate the monthly dryness maps: MP4 for the site, GIF for sharing.

    make secura-anim

Writes webapp/public/secura.mp4, webapp/public/secura.jpg (the still that shows
while the video loads) and ~/Desktop/secura.gif.

Two deliberate choices:

  The maps cross-fade, the month label does not. Dissolving "Junho" into "Julho"
  produces ghosted, unreadable text, so the caption cuts at the midpoint of each
  transition the way broadcast graphics do.

  The intermediate frames are a visual dissolve between two measurements, not
  measurements themselves. Only three months were observed, and the caption
  always names the month being shown, so nobody reads this as daily data.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from .dryness_frames import _moldura, layers

WEB_DIR = Path(__file__).resolve().parent.parent / "webapp" / "public"
LARGURA = 1280     # plenty for a web figure; the print stills stay at 2800
FPS = 25
ESPERA = 1.1       # seconds holding on each month
TRANSICAO = 0.7    # seconds dissolving into the next


def _sequencia(months: list[dict]) -> list[tuple[int, int, float]]:
    """(from, to, blend) per frame, looping back to the first month at the end."""
    steps = []
    n = len(months)
    for i in range(n):
        for _ in range(int(ESPERA * FPS)):
            steps.append((i, i, 0.0))
        j = (i + 1) % n
        for k in range(int(TRANSICAO * FPS)):
            steps.append((i, j, (k + 1) / int(TRANSICAO * FPS)))
    return steps


def build(name: str = "Baião", gif_dir: Path | None = None) -> dict:
    gif_dir = Path(gif_dir) if gif_dir else Path.home() / "Desktop"
    months = layers(name, width=LARGURA)
    if len(months) < 2:
        raise SystemExit("são precisos pelo menos dois meses para animar")

    sub = f"Secura da vegetação · {name}"
    steps = _sequencia(months)
    print(f"{len(months)} meses · {len(steps)} frames · {len(steps) / FPS:.1f}s")

    tmp = Path(tempfile.mkdtemp(prefix="secura-"))
    try:
        for n, (i, j, t) in enumerate(steps):
            mapa = (months[i]["imagem"] if t == 0.0
                    else Image.blend(months[i]["imagem"], months[j]["imagem"], t))
            # the label cuts, it does not dissolve
            title = months[j if t >= 0.5 else i]["titulo"]
            _moldura(mapa.copy(), title, sub).save(tmp / f"{n:04d}.png")

        WEB_DIR.mkdir(parents=True, exist_ok=True)
        mp4 = WEB_DIR / "secura.mp4"
        poster = WEB_DIR / "secura.jpg"
        gif = gif_dir / "secura.gif"

        # yuv420p and even dimensions: without both, Safari and iOS refuse to play it
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
            "-i", str(tmp / "%04d.png"),
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
            "-movflags", "+faststart", str(mp4),
        ], check=True)

        _moldura(months[0]["imagem"].copy(), months[0]["titulo"], sub).save(
            poster, "JPEG", quality=80, optimize=True)

        # GIF has 256 colours, so a palette computed from these frames rather
        # than the default web palette is the difference between smooth and banded
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
            "-i", str(tmp / "%04d.png"),
            "-vf", "fps=12,scale=800:-1:flags=lanczos,split[a][b];"
                   "[a]palettegen=max_colors=256[p];[b][p]paletteuse=dither=sierra2_4a",
            "-loop", "0", str(gif),
        ], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for p in (mp4, poster, gif):
        print(f"  {p}  {p.stat().st_size / 1024:.0f} KB")
    return {"mp4": mp4, "poster": poster, "gif": gif}


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if a]
    build(args[0] if args else "Baião", args[1] if len(args) > 1 else None)
