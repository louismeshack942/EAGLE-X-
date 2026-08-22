"""Generate the 30 EAGLE-X tutorial videos as real, playable MP4 files.

Pipeline per segment: Pillow renders a slide PNG, espeak-ng synthesizes the
narration WAV, then FFmpeg muxes slide+audio into the final MP4 (H.264 +
AAC — the most broadly compatible combination across Android, iOS, Windows,
macOS, and browsers). Also generates a JPEG thumbnail per video.

Output: frontend/public/videos/<n>-<slug>.mp4, thumbnails/<n>-<slug>.jpg
Writes: scripts/videos.json — the manifest consumed by the Video Hub page.
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from videodata import VIDEOS  # noqa: E402

# Narration voice: natural female neural voice at a moderate, readable pace.
VOICE = "en-US-JennyNeural"
RATE = "-5%"

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "frontend" / "public" / "videos"
THUMB_DIR = ROOT / "frontend" / "public" / "videos" / "thumbnails"
TMP_DIR = ROOT / "scripts" / "_tmp_videos"

IN_DIR = ROOT / "frontend" / "public" / "videos" / "thumbnails"
W, H, FPS = 1280, 720, 24
BG = "#0d1117"
CARD = "#161b22"
BORDER = "#30363d"
TEXT = "#c9d1d9"
MUTED = "#8b949e"
ACCENT = "#58a6ff"
GREEN = "#3fb950"

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

CAT_COLOR = {"Beginner": "#3fb950", "Intermediate": "#d29922", "Advanced": "#f85149"}


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=f) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def rounded_panel(draw: ImageDraw.ImageDraw, xy, radius: int, fill: str, outline: str) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=2)


def render_slide(title: str, bullets: list[str], cat: str, footer: str, out: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # header band
    d.rectangle([0, 0, W, 90], fill=CARD)
    d.line([0, 90, W, 90], fill=BORDER, width=2)
    d.text((40, 28), "🦅", font=font(FONT_REG, 40))
    d.text((110, 28), title, font=font(FONT_BOLD, 40), fill=ACCENT)
    # category chip
    chip_w = int(d.textlength(cat, font=font(FONT_BOLD, 22))) + 28
    d.rounded_rectangle([W - chip_w - 30, 28, W - 30, 60], radius=16, fill=CAT_COLOR.get(cat, "#58a6ff"))
    d.text((W - chip_w - 16, 32), cat, font=font(FONT_BOLD, 22), fill="#0d1117")
    # content card
    rounded_panel(d, (60, 140, W - 60, H - 120), 16, CARD, BORDER)
    bf = font(FONT_REG, 40)
    y = 190
    for b in bullets:
        d.ellipse([100, y + 10, 120, y + 30], fill=GREEN)
        for i, line in enumerate(wrap(b, bf, W - 320, d)):
            d.text((150, y), line, font=bf, fill=TEXT)
            y += 54
        y += 34
    # footer
    d.line([60, H - 80, W - 60, H - 80], fill=BORDER, width=2)
    d.text((W // 2 - int(d.textlength(footer, font=font(FONT_REG, 22)) // 2), H - 66),
           footer, font=font(FONT_REG, 22), fill=MUTED)
    img.save(out, "PNG")


def render_thumb(video: dict, out: Path) -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, H], fill=CARD)
    # big number
    num = f"{video['n']:02d}"
    d.text((W // 2 - 130, H // 2 - 150), num, font=font(FONT_BOLD, 180), fill="#1f2a3a")
    # play triangle
    d.polygon([(W // 2 - 40, H // 2 + 10), (W // 2 - 40, H // 2 + 110), (W // 2 + 60, H // 2 + 60)], fill=ACCENT)
    # title
    tf = font(FONT_BOLD, 44)
    lines = wrap(video["title"], tf, W - 200, d)
    y = H - 180 if len(lines) == 1 else H - 230
    for line in lines:
        d.text((70, y), line, font=tf, fill=TEXT)
        y += 52
    # chip
    cat = video["cat"]
    cw = int(d.textlength(cat, font=font(FONT_BOLD, 24))) + 28
    d.rounded_rectangle([70, y + 16, 70 + cw, y + 52], radius=16, fill=CAT_COLOR.get(cat, "#58a6ff"))
    d.text((84, y + 20), cat, font=font(FONT_BOLD, 24), fill="#0d1117")
    img.save(out, "JPEG", quality=88)


def speak(text: str, out: Path) -> None:
    async def _go() -> None:
        import edge_tts
        tts = edge_tts.Communicate(text, voice=VOICE, rate=RATE)
        await tts.save(str(out.with_suffix(".mp3")))
    asyncio.run(_go())
    # Normalize to WAV 44.1k mono for stable downstream muxing.
    mp3 = out.with_suffix(".mp3")
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "44100", "-ac", "1",
                    "-loglevel", "error", str(out)], check=True)


def mux(slide: Path, wav: Path, out: Path) -> float:
    # duration of narration (pad by 0.6s so the last word isn't clipped)
    info = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
        check=True, capture_output=True, text=True)
    dur = float(info.stdout.strip()) + 0.65
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(slide), "-i", str(wav),
         "-t", f"{dur:.2f}", "-r", str(FPS),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
         "-c:a", "aac", "-b:a", "96k",
         "-movflags", "+faststart", "-loglevel", "error", str(out)],
        check=True)
    return dur


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    jobs = sys.argv[1] if len(sys.argv) > 1 else "all"
    to_run = VIDEOS if jobs == "all" else [v for v in VIDEOS if str(v["n"]) in jobs.split(",")]

    manifest = []
    for v in to_run:
        stem = f"{v['n']:02d}-{v['slug']}"
        mp4 = OUT_DIR / f"{stem}.mp4"
        body = TMP_DIR / f"{stem}.body.mp4"
        thumb = THUMB_DIR / f"{stem}.jpg"

        # render slides + TTS per segment
        seg_files = []
        total = 0.0
        for idx, (heading, bullets, narration) in enumerate(v["segments"]):
            st = fmt = f"({idx + 1}/{len(v['segments'])}) EAGLE-X · Tutorial {v['n']} of {len(VIDEOS)} · {v['cat']}"
            slide = TMP_DIR / f"{stem}-{idx}.png"
            wav = TMP_DIR / f"{stem}-{idx}.wav"
            seg = TMP_DIR / f"{stem}-{idx}.mp4"
            render_slide(heading, bullets, v["cat"], fmt, slide)
            speak(narration, wav)
            total += mux(slide, wav, seg)
            seg_files.append(seg)

        # concat segments
        lst = TMP_DIR / f"{stem}.txt"
        lst.write_text("\n".join(f"file '{p.as_posix()}'" for p in seg_files))
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-movflags", "+faststart", "-loglevel", "error", str(body)],
                       check=True)
        body.replace(mp4)
        render_thumb(v, thumb)
        manifest.append({
            "n": v["n"], "slug": v["slug"], "cat": v["cat"], "title": v["title"],
            "src": f"/videos/{stem}.mp4", "thumb": f"/videos/thumbnails/{stem}.jpg",
            "duration": round(total),
        })
        print(f"✔ {stem}.mp4 ({round(total)}s)")

    if jobs == "all":
        merged = manifest
    else:
        path = ROOT / "scripts" / "videos.json"
        merged = json.loads(path.read_text()) if path.exists() else []
        seen = {m["n"]: m for m in merged}
        for m in manifest:
            seen[m["n"]] = m
        merged = [seen[k] for k in sorted(seen)]
    (ROOT / "scripts" / "videos.json").write_text(json.dumps(merged, indent=2))
    print(f"manifest: {len(merged)} videos")


if __name__ == "__main__":
    main()
