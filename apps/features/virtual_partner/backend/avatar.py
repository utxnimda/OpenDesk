"""The partner's single local portrait, stored as versioned 96 × 96 RGBA pixels."""
import base64
import binascii
import struct
import zlib

from .records import StoreError


_SIDE = 96
_STRIDE = _SIDE * 4
_BYTE_COUNT = _SIDE * _STRIDE
_VERSION = 1


def initialize_avatar(db):
    # The previous worker checks the exact set of vp2_* tables. Keep this extension separate.
    existing = db.execute(
        "SELECT type FROM sqlite_master WHERE name='persona_avatar' COLLATE NOCASE").fetchone()
    if existing is None:
        db.execute(f"""CREATE TABLE persona_avatar(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            version INTEGER NOT NULL, rgba BLOB
            CHECK(rgba IS NULL OR (typeof(rgba)='blob' AND length(rgba)={_BYTE_COUNT})))""")
        db.execute("INSERT INTO persona_avatar VALUES(1,?,NULL)", (_VERSION,))
        return
    if existing[0] == "table":
        columns = [row[1] for row in db.execute("PRAGMA table_info(persona_avatar)")]
        if columns == ["singleton", "version", "rgba"]:
            # Validate the size in SQLite before allowing the image into process memory.
            valid = db.execute("""SELECT singleton=1 AND version=? AND
                (rgba IS NULL OR (typeof(rgba)='blob' AND length(rgba)=?))
                FROM persona_avatar LIMIT 2""", (_VERSION, _BYTE_COUNT)).fetchall()
            if len(valid) == 1 and valid[0][0] == 1:
                return
    raise StoreError("DATA_SCHEMA", "伙伴头像数据损坏或版本不兼容，原数据已保留。")


def save_avatar(db, encoded):
    pixels = None
    if encoded is not None:
        invalid = StoreError("INVALID_PARAMS", "头像必须是 96 × 96 的 RGBA 像素，请重新选择图片。")
        if not isinstance(encoded, str) or len(encoded) != _BYTE_COUNT * 4 // 3:
            raise invalid
        try:
            pixels = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise invalid from None
        # The required byte count is divisible by three, so its canonical base64 has no padding.
        if len(pixels) != _BYTE_COUNT:
            raise invalid
    db.execute("UPDATE persona_avatar SET rgba=? WHERE singleton=1", (pixels,))


def _png_chunk(kind, data):
    chunk = kind + data
    return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk))


def avatar_data_url(db):
    pixels = db.execute("SELECT rgba FROM persona_avatar WHERE singleton=1").fetchone()[0]
    if pixels is None:
        return None
    header = struct.pack(">IIBBBBB", _SIDE, _SIDE, 8, 6, 0, 0, 0)
    scanlines = b"".join(b"\x00" + pixels[i:i + _STRIDE] for i in range(0, _BYTE_COUNT, _STRIDE))
    png = (b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header) +
           _png_chunk(b"IDAT", zlib.compress(scanlines)) + _png_chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
