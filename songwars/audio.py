"""
Er filen faktisk mp3 eller wav?

Filendelsen og Content-Type er hvad browseren siger - det kan man skrive
hvad som helst i. Her laeses selve bytes'ene: en wav skal have en RIFF/
WAVE-header med fmt- og data-chunk, en mp3 skal have rigtige MPEG-rammer
lige efter hinanden. Samtidig regnes laengden ud, saa den kan vises.

Kun standardbiblioteket. Kopieret og strammet op fra EBVB, saa de to apps
ikke deler kode - de deployes hver for sig.
"""

from pathlib import Path

_KBPS = {
    (True, 1): (32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (True, 2): (32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (True, 3): (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (False, 1): (32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (False, 2): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (False, 3): (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}

# PCM, IEEE float og WAVE_FORMAT_EXTENSIBLE. Komprimeret lyd i en wav-
# beholder (ADPCM, GSM, mp3-i-wav) kan de fleste programmer ikke aabne.
_WAV_FORMATS = {1, 3, 0xFFFE}

# Hvor mange MPEG-rammer i traek der skal til foer det tro paa at det er
# en mp3. Tilfaeldige bytes kan ramme en header; tre i traek kan de ikke.
_FRAMES_IN_A_ROW = 3


class NotAudio(ValueError):
    """Filen er ikke en mp3 eller wav der kan bruges. Teksten vises til
    brugeren, saa den er paa dansk."""


def _be(b):
    return int.from_bytes(b, "big")


def _skip_id3(fh):
    pos = 0
    for _ in range(8):
        fh.seek(pos)
        head = fh.read(10)
        if len(head) < 10 or head[:3] != b"ID3":
            return pos
        size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        pos += 10 + size + (10 if head[5] & 0x10 else 0)
    return pos


def _mp3_header(b):
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return None
    version = (b[1] >> 3) & 3
    layer = 4 - ((b[1] >> 1) & 3)
    rate_i = (b[2] >> 2) & 3
    kbps_i = (b[2] >> 4) & 15
    if version == 1 or layer == 4 or rate_i == 3 or kbps_i in (0, 15):
        return None
    mpeg1 = version == 3
    kbps = _KBPS[(mpeg1, layer)][kbps_i - 1]
    rate = _RATES[version][rate_i]
    pad = (b[2] >> 1) & 1
    if layer == 1:
        length = (12 * kbps * 1000 // rate + pad) * 4
        spf = 384
    else:
        spf = 1152 if (layer == 2 or mpeg1) else 576
        length = spf // 8 * kbps * 1000 // rate + pad
    return {"mpeg1": mpeg1, "kbps": kbps, "rate": rate, "spf": spf,
            "length": length, "mono": (b[3] >> 6) & 3 == 3}


def _mp3(fh, size):
    start = _skip_id3(fh)
    fh.seek(start)
    buf = fh.read(256 * 1024)
    for i in range(min(len(buf), 64 * 1024) - 4):
        h = _mp3_header(buf[i:i + 4])
        if h is None:
            continue
        pos, ok = i, True
        for _ in range(_FRAMES_IN_A_ROW - 1):
            pos += _mp3_header(buf[pos:pos + 4])["length"]
            if pos + 4 > len(buf) or _mp3_header(buf[pos:pos + 4]) is None:
                ok = False
                break
        if not ok:
            continue

        end = size
        fh.seek(max(0, size - 128))
        if fh.read(3) == b"TAG":
            end -= 128
        have = end - start - i                    # lydbytes der faktisk er her

        side = (17 if h["mono"] else 32) if h["mpeg1"] else (9 if h["mono"] else 17)
        xing = buf[i + 4 + side:i + 4 + side + 16]
        if xing[:4] in (b"Xing", b"Info"):
            flags = _be(xing[4:8])
            frames = _be(xing[8:12]) if flags & 1 else 0
            claimed = _be(xing[12:16]) if flags & 3 == 3 else frames * h["length"]
            # Headeren siger hvor stor filen skulle vaere. En upload der
            # blev afbrudt halvvejs, har en header der lover hele nummeret.
            if claimed and have < claimed * 0.9:
                raise NotAudio("Filen er ufuldstændig - den slutter før lyden "
                               "gør. Eksportér den igen, og prøv igen.")
            if frames:
                return frames * h["spf"] / h["rate"]
        vbri = buf[i + 36:i + 36 + 18]
        if vbri[:4] == b"VBRI":
            if have < _be(vbri[10:14]) * 0.9:
                raise NotAudio("Filen er ufuldstændig - den slutter før lyden "
                               "gør. Eksportér den igen, og prøv igen.")
            return _be(vbri[14:18]) * h["spf"] / h["rate"]

        return have * 8 / (h["kbps"] * 1000)
    return None


def _wav(fh, size):
    fh.seek(0)
    head = fh.read(12)
    if head[:4] not in (b"RIFF", b"RIFX") or head[8:12] != b"WAVE":
        return None
    order = "big" if head[:4] == b"RIFX" else "little"
    pos, byte_rate, fmt_ok = 12, 0, False
    for _ in range(200):
        fh.seek(pos)
        chunk = fh.read(8)
        if len(chunk) < 8:
            return None
        kind, length = chunk[:4], int.from_bytes(chunk[4:8], order)
        if kind == b"fmt ":
            fmt = fh.read(16)
            if len(fmt) < 16:
                return None
            fmt_ok = int.from_bytes(fmt[0:2], order) in _WAV_FORMATS
            byte_rate = int.from_bytes(fmt[8:12], order)
        elif kind == b"data":
            if not fmt_ok or not byte_rate:
                return None
            room = size - pos - 8
            if length == 0 or length > room:
                length = room              # optagere der ikke fik rettet headeren
            return length / byte_rate
        pos += 8 + length + (length & 1)
    return None


def inspect(path):
    """Returnerer (kind, mimetype, seconds) eller kaster NotAudio.

    kind er 'mp3' eller 'wav' og bestemmes af indholdet - ikke af navnet.
    """
    path = Path(path)
    size = path.stat().st_size
    if size < 1024:
        raise NotAudio("Filen er tom eller for lille til at være lyd.")

    with open(path, "rb") as fh:
        head = fh.read(12)
        if head[:4] in (b"RIFF", b"RIFX"):
            seconds = _wav(fh, size)
            if seconds is None:
                raise NotAudio("Filen ligner en wav, men headeren er i stykker "
                               "eller lyden er komprimeret. Eksportér som PCM-wav.")
            kind, mime = "wav", "audio/wav"
        else:
            try:
                seconds = _mp3(fh, size)
            except (IndexError, KeyError, ZeroDivisionError):
                seconds = None
            if seconds is None:
                raise NotAudio("Filen er hverken mp3 eller wav. Eksportér nummeret "
                               "som mp3 eller wav fra dit program.")
            kind, mime = "mp3", "audio/mpeg"

    if not (1 <= seconds <= 60 * 60):
        raise NotAudio("Lyden er kortere end et sekund eller længere end en time.")
    return kind, mime, seconds
