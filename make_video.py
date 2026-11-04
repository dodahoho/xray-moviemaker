#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.0",
#     "imageio-ffmpeg>=0.5",
# ]
# ///
"""Skapar en slideshow-video av DvXray-datasetet (objektdetektion).

Datasetet förväntas ha två mappar med samma struktur som i detta repo:

    DvXray_Positive_Samples/P00000_OL.png   (vy uppifrån)
    DvXray_Positive_Samples/P00000_SD.png   (vy från sidan)
    DvXray_Positive_Samples/P00000.json
    DvXray_Negative_Samples/N00000_OL.png
    ...

Som standard skapas två videofiler med samma urval och ordning: en med vy
uppifrån (top) och en med vy från sidan (side). Det liknar en live-röntgen med
dual view. Bilderna rullar kontinuerligt som på ett löpande band.

Körs enklast med uv, som automatiskt installerar beroendena:

    uv run make_video.py

Det ger xray_slideshow_top.mp4 och xray_slideshow_side.mp4 med 15 samples som
visas i 5 sekunder vardera. Bakgrunden mellan bilderna är vit som standard.
Använd --view both för att i stället visa båda vyerna sida vid sida i en enda
video, eller --view top/side för en enskild vy.

Använd --help för alla parametrar.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import os
import random
import shutil
import subprocess
import sys
import time
import zlib
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent

VIEW_SUFFIX = {"ol": "_OL.png", "sd": "_SD.png"}
VIEW_LABEL = {"ol": "OL (uppifrån)", "sd": "SD (från sidan)"}
KIND_LABEL = {"positive": "POSITIVE", "negative": "NEGATIVE"}
KIND_COLOR = {"positive": (0, 140, 60), "negative": (190, 40, 40)}

FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


# ---------------------------------------------------------------------------
# Datamodell och urval
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    """Ett sample med en eller flera vyer och dess JSON-annotationer."""

    name: str
    kind: str  # "positive" eller "negative"
    paths: dict[str, Path]
    objects: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Selection:
    samples: list[Sample]
    positive_count: int
    negative_count: int
    seed: int | None


@dataclass(frozen=True)
class RenderJob:
    """En videofil som ska renderas, med tillhörande vy(er)."""

    label: str  # "top", "side" eller "both"
    views: tuple[str, ...]
    output: Path


VIEW_ALIASES = {"ol": "top", "sd": "side"}


def add_view_suffix(output: Path, label: str) -> Path:
    """Lägg till vy i filnamnet, t.ex. video.mp4 -> video_top.mp4."""
    if output.stem.endswith(f"_{label}"):
        return output
    return output.with_name(f"{output.stem}_{label}{output.suffix}")


def plan_render_jobs(args: argparse.Namespace) -> list[RenderJob]:
    """Bestäm vilka videofiler som ska skapas.

    Standard är två separata filer (top och side) med samma samples. Med
    --view both blir det en fil där båda vyerna visas sida vid sida.
    """
    if args.view == "separate":
        specs: tuple[tuple[str, tuple[str, ...]], ...] = (
            ("top", ("ol",)),
            ("side", ("sd",)),
        )
    elif args.view == "both":
        specs = (("both", ("ol", "sd")),)
    elif args.view == "top":
        specs = (("top", ("ol",)),)
    else:  # side
        specs = (("side", ("sd",)),)

    output = args.output.expanduser()
    return [
        RenderJob(label=label, views=views, output=add_view_suffix(output, label))
        for label, views in specs
    ]


def selected_views(view: str) -> list[str]:
    """Interna vynycklar som behövs för urval, statistik och rendering."""
    if view in ("separate", "both"):
        return ["ol", "sd"]
    return ["ol"] if view == "top" else ["sd"]


def discover_samples(
    directory: Path, kind: str, views: Sequence[str], verbose: bool = True
) -> list[Sample]:
    """Hitta alla kompletta samples (alla begärda vyer finns) i en mapp."""
    if not directory.is_dir():
        raise SystemExit(f"Fel: mappen finns inte: {directory}")

    per_name: dict[str, dict[str, Path]] = {}
    for view in views:
        suffix = VIEW_SUFFIX[view]
        for path in directory.glob(f"*{suffix}"):
            name = path.name[: -len(suffix)]
            per_name.setdefault(name, {})[view] = path

    samples: list[Sample] = []
    skipped = 0
    for name in sorted(per_name):
        paths = per_name[name]
        if len(paths) != len(views):
            skipped += 1
            continue

        objects: tuple[dict[str, Any], ...] = ()
        json_path = directory / f"{name}.json"
        if json_path.is_file():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                if verbose:
                    print(
                        f"Varning: kunde inte läsa {json_path}: {exc}", file=sys.stderr
                    )
            else:
                raw_objects = data.get("objects")
                if isinstance(raw_objects, list):
                    objects = tuple(obj for obj in raw_objects if isinstance(obj, dict))

        samples.append(Sample(name=name, kind=kind, paths=dict(paths), objects=objects))

    if not samples:
        suffix_list = ", ".join(VIEW_SUFFIX[v] for v in views)
        raise SystemExit(
            f"Fel: hittade inga kompletta samples i {directory} "
            f"(behöver {suffix_list} med samma namn)."
        )

    if verbose and skipped:
        print(
            f"Varning: hoppade över {skipped} ofullständiga samples i {directory}.",
            file=sys.stderr,
        )
    return samples


def allocate_counts(
    total: int,
    ratio: float,
    available_positive: int,
    available_negative: int,
    allow_repeat: bool,
) -> tuple[int, int]:
    """Fördela antalet bilder mellan positiva och negativa samples."""
    if total <= 0:
        total = available_positive + available_negative

    if not allow_repeat and total > available_positive + available_negative:
        raise SystemExit(
            f"Fel: --num-images {total} är fler än tillgängliga "
            f"{available_positive + available_negative} samples. "
            "Använd ett lägre antal eller --allow-repeat."
        )

    positive = round(total * ratio)
    positive = min(max(positive, 0), total)
    negative = total - positive

    if not allow_repeat:
        if positive > available_positive:
            negative += positive - available_positive
            positive = available_positive
        if negative > available_negative:
            positive += negative - available_negative
            negative = available_negative

    return positive, negative


def draw_pool(
    pool: list[Sample], count: int, rng: random.Random, allow_repeat: bool
) -> list[Sample]:
    if count <= 0:
        return []
    if allow_repeat and count > len(pool):
        # Behåll gärna alla unika samples minst en gång, fyll sedan på.
        chosen = list(pool)
        chosen.extend(rng.choice(pool) for _ in range(count - len(pool)))
        rng.shuffle(chosen)
        return chosen
    return rng.sample(pool, count)


def interleave(positive: list[Sample], negative: list[Sample]) -> list[Sample]:
    """Växla mellan positiva och negativa samples."""
    result: list[Sample] = []
    i = j = 0
    while i < len(positive) or j < len(negative):
        if i < len(positive):
            result.append(positive[i])
            i += 1
        if j < len(negative):
            result.append(negative[j])
            j += 1
    return result


def select_samples(
    positives: list[Sample],
    negatives: list[Sample],
    args: argparse.Namespace,
    rng: random.Random,
    seed: int | None,
) -> Selection:
    positive_count, negative_count = allocate_counts(
        total=args.num_images,
        ratio=args.positive_ratio,
        available_positive=len(positives),
        available_negative=len(negatives),
        allow_repeat=args.allow_repeat,
    )

    if positive_count > 0 and not positives:
        raise SystemExit("Fel: urvalet kräver positiva samples, men inga hittades.")
    if negative_count > 0 and not negatives:
        raise SystemExit("Fel: urvalet kräver negativa samples, men inga hittades.")

    chosen_positive = draw_pool(positives, positive_count, rng, args.allow_repeat)
    chosen_negative = draw_pool(negatives, negative_count, rng, args.allow_repeat)

    if args.order == "random":
        samples = chosen_positive + chosen_negative
        rng.shuffle(samples)
    elif args.order == "alternating":
        samples = interleave(chosen_positive, chosen_negative)
    else:  # sequential
        samples = sorted(chosen_positive + chosen_negative, key=lambda s: s.name)

    return Selection(
        samples=samples,
        positive_count=len(chosen_positive),
        negative_count=len(chosen_negative),
        seed=seed,
    )


# ---------------------------------------------------------------------------
# Bildkomposition
# ---------------------------------------------------------------------------


def load_font(
    size: int, font_path: str | None = None
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if font_path:
        candidates.append(font_path)
    candidates.extend(FONT_CANDIDATES)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def text_size(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont
) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def draw_tag(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
    pad: int = 6,
) -> tuple[int, int]:
    """Rita en text med bakgrund och returnera högerkanten."""
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    x, y = xy
    draw.rectangle(
        [x, y, x + width + 2 * pad, y + height + 2 * pad],
        fill=background,
    )
    draw.text(
        (x + pad - box[0], y + pad - box[1]),
        text,
        font=font,
        fill=foreground,
    )
    return x + width + 2 * pad, y + height + 2 * pad


def label_color(label: str) -> tuple[int, int, int]:
    hue = (zlib.crc32(label.encode("utf-8")) % 360) / 360.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 0.95)
    return int(red * 255), int(green * 255), int(blue * 255)


def contrasting_text(color: tuple[int, int, int]) -> tuple[int, int, int]:
    red, green, blue = color
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    return (0, 0, 0) if luminance >= 140 else (255, 255, 255)


def draw_overlay(
    cell: Image.Image,
    sample: Sample,
    view: str,
    scale: float,
    offset_x: int,
    offset_y: int,
    args: argparse.Namespace,
    class_font: ImageFont.ImageFont,
    object_font: ImageFont.ImageFont,
) -> None:
    """Rita klass, bounding boxes och/eller vynamn på en vy-cell."""
    draw = ImageDraw.Draw(cell)
    pad = 6

    if args.overlay in ("class", "all"):
        text = KIND_LABEL[sample.kind]
        if args.show_name:
            text = f"{text}  {sample.name}"
        draw_tag(
            draw,
            (10, 10),
            text,
            class_font,
            (255, 255, 255),
            KIND_COLOR[sample.kind],
            pad,
        )

    if args.overlay in ("boxes", "all"):
        for obj in sample.objects:
            box = obj.get(f"{view}_bb")
            label = obj.get("label")
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            try:
                x1, y1, x2, y2 = (float(v) for v in box)
            except (TypeError, ValueError):
                continue

            left = offset_x + x1 * scale
            top = offset_y + y1 * scale
            right = offset_x + x2 * scale
            bottom = offset_y + y2 * scale
            left, right = sorted((left, right))
            top, bottom = sorted((top, bottom))

            color = label_color(str(label) if label is not None else "object")
            width = max(2, round(args.font_size / 9))
            draw.rectangle([left, top, right, bottom], outline=color, width=width)

            if label:
                text = str(label)
                text_width, text_height = text_size(draw, text, object_font)
                label_top = top - text_height - 2 * pad
                if label_top < 2:
                    label_top = top + 2
                draw.rectangle(
                    [
                        left,
                        label_top,
                        left + text_width + 2 * pad,
                        label_top + text_height + 2 * pad,
                    ],
                    fill=color,
                )
                draw.text(
                    (left + pad, label_top + pad),
                    text,
                    font=object_font,
                    fill=contrasting_text(color),
                )

    if args.show_view_labels:
        text = VIEW_LABEL[view]
        _, tag_height = text_size(draw, text, object_font)
        y = cell.height - tag_height - 2 * pad - 10
        draw_tag(
            draw,
            (10, y),
            text,
            object_font,
            (255, 255, 255),
            (25, 25, 25),
            pad,
        )


def compose_sample(
    sample: Sample,
    views: Sequence[str],
    args: argparse.Namespace,
    background: tuple[int, int, int],
    class_font: ImageFont.ImageFont,
    object_font: ImageFont.ImageFont,
) -> Image.Image:
    """Skapa en färdig bildruta för ett sample (en eller två vyer)."""
    canvas = Image.new("RGB", (args.width, args.height), background)

    margin = args.margin
    gap = args.gap
    count = len(views)
    available_width = args.width - 2 * margin - gap * (count - 1)
    cell_width = available_width // count
    cell_height = args.height - 2 * margin
    if cell_width < 8 or cell_height < 8:
        raise SystemExit(
            f"Fel: --margin {margin} och --gap {gap} lämnar för lite plats "
            f"i {args.width}x{args.height}."
        )

    for index, view in enumerate(views):
        cell = Image.new("RGB", (cell_width, cell_height), background)
        path = sample.paths[view]
        try:
            with Image.open(path) as image:
                image = image.convert("RGB")
                scale = min(cell_width / image.width, cell_height / image.height)
                new_width = max(1, round(image.width * scale))
                new_height = max(1, round(image.height * scale))
                resized = image.resize((new_width, new_height), Image.LANCZOS)
        except OSError as exc:
            raise SystemExit(f"Fel: kunde inte läsa bilden {path}: {exc}") from exc

        offset_x = (cell_width - new_width) // 2
        offset_y = (cell_height - new_height) // 2
        cell.paste(resized, (offset_x, offset_y))

        draw_overlay(
            cell,
            sample,
            view,
            scale,
            offset_x,
            offset_y,
            args,
            class_font,
            object_font,
        )

        x = margin + index * (cell_width + gap)
        canvas.paste(cell, (x, margin))

    return canvas


class FrameCache:
    """LRU-cache så att bara några få bildrutor finns i minnet samtidigt."""

    def __init__(self, factory: Callable[[int], Image.Image], maxsize: int = 4) -> None:
        self._factory = factory
        self._maxsize = maxsize
        self._cache: OrderedDict[int, Image.Image] = OrderedDict()

    def get(self, index: int) -> Image.Image:
        if index in self._cache:
            self._cache.move_to_end(index)
            return self._cache[index]
        frame = self._factory(index)
        self._cache[index] = frame
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)
        return frame


def ease_progress(progress: float, mode: str) -> float:
    progress = min(max(progress, 0.0), 1.0)
    if mode == "smooth":
        return progress * progress * (3.0 - 2.0 * progress)
    return progress


def paste_clipped(canvas: Image.Image, image: Image.Image, x: int, y: int) -> None:
    """Klistra in en bild även när delar ligger utanför canvasen."""
    canvas_width, canvas_height = canvas.size
    image_width, image_height = image.size

    left = max(0, x)
    top = max(0, y)
    right = min(canvas_width, x + image_width)
    bottom = min(canvas_height, y + image_height)
    if right <= left or bottom <= top:
        return

    cropped = image.crop((left - x, top - y, right - x, bottom - y))
    canvas.paste(cropped, (left, top))


def render_transition(
    previous: Image.Image,
    following: Image.Image,
    progress: float,
    transition: str,
    direction: str,
    background: tuple[int, int, int],
    easing: str,
) -> Image.Image:
    progress = ease_progress(progress, easing)

    if transition == "fade":
        return Image.blend(previous, following, progress)

    width, height = previous.size
    canvas = Image.new("RGB", (width, height), background)

    if direction == "left":
        # Ny bild glider in från vänster; föregående glider ut åt höger.
        previous_x = round(width * progress)
        following_x = round(-width + width * progress)
        paste_clipped(canvas, previous, previous_x, 0)
        paste_clipped(canvas, following, following_x, 0)
    elif direction == "right":
        previous_x = round(-width * progress)
        following_x = round(width - width * progress)
        paste_clipped(canvas, previous, previous_x, 0)
        paste_clipped(canvas, following, following_x, 0)
    elif direction == "up":
        previous_y = round(height * progress)
        following_y = round(-height + height * progress)
        paste_clipped(canvas, previous, 0, previous_y)
        paste_clipped(canvas, following, 0, following_y)
    else:  # down
        previous_y = round(-height * progress)
        following_y = round(height - height * progress)
        paste_clipped(canvas, previous, 0, previous_y)
        paste_clipped(canvas, following, 0, following_y)

    return canvas


# ---------------------------------------------------------------------------
# ffmpeg och videoproduktion
# ---------------------------------------------------------------------------


def resolve_ffmpeg(explicit: str | None, required: bool = True) -> str:
    """Hitta ffmpeg: explicit flagga, FFMPEG-env, PATH eller imageio-ffmpeg."""
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(explicit)
        if found:
            return found
        raise SystemExit(f"Fel: hittade ingen ffmpeg på {explicit!r}.")

    environment = os.environ.get("FFMPEG")
    if environment:
        path = Path(environment).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(environment)
        if found:
            return found

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg
    except ImportError:
        imageio_ffmpeg = None

    if imageio_ffmpeg is not None:
        try:
            bundled = imageio_ffmpeg.get_ffmpeg_exe()
        except (OSError, RuntimeError):
            bundled = None
        if bundled and Path(bundled).is_file():
            return bundled

    if required:
        raise SystemExit(
            "Fel: ffmpeg hittades inte.\n"
            "Kör scriptet med uv så installeras imageio-ffmpeg automatiskt:\n"
            "    uv run make_video.py ...\n"
            "Du kan också ange en egen binary med --ffmpeg C:\\sökväg\\ffmpeg.exe."
        )
    return "ffmpeg"


def resolve_pix_fmt(codec: str, explicit: str | None) -> str:
    """Välj pixelfomat. Standard är 4:4:4, dvs. ingen chroma-subsampling."""
    if explicit:
        return explicit
    if codec == "libx264rgb":
        return "rgb24"
    if codec == "ffv1":
        return "bgr0"
    return "yuv444p"


def is_rgb_pix_fmt(pix_fmt: str) -> bool:
    return pix_fmt.startswith(("rgb", "bgr", "gbr"))


def has_chroma_subsampling(pix_fmt: str) -> bool:
    return "420" in pix_fmt or "422" in pix_fmt


def build_ffmpeg_command(
    ffmpeg: str,
    output: Path,
    width: int,
    height: int,
    fps: int,
    args: argparse.Namespace,
) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        args.codec,
    ]

    if args.codec in ("libx264", "libx265", "libx264rgb"):
        crf = 0 if args.lossless else args.crf
        command.extend(["-preset", args.preset, "-crf", str(crf)])
    elif args.codec != "ffv1":
        command.extend(["-b:v", args.bitrate])

    # Konvertera RGB -> YUV med fullt färgomfång och BT.709 om vi inte redan
    # använder en RGB-pixelfomat. Detta undviker chroma-subsampling.
    if not is_rgb_pix_fmt(args.pix_fmt):
        scale_options = ["in_range=pc", "out_color_matrix=bt709"]
        if args.color_range != "auto":
            scale_options.append(f"out_range={args.color_range}")
        command.extend(["-vf", "scale=" + ":".join(scale_options)])

    command.extend(["-pix_fmt", args.pix_fmt])
    command.extend(
        [
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
        ]
    )
    if is_rgb_pix_fmt(args.pix_fmt):
        # RGB-data är alltid full range, oavsett --color-range.
        command.extend(["-color_range", "pc"])
    elif args.color_range != "auto":
        command.extend(["-color_range", args.color_range])

    if args.format:
        command.extend(["-f", args.format])

    is_mp4 = args.format in ("mp4", "mov") or (
        not args.format and output.suffix.lower() in (".mp4", ".mov", ".m4v")
    )
    if is_mp4:
        command.extend(["-movflags", "+faststart"])
    command.append(str(output))
    return command


def format_command(command: Sequence[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(list(command))
    return " ".join(shlex_quote(part) for part in command)


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def human_duration(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours} h {minutes} min {secs:.1f} s"
    if minutes:
        return f"{int(minutes)} min {secs:.1f} s"
    return f"{secs:.1f} s"


# ---------------------------------------------------------------------------
# Statistik och CLI
# ---------------------------------------------------------------------------


def print_stats(
    positives: list[Sample], negatives: list[Sample], views: Sequence[str]
) -> None:
    print(f"Positiva samples: {len(positives)}")
    print(f"Negativa samples: {len(negatives)}")
    print(f"Totalt:           {len(positives) + len(negatives)}")
    print(f"Vyer:             {', '.join(VIEW_LABEL[v] for v in views)}")

    label_counts: dict[str, int] = {}
    for sample in positives:
        for obj in sample.objects:
            label = obj.get("label")
            if label:
                label_counts[str(label)] = label_counts.get(str(label), 0) + 1
    if label_counts:
        print("Objektetiketter bland positiva:")
        for label, count in sorted(
            label_counts.items(), key=lambda item: (-item[1], item[0])
        ):
            print(f"  {label}: {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Skapa videofiler (mp4) av DvXray positiva och negativa samples. "
            "Som standard skapas två filer med samma urval: en vy uppifrån (top) "
            "och en från sidan (side)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    data = parser.add_argument_group("Dataset")
    data.add_argument(
        "--positive-dir",
        type=Path,
        default=SCRIPT_DIR / "DvXray_Positive_Samples",
        help="Mapp med positiva samples (P*).",
    )
    data.add_argument(
        "--negative-dir",
        type=Path,
        default=SCRIPT_DIR / "DvXray_Negative_Samples",
        help="Mapp med negativa samples (N*).",
    )
    data.add_argument(
        "--view",
        choices=("separate", "top", "side", "both", "ol", "sd"),
        default="separate",
        help=(
            "separate=två filer (top och side) med samma samples, top=endast "
            "uppifrån, side=endast från sidan, both=båda vyerna sida vid sida "
            "i samma video. ol/sd är alias för top/side."
        ),
    )
    data.add_argument(
        "--stats",
        action="store_true",
        help="Visa datasetstatistik och avsluta utan att skapa video.",
    )

    selection = parser.add_argument_group("Urval och ordning")
    selection.add_argument(
        "-n",
        "--num-images",
        type=int,
        default=15,
        help="Antal samples/bildrutor totalt. 0 = alla tillgängliga.",
    )
    selection.add_argument(
        "--positive-ratio",
        type=float,
        default=0.5,
        help="Andel positiva samples (0.0-1.0). Resterande blir negativa.",
    )
    selection.add_argument(
        "--order",
        choices=("random", "alternating", "sequential"),
        default="random",
        help="Ordning på urvalet.",
    )
    selection.add_argument(
        "--seed",
        type=int,
        default=1234,
        help="Slumpfrö för reproducerbart urval. -1 = slumpmässigt frö.",
    )
    selection.add_argument(
        "--allow-repeat",
        action="store_true",
        help="Tillåt att samples upprepas om --num-images är fler än datat.",
    )

    timing = parser.add_argument_group("Tid och övergångar")
    timing.add_argument(
        "-s",
        "--seconds-per-image",
        type=float,
        default=5.0,
        help="Sekunder per bildruta (övergången räknas in i tiden).",
    )
    timing.add_argument(
        "--transition",
        choices=("cut", "slide", "push", "fade"),
        default="slide",
        help=(
            "cut=rakt byte, slide=kontinuerligt löpande band, "
            "push=kort övergång, fade=kort toning."
        ),
    )
    timing.add_argument(
        "--slide-direction",
        choices=("left", "right", "up", "down"),
        default="left",
        help="Riktning som nästa bild rullar in från (slide/push).",
    )
    timing.add_argument(
        "--transition-duration",
        type=float,
        default=0.6,
        help="Övergångens längd i sekunder (endast push/fade).",
    )
    timing.add_argument(
        "--ease",
        choices=("linear", "smooth"),
        default="smooth",
        help="Rörelsehastighet för push/fade. Slide är alltid linjär.",
    )
    timing.add_argument(
        "--conveyor-loop",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Låt löpande bandet fortsätta med första bilden efter sista (endast slide)."
        ),
    )
    timing.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Bildfrekvens i videon.",
    )

    layout = parser.add_argument_group("Layout")
    layout.add_argument(
        "--width", type=int, default=1280, help="Videobredd i pixlar (jämn)."
    )
    layout.add_argument(
        "--height", type=int, default=720, help="Videohöjd i pixlar (jämn)."
    )
    layout.add_argument(
        "--margin", type=int, default=24, help="Marginal runt bildytan i pixlar."
    )
    layout.add_argument(
        "--gap", type=int, default=24, help="Mellanrum mellan OL och SD i pixlar."
    )
    layout.add_argument(
        "--background",
        default="#ffffff",
        help=(
            "Bakgrundsfärg (hex). Standard är vit (#ffffff); använd t.ex. "
            "#101010 för mörk bakgrund."
        ),
    )

    overlay = parser.add_argument_group("Overlay")
    overlay.add_argument(
        "--overlay",
        choices=("none", "class", "boxes", "all"),
        default="none",
        help="none=inga etiketter, class=POSITIVE/NEGATIVE, boxes=objekt+bb, all=båda.",
    )
    overlay.add_argument(
        "--show-name",
        action="store_true",
        help="Visa samplens namn i klasstexten (kräver --overlay class/all).",
    )
    overlay.add_argument(
        "--show-view-labels",
        action="store_true",
        help="Visa 'OL (uppifrån)' och 'SD (från sidan)' i bilden.",
    )
    overlay.add_argument("--font", default=None, help="Sökväg till TTF-font.")
    overlay.add_argument(
        "--font-size",
        type=int,
        default=28,
        help="Teckenstorlek för etiketter.",
    )

    video = parser.add_argument_group("Video och ffmpeg")
    video.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("xray_slideshow.mp4"),
        help=(
            "Basnamn för video. Vyn läggs till automatiskt: "
            "xray_slideshow.mp4 -> xray_slideshow_top.mp4 och "
            "xray_slideshow_side.mp4."
        ),
    )
    video.add_argument(
        "--codec",
        default="libx264",
        help="Videokodek (t.ex. libx264, libx264rgb, libx265, ffv1).",
    )
    video.add_argument(
        "--crf",
        type=int,
        default=18,
        help="Kvalitet för libx264/libx265/libx264rgb (lägre=bättre, 0=förlustfri).",
    )
    video.add_argument("--preset", default="medium", help="x264/x265-preset.")
    video.add_argument(
        "--bitrate", default="8M", help="Bitrate för andra kodekar än x264/x265/ffv1."
    )
    video.add_argument(
        "--lossless",
        action="store_true",
        help="Förlustfri kodning (CRF 0 för x264/x265, FFV1 är alltid förlustfri).",
    )
    video.add_argument(
        "--pix-fmt",
        default=None,
        help=(
            "Pixelfomat. Standard: yuv444p (ingen chroma-subsampling), "
            "rgb24 för libx264rgb och bgr0 för ffv1."
        ),
    )
    video.add_argument(
        "--format",
        default=None,
        help="Tvinga container, t.ex. mp4 eller matroska. Annars styrs det av filändelsen.",
    )
    video.add_argument(
        "--color-range",
        choices=("auto", "pc", "tv"),
        default="pc",
        help="Färgomfång. pc=fullt omfång (bäst för RGB-bilder), tv=begränsat.",
    )
    video.add_argument(
        "--ffmpeg",
        default=None,
        help="Sökväg till ffmpeg (annars PATH/imageio-ffmpeg).",
    )
    video.add_argument(
        "--dry-run",
        action="store_true",
        help="Visa urval och ffmpeg-kommando utan att rendera.",
    )
    video.add_argument(
        "--no-progress", action="store_true", help="Visa ingen progress."
    )

    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    args.view = VIEW_ALIASES.get(args.view, args.view)
    if args.num_images < 0:
        parser.error("--num-images får inte vara negativt.")
    if args.seconds_per_image <= 0:
        parser.error("--seconds-per-image måste vara större än 0.")
    if not 0.0 <= args.positive_ratio <= 1.0:
        parser.error("--positive-ratio måste ligga mellan 0.0 och 1.0.")
    if args.fps <= 0:
        parser.error("--fps måste vara större än 0.")
    if args.width < 64 or args.height < 64:
        parser.error("--width och --height måste vara minst 64.")
    if args.width % 2 or args.height % 2:
        parser.error("--width och --height måste vara jämna tal (t.ex. 1280x720).")
    if args.margin < 0 or args.gap < 0:
        parser.error("--margin och --gap får inte vara negativa.")
    if args.transition_duration < 0:
        parser.error("--transition-duration får inte vara negativ.")
    if args.seconds_per_image * args.fps < 1:
        parser.error(
            "--seconds-per-image ger mindre än en bildruta per bild. "
            "Öka tiden eller sänk --fps."
        )
    if args.font_size < 6:
        parser.error("--font-size måste vara minst 6.")
    if args.view == "both" and args.gap * 1 >= args.width - 2 * args.margin:
        parser.error("--gap är för stort för vald --width/--margin.")

    args.pix_fmt = resolve_pix_fmt(args.codec, args.pix_fmt)

    if args.codec == "libx264rgb" and args.pix_fmt != "rgb24":
        parser.error("--codec libx264rgb kräver --pix-fmt rgb24.")
    if args.lossless and args.codec not in ("libx264", "libx265", "libx264rgb", "ffv1"):
        parser.error(
            "--lossless stöds endast för libx264, libx265, libx264rgb och ffv1."
        )
    if has_chroma_subsampling(args.pix_fmt):
        print(
            f"Varning: --pix-fmt {args.pix_fmt} använder chroma-subsampling. "
            "Det kan ge färgavvikelser jämfört med PNG-bilderna. "
            "Använd yuv444p, rgb24 eller bgr0 för bättre färgnoggrannhet.",
            file=sys.stderr,
        )
    if args.codec == "ffv1" and not args.format:
        suffix = args.output.suffix.lower()
        if suffix not in (".mkv", ".avi", ".nut"):
            parser.error(
                "FFV1 kan inte skrivas till "
                f"{suffix or 'en fil utan filändelse'}. "
                "Använd t.ex. -o video.mkv eller --format matroska."
            )

    if args.transition in ("push", "fade"):
        if args.transition_duration == 0:
            parser.error("--transition-duration måste vara > 0 för push/fade.")
        if args.transition_duration >= args.seconds_per_image:
            print(
                f"Varning: --transition-duration {args.transition_duration} s måste vara "
                f"kortare än --seconds-per-image {args.seconds_per_image} s; "
                "kortar övergången.",
                file=sys.stderr,
            )
            args.transition_duration = args.seconds_per_image * 0.9


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    views = selected_views(args.view)

    try:
        background = ImageColor.getrgb(args.background)[:3]
    except ValueError:
        parser.error(f"ogiltig --background-färg: {args.background}")

    positive_dir = args.positive_dir.expanduser().resolve()
    negative_dir = args.negative_dir.expanduser().resolve()

    print("Läser dataset ...", file=sys.stderr)
    positives = discover_samples(positive_dir, "positive", views)
    negatives = discover_samples(negative_dir, "negative", views)

    if args.stats:
        print_stats(positives, negatives, views)
        return 0

    seed = args.seed if args.seed >= 0 else random.SystemRandom().randint(0, 2**31 - 1)
    rng = random.Random(seed)
    selection = select_samples(positives, negatives, args, rng, seed)

    if not selection.samples:
        raise SystemExit("Fel: urvalet blev tomt.")

    duration = len(selection.samples) * args.seconds_per_image
    total_frames = max(1, round(duration * args.fps))
    transition_duration = (
        args.transition_duration if args.transition in ("push", "fade") else 0.0
    )

    if args.transition == "slide":
        if len(selection.samples) == 1:
            transition_text = "slide (endast en bild, statisk)"
        else:
            transition_text = (
                f"slide (kontinuerligt, riktning={args.slide_direction}, "
                f"loop={'ja' if args.conveyor_loop else 'nej'})"
            )
    elif args.transition == "push":
        transition_text = f"push ({transition_duration:g} s, {args.slide_direction})"
    elif args.transition == "fade":
        transition_text = f"fade ({transition_duration:g} s)"
    else:
        transition_text = "cut"

    jobs = plan_render_jobs(args)
    ffmpeg = resolve_ffmpeg(args.ffmpeg, required=not args.dry_run)

    print(
        f"Urval: {len(selection.samples)} samples "
        f"({selection.positive_count} positiva, {selection.negative_count} negativa), "
        f"seed={selection.seed}",
        file=sys.stderr,
    )
    print(
        f"Video: {args.width}x{args.height} @ {args.fps} fps, "
        f"{human_duration(duration)}, {total_frames} bildrutor, "
        f"övergång={transition_text}",
        file=sys.stderr,
    )
    if len(jobs) > 1:
        print(
            "Skapar två videofiler med samma samples och ordning: "
            + " och ".join(f"{job.output.name} ({job.label})" for job in jobs),
            file=sys.stderr,
        )
    if args.lossless or (
        args.codec in ("libx264", "libx265", "libx264rgb") and args.crf == 0
    ):
        quality = "förlustfri"
    elif args.codec == "ffv1":
        quality = "förlustfri (FFV1)"
    elif args.codec in ("libx264", "libx265", "libx264rgb"):
        quality = f"crf={args.crf}"
    else:
        quality = f"bitrate={args.bitrate}"
    print(
        f"Kodning: {args.codec}/{args.pix_fmt}, {quality}, "
        f"färgomfång={args.color_range}",
        file=sys.stderr,
    )

    if args.dry_run:
        for job in jobs:
            command = build_ffmpeg_command(
                ffmpeg, job.output, args.width, args.height, args.fps, args
            )
            print(f"\nffmpeg-kommando ({job.label}):")
            print(format_command(command))
        print("\nSamples i ordning:")
        for index, sample in enumerate(selection.samples, start=1):
            print(f"  {index:4d}. {sample.name} ({sample.kind})")
        return 0

    class_font = load_font(args.font_size, args.font)
    object_font = load_font(max(10, round(args.font_size * 0.72)), args.font)

    continuous_slide = args.transition == "slide" and len(selection.samples) > 1
    blank_frame = (
        Image.new("RGB", (args.width, args.height), background)
        if continuous_slide and not args.conveyor_loop
        else None
    )

    overall_start = time.perf_counter()

    for job in jobs:
        output = job.output
        if output.parent and not output.parent.exists():
            output.parent.mkdir(parents=True, exist_ok=True)

        def factory(index: int, job_views: tuple[str, ...] = job.views) -> Image.Image:
            return compose_sample(
                selection.samples[index],
                job_views,
                args,
                background,
                class_font,
                object_font,
            )

        frames = FrameCache(factory, maxsize=4)
        command = build_ffmpeg_command(
            ffmpeg, output, args.width, args.height, args.fps, args
        )

        print(f"Renderar {job.label} med ffmpeg: {ffmpeg}", file=sys.stderr)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )

        start_time = time.perf_counter()
        last_percent = -1
        written = 0

        try:
            assert process.stdin is not None
            for frame_index in range(total_frames):
                time_position = frame_index / args.fps
                slot = min(
                    int(time_position / args.seconds_per_image),
                    len(selection.samples) - 1,
                )
                local_time = time_position - slot * args.seconds_per_image

                is_last = slot == len(selection.samples) - 1
                if continuous_slide:
                    # Löpande band: bilden rör sig under hela sin tidslucka.
                    progress = local_time / args.seconds_per_image
                    if slot + 1 < len(selection.samples):
                        following_index: int | None = slot + 1
                    elif args.conveyor_loop:
                        following_index = 0
                    else:
                        following_index = None

                    following = (
                        frames.get(following_index)
                        if following_index is not None
                        else blank_frame
                    )
                    image = render_transition(
                        frames.get(slot),
                        following,
                        progress,
                        "slide",
                        args.slide_direction,
                        background,
                        "linear",
                    )
                elif (
                    transition_duration > 0
                    and not is_last
                    and local_time >= args.seconds_per_image - transition_duration
                ):
                    progress = (
                        local_time - (args.seconds_per_image - transition_duration)
                    ) / transition_duration

                    # Se till att övergången är helt klar på sista bildrutan i
                    # tidsluckan, annars kan nästa bild hoppa det sista stycket.
                    next_time = (frame_index + 1) / args.fps
                    next_slot = min(
                        int(next_time / args.seconds_per_image),
                        len(selection.samples) - 1,
                    )
                    if next_slot != slot:
                        progress = 1.0

                    image = render_transition(
                        frames.get(slot),
                        frames.get(slot + 1),
                        progress,
                        args.transition,
                        args.slide_direction,
                        background,
                        args.ease,
                    )
                else:
                    image = frames.get(slot)

                process.stdin.write(image.tobytes())
                written += 1

                if not args.no_progress:
                    percent = (frame_index + 1) * 100 // total_frames
                    if percent != last_percent and (
                        sys.stderr.isatty() or percent % 10 == 0
                    ):
                        elapsed = time.perf_counter() - start_time
                        rate = (frame_index + 1) / elapsed if elapsed > 0 else 0.0
                        prefix = "\r" if sys.stderr.isatty() else ""
                        print(
                            f"{prefix}[{job.label}] {percent:3d}%  "
                            f"{frame_index + 1}/{total_frames} bildrutor  "
                            f"({rate:.1f} fps)",
                            file=sys.stderr,
                            flush=True,
                        )
                        last_percent = percent

            process.stdin.close()
            return_code = process.wait()
        except BrokenPipeError:
            process.wait()
            return_code = process.returncode or 1
        except KeyboardInterrupt:
            print("\nAvbruten av användaren.", file=sys.stderr)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            if output.is_file():
                output.unlink(missing_ok=True)
            return 130

        if sys.stderr.isatty() and not args.no_progress:
            print(file=sys.stderr)

        if return_code != 0:
            print(
                f"Fel: ffmpeg avslutades med kod {return_code} ({job.label}).",
                file=sys.stderr,
            )
            if output.is_file():
                output.unlink(missing_ok=True)
            return return_code or 1

        elapsed = time.perf_counter() - start_time
        if not output.is_file():
            print(f"Fel: ffmpeg skapade ingen fil: {output}", file=sys.stderr)
            return 1

        size_mb = output.stat().st_size / (1024 * 1024)
        print(
            f"Klart ({job.label}): {output} ({size_mb:.1f} MB, {written} bildrutor, "
            f"{human_duration(duration)}, {elapsed:.1f} s renderningstid).",
            file=sys.stderr,
        )

    if len(jobs) > 1:
        elapsed_total = time.perf_counter() - overall_start
        print(
            f"Klart: {len(jobs)} videofiler, {human_duration(duration)} vardera, "
            f"{elapsed_total:.1f} s totalt.",
            file=sys.stderr,
        )
        for job in jobs:
            print(f"  {job.label}: {job.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
