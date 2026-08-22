"""Build the 15-second EAGLE-X cinematic splash video (real footage + real audio).

Sources (Wikimedia Commons, real-world footage — see ATTRIBUTION below):
  S1a forest   : Golden hour rays break through mist on Diana Creek
  S1b perched  : White-tailed eagle perched in a snowy forest (zeearenden film)
  S2  flight   : Same film — takeoff from snow into soaring blue sky
  S3  pitch    : UNC Kenan football stadium aerial (4-3-3 tactical overlay added)
  S4  mountain : Flying the Rocky Mountains — FPV ridge climb
  S5  peak     : The Hunting Golden Eagle (Norway) — snow-field panorama, logo scene

Audio is synthesised in numpy (forest ambience, wing flaps, wind, stadium
crowd, eagle calls, orchestral sting) and embedded as AAC — no external files.

Output:
  frontend/public/videos/splash.mp4          (1920x1080 H.264 + AAC, 15.0s)
  frontend/public/videos/splash-poster.jpg   (fallback still)

ATTRIBUTION (CC-licensed Wikimedia Commons footage):
  - "Golden hour rays break through mist on Diana Creek" — Wikimedia Commons
  - "Zittende en vliegende zeearenden van verschillende leeftijden-4961975" — Wikimedia Commons
  - "UNC chapel hill kenan football stadium aerial" — Wikimedia Commons
  - "Flying the Rocky Mountains — Stunning FPV Drone Footage" — Wikimedia Commons
  - "Daniel Herskedal – The Hunting Golden Eagle" — Wikimedia Commons
"""
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "_splash_src"
TMP = ROOT / "scripts" / "_splash_tmp"
OUT = ROOT / "frontend" / "public" / "videos"

W, H, FPS = 1920, 1080, 24
SR = 44100
DUR = 15.0

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# scene segments: (source, seek, duration, ken-burns)
SEGS = [
    ("forest.webm",       8.0,  2.0, "in"),    # S1a misty forest, golden rays
    ("eagle_fly.webm",   39.4,  2.0, "in"),    # S1b perched eagle, watching
    ("eagle_fly.webm",  200.2,  3.5, None),    # S2  takeoff → soaring sky
    ("stadium.webm",      9.0,  3.5, "in"),    # S3  aerial pitch (tactical overlay)
    ("rockies.webm",     10.0,  3.5, None),    # S4  FPV ridge climb
    ("norway.webm",      49.5,  3.0, "out"),   # S5  summit panorama → logo
]
XFADE = 0.5  # crossfade duration between segments
# chained-xfade offsets: cumulative = sum(d) - k*XFADE ; offsets at 1.5,3,6,9,12
OFFSETS = [1.5, 3.0, 6.0, 9.0, 12.0]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------- video segments
def cut_segments() -> list[Path]:
    outs = []
    for i, (src, ss, dur, kb) in enumerate(SEGS):
        out = TMP / f"seg{i}.mp4"
        if out.exists():  # resume: segments don't change between iterations
            outs.append(out)
            continue
        vf = [
            f"scale={W}:{H}:force_original_aspect_ratio=increase",
            f"crop={W}:{H}",
            "setsar=1",
            f"fps={FPS}",
        ]
        if kb == "in":
            vf.append(f"zoompan=z='min(zoom+0.0009,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}")
        elif kb == "out":
            vf.append(f"zoompan=z='max(zoom-0.0011,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS}")
        # cinematic grade: gentle contrast/saturation + vignette
        vf += [
            "eq=contrast=1.07:saturation=1.14",
            "vignette=PI/4.6",
            "format=yuv420p",
        ]
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(ss), "-i", str(SRC / src), "-t", str(dur),
            "-vf", ",".join(vf),
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-an", str(out),
        ])
        outs.append(out)
    return outs


