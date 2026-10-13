"""Small, sanitized product photos persisted in PostgreSQL, not ephemeral Railway disk."""
from io import BytesIO

from django.core.exceptions import ValidationError
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_SOURCE_PIXELS = 12_000_000
MAX_OUTPUT_BYTES = 1024 * 1024
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}


def prepare_offer_photo(upload):
    """Validate uploaded image and return a small EXIF-free JPEG for DB storage."""
    if upload.size > MAX_UPLOAD_BYTES:
        raise ValidationError('Photo must be 4 MB or smaller.')
    try:
        raw = upload.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ValidationError('Photo must be 4 MB or smaller.')
        source = Image.open(BytesIO(raw))
        if source.format not in ALLOWED_FORMATS:
            raise ValidationError('Upload a JPEG, PNG or WebP photo.')
        if source.width * source.height > MAX_SOURCE_PIXELS:
            raise ValidationError('Photo resolution is too large; use one under 12 megapixels.')
        source.load()
        image = ImageOps.exif_transpose(source)
        image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        # Flatten transparency to white; never retain original metadata.
        if 'A' in image.getbands():
            rgba = image.convert('RGBA')
            white = Image.new('RGBA', rgba.size, (255, 255, 255, 255))
            image = Image.alpha_composite(white, rgba).convert('RGB')
        else:
            image = image.convert('RGB')
        buffer = BytesIO()
        image.save(buffer, 'JPEG', quality=82, optimize=True)
        data = buffer.getvalue()
        if len(data) > MAX_OUTPUT_BYTES:
            raise ValidationError('This photo could not be optimized. Please choose a smaller image.')
        return data
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, OverflowError) as exc:
        raise ValidationError('The image could not be read. Use a valid JPEG, PNG or WebP file.') from exc
