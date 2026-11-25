#!/usr/bin/env python3
"""Sound effects and music from a text prompt, through Lemonade.

Sound effects (MOSS-SoundEffect):
  makesound "heavy wooden door creaking open in a stone castle"
  makesound "sword clash, two steel blades, short" --seconds 2 --takes 4

Music (ACE-Step-Music):
  makesound --music "epic orchestral battle theme, drums, brass, 140 bpm" --seconds 60
  makesound --music "calm tavern folk, lute and flute, loopable" --seconds 90
  makesound --music "upbeat pop, female vocals" --lyrics song.txt --seconds 120

Lyrics (file or text) use section tags on their own lines: [verse], [chorus], [bridge].
Without --lyrics, music is instrumental. Output: ~/3d/out/sound/<name>/<name>[-N].wav (+ .ogg)
"""
import argparse, os, re, shutil, subprocess, sys, time

TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS)
import lemon  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", help="the sound, or for --music the style: genre, mood, tempo, instruments, voice")
    ap.add_argument("--music", action="store_true", help="make music (ACE-Step) instead of a sound effect")
    ap.add_argument("--lyrics", help="lyrics to sing: a text file or the text itself (music only)")
    ap.add_argument("--language", help="lyrics language code, e.g. en, fr, ja (default en)")
    ap.add_argument("--seconds", type=float, help="length (default: sound effect 5, music 30)")
    ap.add_argument("--takes", type=int, default=1, help="make several versions to choose from")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--name", help="output name (default: from the prompt)")
    a = ap.parse_args()
    if a.lyrics and not a.music:
        ap.error("--lyrics needs --music")

    lyrics = None
    if a.lyrics:
        lyrics = open(a.lyrics).read() if os.path.isfile(a.lyrics) else a.lyrics.replace("\\n", "\n")
    model = lemon.MUSIC_MODEL if a.music else lemon.SFX_MODEL
    seconds = a.seconds or (30 if a.music else 5)
    name = a.name or re.sub(r"[^a-z0-9]+", "-", a.prompt.lower()).strip("-")[:40]
    out = os.path.join(os.path.dirname(TOOLS), "out", "sound", name)
    os.makedirs(out, exist_ok=True)

    kind = ("song" if lyrics else "music") if a.music else "sound effect"
    print(f"== {kind}: {model}, {seconds:g} s, {a.takes} take(s)")
    for i in range(a.takes):
        t = time.time()
        wav = os.path.join(out, f"{name}-{i + 1}.wav" if a.takes > 1 else f"{name}.wav")
        with open(wav, "wb") as f:
            f.write(lemon.generate_audio(a.prompt, model, duration=seconds, seed=a.seed + i,
                                         lyrics=lyrics, language=a.language))
        ogg = wav[:-4] + ".ogg"   # small, loops cleanly in game engines
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "libvorbis",
                            "-q:a", "6", ogg], check=False)
        print(f"  {wav}  ({time.time() - t:.0f}s)" + (f"\n  {ogg}" if os.path.exists(ogg) else ""))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"\nerror: {e}")