# ---------------------------------------------------------------- overlays
def formation_png(path: Path) -> None:
    """4-3-3 tactical overlay, broadcast-HUD style, transparent background."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = W // 2, int(H * 0.60)
    pw, ph = int(W * 0.34), int(H * 0.34)  # perspective ellipse half-extents

    def proj(x: float, y: float) -> tuple[int, int]:
        # x,y in [-1,1] pitch space → ellipse projection
        return int(cx + x * pw), int(cy + y * ph)

    line = (88, 166, 255, 90)
    dot = (88, 166, 255, 235)
    glow = (88, 166, 255, 60)
    # pitch outline
    d.ellipse([cx - pw, cy - ph, cx + pw, cy + ph], outline=line, width=3)
    mid = proj(0, 0)
    d.line([proj(0, -1), proj(0, 1)], fill=line, width=3)
    r = int(ph * 0.28)
    d.ellipse([mid[0] - r, mid[1] - r, mid[0] + r, mid[1] + r], outline=line, width=3)
    # 4-3-3: GK, back 4, mid 3, front 3
    positions = [
        (0.0, 0.86),                                            # GK
        (-0.62, 0.52), (-0.21, 0.55), (0.21, 0.55), (0.62, 0.52),  # defence
        (-0.4, 0.05), (0.0, 0.12), (0.4, 0.05),                 # midfield
        (-0.55, -0.48), (0.0, -0.58), (0.55, -0.48),            # forwards
    ]
    for x, y in positions:
        px, py = proj(x, y)
        d.ellipse([px - 26, py - 26, px + 26, py + 26], fill=glow)
        d.ellipse([px - 13, py - 13, px + 13, py + 13], fill=dot)
    img.save(path)


def logo_png(path: Path) -> None:
    """EAGLE-X logo block: wordmark, subtitle, version — transparent bg."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    gold = (245, 197, 66, 255)
    soft = (201, 209, 217, 230)
    muted = (139, 148, 158, 220)

    def center_text(y: int, text: str, font, fill, spacing: int = 0) -> None:
        widths = [d.textlength(ch, font=font) for ch in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = (W - total) / 2
        for ch, wch in zip(text, widths):
            d.text((x, y), ch, font=font, fill=fill)
            x += wch + spacing

    f_logo = ImageFont.truetype(FONT_BOLD, 150)
    f_sub = ImageFont.truetype(FONT_REG, 44)
    f_ver = ImageFont.truetype(FONT_REG, 34)
    # soft dark band behind the whole block so text reads over bright snow
    band = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    db = ImageDraw.Draw(band)
    db.rounded_rectangle([W // 2 - 720, 520, W // 2 + 720, 920], radius=40, fill=(5, 8, 12, 118))
    band = band.filter(ImageFilter.GaussianBlur(28))
    img = Image.alpha_composite(img, band)
    # soft shadow pass for extra legibility
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sh)

    def center_text_shadow(y, text, font, spacing=0):
        widths = [ds.textlength(ch, font=font) for ch in text]
        total = sum(widths) + spacing * (len(text) - 1)
        x = (W - total) / 2
        for ch, wch in zip(text, widths):
            ds.text((x + 4, y + 5), ch, font=font, fill=(0, 0, 0, 200))
            x += wch + spacing

    center_text_shadow(560, "EAGLE-X", f_logo, 18)
    center_text_shadow(760, "TRADING INTELLIGENCE PLATFORM", f_sub, 10)
    sh = sh.filter(ImageFilter.GaussianBlur(7))
    img = Image.alpha_composite(img, sh)
    d = ImageDraw.Draw(img)
    center_text(560, "EAGLE-X", f_logo, gold, 18)
    center_text(760, "TRADING INTELLIGENCE PLATFORM", f_sub, 10)
    center_text(830, "v1.0.0", f_ver, muted, 4)
    img.save(path)


def loading_png(path: Path) -> None:
    """Just the 'Loading…' caption; the animated bar is a drawbox."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT_REG, 30)
    txt = "Loading…"
    tw = d.textlength(txt, font=f)
    d.text(((W - tw) / 2 + 2, H - 172 + 2), txt, font=f, fill=(0, 0, 0, 180))
    d.text(((W - tw) / 2, H - 172), txt, font=f, fill=(201, 209, 217, 235))
    img.save(path)


# ---------------------------------------------------------------- audio synth
def _fft_band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Zero everything outside [lo,hi] Hz (brick-wall, fine for SFX)."""
    X = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    X[(freqs < lo) | (freqs > hi)] = 0.0
    return np.fft.irfft(X, len(x))


def _env(n: int, a: float, d: float, sr: int = SR) -> np.ndarray:
    """Attack/decay envelope (seconds)."""
    e = np.ones(n)
    na, nd = int(a * sr), int(d * sr)
    if na > 0:
        e[:na] = np.linspace(0, 1, na) ** 1.5
    if nd > 0:
        e[-nd:] *= np.linspace(1, 0, nd) ** 1.5
    return e


def _bird(t: np.ndarray, f0: float, f1: float, dur: float) -> np.ndarray:
    """Little songbird chirp: fast FM sweep with tremolo."""
    phase = 2 * np.pi * (f0 * t + (f1 - f0) / dur * t**2 / 2)
    return np.sin(phase) * (0.6 + 0.4 * np.sin(2 * np.pi * 18 * t))


def _eagle_call(dur: float = 0.85, sr: int = SR) -> np.ndarray:
    """Raptor screech: descending sweep, harmonics, vibrato, breath."""
    t = np.linspace(0, dur, int(dur * sr), endpoint=False)
    f = 2500 * np.exp(-t / (dur * 1.15)) + 900          # 2500→~1300 Hz
    vib = 1 + 0.035 * np.sin(2 * np.pi * 26 * t)
    phase = 2 * np.pi * np.cumsum(f * vib) / sr
    y = (np.sin(phase) + 0.45 * np.sin(2 * phase) + 0.2 * np.sin(3 * phase))
    trem = 0.75 + 0.25 * np.sin(2 * np.pi * 30 * t)
    breath = np.random.default_rng(7).standard_normal(len(t))
    breath = _fft_band(breath, 1500, 3800) * 0.18
    y = (y * 0.9 + breath) * trem * _env(len(t), 0.06, dur * 0.45)
    return y


def synthesize_audio(path: Path) -> None:
    rng = np.random.default_rng(42)
    n = int(DUR * SR)
    t = np.arange(n) / SR
    left = np.zeros(n)
    right = np.zeros(n)

    def add(sig: np.ndarray, start: float, gain: float, pan: float = 0.5) -> None:
        i = int(start * SR)
        j = min(n, i + len(sig))
        if j <= i:
            return
        s = sig[: j - i] * gain
        left[i:j] += s * math.cos(pan * math.pi / 2)
        right[i:j] += s * math.sin(pan * math.pi / 2)

    # --- S1 forest (0–3.5s): leaves, stream, birds
    wind = _fft_band(rng.standard_normal(n), 120, 900)
    wind *= 0.35 + 0.2 * np.sin(2 * np.pi * 0.23 * t + 1.0)
    add(wind[: int(3.6 * SR)] * _env(int(3.6 * SR), 0.8, 1.2), 0, 0.30)
    stream = _fft_band(rng.standard_normal(n), 1100, 2800)
    stream *= 0.5 + 0.5 * np.sin(2 * np.pi * 3.1 * t) * np.sin(2 * np.pi * 1.7 * t + 2)
    add(stream[: int(3.4 * SR)] * _env(int(3.4 * SR), 0.6, 1.3), 0, 0.10, 0.7)
    for st, f0, f1, dd in [(0.45, 3900, 4600, 0.16), (1.15, 3300, 2900, 0.2),
                           (1.9, 4100, 3700, 0.14), (2.5, 3600, 4400, 0.18)]:
        tt = np.linspace(0, dd, int(dd * SR), endpoint=False)
        add(_bird(tt, f0, f1, dd) * _env(len(tt), 0.02, dd * 0.6), st, 0.16, rng.random())

    # --- S2 flight (3–6.5s): wing flaps + rising wind + first call
    rush = _fft_band(rng.standard_normal(n), 250, 1400)
    sw = np.clip((t - 2.9) / 3.0, 0, 1) * np.clip((6.8 - t) / 1.2, 0, 1)
    add(rush * sw, 0, 0.5)
    for k in range(7):
        st = 3.0 + k * 0.4
        fl = _fft_band(rng.standard_normal(int(0.22 * SR)), 180, 950) * _env(int(0.22 * SR), 0.015, 0.16)
        add(fl, st, 0.55, 0.35 + 0.3 * (k % 2))
    add(_eagle_call(), 4.6, 0.5, 0.55)

    # --- S3 stadium (6–9.5s): crowd wash + echoing call
    crowd = _fft_band(rng.standard_normal(n), 260, 2900)
    cenv = np.clip((t - 6.0) / 0.9, 0, 1) * np.clip((9.7 - t) / 1.1, 0, 1)
    crowd *= cenv * (0.8 + 0.2 * np.sin(2 * np.pi * 0.4 * t))
    add(crowd, 0, 0.30, 0.5)
    call2 = _eagle_call(0.7)
    add(call2, 7.3, 0.30, 0.4)
    add(call2, 7.3 + 0.28, 0.14, 0.65)   # stadium echo
    add(call2, 7.3 + 0.56, 0.07, 0.5)

    # --- S4 mountain (9–12.5s): howling wind
    rumble = _fft_band(rng.standard_normal(n), 55, 220)
    gust = np.clip((t - 9.0) / 1.0, 0, 1) * np.clip((12.7 - t) / 1.0, 0, 1)
    gust *= 0.7 + 0.3 * np.sin(2 * np.pi * 0.31 * t + 0.7)
    add(rumble * gust, 0, 0.75)
    whistle = _fft_band(rng.standard_normal(n), 700, 1000)
    add(whistle * gust * (0.4 + 0.6 * np.abs(np.sin(2 * np.pi * 0.17 * t))), 0, 0.12, 0.6)

    # --- S5 sting (11.9–15s): timpani + brass chord + cymbal + final call
    hit = int(0.9 * SR)
    th = np.linspace(0, 0.9, hit, endpoint=False)
    timp = np.sin(2 * np.pi * (68 - 18 * th) * th) * np.exp(-th * 5.5)
    timp += _fft_band(rng.standard_normal(hit), 60, 240) * np.exp(-th * 14) * 0.6
    add(timp, 12.0, 0.9)

    chord_dur = 2.9
    cn = int(chord_dur * SR)
    ct = np.linspace(0, chord_dur, cn, endpoint=False)
    brass = np.zeros(cn)
    for f0 in (146.83, 220.0, 293.66, 369.99, 587.33):   # D major stack
        saw = 2 * ((f0 * ct) % 1.0) - 1.0
        saw += 2 * (((f0 * 1.004) * ct) % 1.0) - 1.0     # detuned pair
        brass += saw
    brass = _fft_band(brass, 100, 2600) / 10.0
    swell = np.clip(ct / 0.55, 0, 1) * np.exp(-np.clip(ct - 1.9, 0, None) * 2.2)
    add(brass * swell, 12.0, 0.65, 0.45)
    add(brass * swell, 12.02, 0.65, 0.6)                 # stereo width

    cym = _fft_band(rng.standard_normal(cn), 4200, 12000)
    cym *= np.clip(ct / 0.8, 0, 1) ** 2 * np.exp(-np.clip(ct - 1.1, 0, None) * 3.0)
    add(cym, 12.0, 0.16)

    add(_eagle_call(1.0) * np.linspace(1, 0.1, int(1.0 * SR)), 13.55, 0.4, 0.5)

    # master: fade-in 80ms, fade-out last 600ms, gentle limiter, normalize
    fade_in = int(0.08 * SR)
    fade_out = int(0.6 * SR)
    for ch in (left, right):
        ch[:fade_in] *= np.linspace(0, 1, fade_in)
        ch[-fade_out:] *= np.linspace(1, 0, fade_out)
    mix = np.stack([left, right], axis=1)
    mix = np.tanh(mix * 1.15)
    mix /= np.max(np.abs(mix)) + 1e-9
    pcm = (mix * 0.89 * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- assembly
def build() -> None:
    TMP.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    print("· cutting segments…")
    segs = cut_segments()

    print("· painting overlays…")
    formation = TMP / "formation.png"
    logo = TMP / "logo.png"
    loading = TMP / "loading.png"
    formation_png(formation)
    logo_png(logo)
    loading_png(loading)

    print("· chaining crossfades…")
    # xfade chain over the 6 segments
    fc_parts = []
    for i in range(len(segs)):
        fc_parts.append(f"[{i}:v]")
    # pairwise: [0][1]xfade → x1 ; [x1][2]xfade → x2 …
    chain = []
    prev = "0:v"
    for i in range(1, len(segs)):
        out = f"x{i}"
        chain.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={XFADE}:offset={OFFSETS[i-1]}[{out}]")
        prev = out

    # overlays on the chained stream (each overlay input gets alpha fades so
    # nothing pops in/out):
    #   formation during scene 3 (6–9), logo + loading during scene 5 (12–15)
    pre = (
        "[6:v]format=rgba,fade=t=in:st=6.3:d=0.4:alpha=1,fade=t=out:st=8.5:d=0.4:alpha=1[f1];"
        "[7:v]format=rgba,fade=t=in:st=12.15:d=0.5:alpha=1,fade=t=out:st=14.5:d=0.4:alpha=1[f2];"
        "[8:v]format=rgba,fade=t=in:st=12.3:d=0.4:alpha=1,fade=t=out:st=14.5:d=0.4:alpha=1[f3];"
    )
    ov = (
        f"[{prev}]"
        "[f1]overlay=0:0:enable='between(t,6.3,9.0)':eval=frame[o1];"
        "[o1][f2]overlay=0:0:enable='between(t,12.15,15)':eval=frame[o2];"
        "[o2][f3]overlay=0:0:enable='between(t,12.3,15)':eval=frame[o3];"
        # loading bar track + animated fill (12.2 → 15.0)
        f"[o3]drawbox=x='(iw-420)/2':y=ih-118:w=420:h=7:color=0x30363d@0.9:t=fill:enable='gte(t,12.2)'[o4];"
        f"[o4]drawbox=x='(iw-420)/2':y=ih-118:w='420*min(1,max(0,(t-12.2)/2.6))':h=7:color=0x58a6ff:t=fill:enable='gte(t,12.2)'[o5];"
        # fade from black at head, fade to dashboard bg at tail
        f"[o5]fade=t=in:st=0:d=0.45,fade=t=out:st=14.35:d=0.65:color=0x0d1117,format=yuv420p[vout]"
    )
    filtergraph = ";".join(chain) + ";" + pre + ov

    video_only = TMP / "splash_video.mp4"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *[a for s in segs for a in ("-i", str(s))],
        "-loop", "1", "-t", str(DUR), "-i", str(formation),
        "-loop", "1", "-t", str(DUR), "-i", str(logo),
        "-loop", "1", "-t", str(DUR), "-i", str(loading),
        "-filter_complex", filtergraph,
        "-map", "[vout]", "-t", str(DUR),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-maxrate", "5M", "-bufsize", "10M",
        "-movflags", "+faststart",
        str(video_only),
    ])

    print("· synthesizing audio…")
    wav = TMP / "splash_audio.wav"
    synthesize_audio(wav)

    print("· muxing…")
    final = OUT / "splash.mp4"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_only), "-i", str(wav),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-t", str(DUR),
        "-movflags", "+faststart",
        str(final),
    ])

    print("· poster…")
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", "13.4", "-i", str(final), "-frames:v", "1",
        "-q:v", "3", str(OUT / "splash-poster.jpg"),
    ])

    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(final)],
        capture_output=True, text=True,
    ).stderr
    for line in probe.splitlines():
        if "Duration" in line or "Stream" in line:
            print("  ", line.strip())
    print(f"✓ splash ready → {final}")


if __name__ == "__main__":
    sys.exit(build())
