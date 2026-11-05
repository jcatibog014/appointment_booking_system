"""Check-in photos: checked, made smaller, saved as WebP, with the camera's details removed.

A phone photo is 3-6 MB and carries EXIF data, often with the GPS position of the
person who took it. Re-drawing the picture with Pillow keeps only the pixels:
the size drops to ~200 KB and nothing private goes along.
"""
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 1600
THUMB_SIDE = 360
MAX_UPLOAD = 5 * 1024 * 1024  # bigger than this is refused before Pillow even opens it
ALLOWED = {"JPEG", "PNG", "WEBP"}
Image.MAX_IMAGE_PIXELS = 40_000_000  # a "decompression bomb" (a tiny file that unpacks huge) is refused


class PhotoError(ValueError):
    pass


def process(raw):
    """Upload bytes -> (photo webp, thumbnail webp, width, height). Raises PhotoError."""
    if not raw:
        raise PhotoError("Choose a photo first.")
    if len(raw) > MAX_UPLOAD:
        raise PhotoError("That photo is too big. Photos can be up to 5 MB.")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in ALLOWED:
                raise PhotoError("Use a JPEG, PNG or WebP photo.")
            image = ImageOps.exif_transpose(image)  # turn it the way the phone held it
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise PhotoError("That file isn't a photo we can read.") from error
    full = image.copy()
    full.thumbnail((MAX_SIDE, MAX_SIDE))
    thumb = image.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE))
    return webp(full), webp(thumb, 70), full.width, full.height


def webp(image, quality=80):
    out = BytesIO()
    image.save(out, "WEBP", quality=quality)  # no exif= argument: nothing but pixels is saved
    return out.getvalue()
