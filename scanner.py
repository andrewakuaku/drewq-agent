"""
Local card reading module for the DREWQ Reader Agent.

Wraps the pyscard/PC-SC interface and returns a ScanResult
that is sent back to the Heroku backend via WebSocket.

Crypto code mirrors backend/services/card_reader.py exactly.
"""

import base64
import hashlib
import logging
import os
import struct
import threading
from dataclasses import dataclass, asdict
from typing import Callable, Optional

from Crypto.Cipher import DES, DES3

logger = logging.getLogger(__name__)

# ── ICAO constants ─────────────────────────────────────────────────────────────

MRTD_AID = bytes([0xA0, 0x00, 0x00, 0x02, 0x47, 0x10, 0x01])
EF_DG1   = bytes([0x01, 0x01])
EF_DG2   = bytes([0x01, 0x02])
EF_DG11  = bytes([0x01, 0x0B])
SM_CHUNK = 0xC0  # 192 bytes — conservative chunk size under SM overhead


@dataclass
class ScanResult:
    success: bool
    personal_id_number: Optional[str] = None
    card_number: Optional[str] = None
    surname: Optional[str] = None
    given_names: Optional[str] = None
    nationality: Optional[str] = None
    sex: Optional[str] = None
    date_of_birth: Optional[str] = None
    expiry_date: Optional[str] = None
    mrz_line1: Optional[str] = None
    mrz_line2: Optional[str] = None
    photo_data: Optional[str] = None
    atr: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _h(b) -> str:
    return " ".join(f"{x:02X}" for x in b)


def _send(conn, apdu: list[int]) -> tuple[list[int], int, int]:
    data, sw1, sw2 = conn.transmit(apdu)
    logger.debug("→ %s  ← %s SW=%02X%02X", _h(apdu), _h(data), sw1, sw2)
    return data, sw1, sw2


# ── BAC cryptography ──────────────────────────────────────────────────────────

def _check_digit(s: str) -> int:
    weights = [7, 3, 1]
    total = 0
    for i, c in enumerate(s):
        v = int(c) if c.isdigit() else (ord(c.upper()) - 55 if c.isalpha() else 0)
        total += v * weights[i % 3]
    return total % 10


def _odd_parity(b: bytes) -> bytes:
    out = bytearray()
    for byte in b:
        byte &= 0xFE
        if bin(byte).count("1") % 2 == 0:
            byte |= 1
        out.append(byte)
    return bytes(out)


def _kdf(kseed: bytes, c: int) -> bytes:
    digest = hashlib.sha1(kseed + struct.pack(">I", c)).digest()
    return _odd_parity(digest[:8]) + _odd_parity(digest[8:16])


def _compute_kseed(doc_number: str, dob: str, expiry: str) -> bytes:
    dn = doc_number.upper().ljust(9, "<")[:9]
    mrz_key = (
        dn + str(_check_digit(dn))
        + dob + str(_check_digit(dob))
        + expiry + str(_check_digit(expiry))
    ).encode("ascii")
    return hashlib.sha1(mrz_key).digest()[:16]


# ── Padding / unpadding ───────────────────────────────────────────────────────

def _pad(data: bytes) -> bytes:
    """ISO/IEC 7816 method-2 padding."""
    data += b"\x80"
    while len(data) % 8:
        data += b"\x00"
    return data


def _unpad(data: bytes) -> bytes:
    """Remove ISO/IEC 7816 method-2 padding."""
    i = len(data) - 1
    while i >= 0 and data[i] == 0x00:
        i -= 1
    return data[:i] if (i >= 0 and data[i] == 0x80) else data


# ── Retail MAC / 3DES ─────────────────────────────────────────────────────────

def _retail_mac(key: bytes, data: bytes) -> bytes:
    """ISO/IEC 9797-1 MAC Algorithm 3."""
    k1, k2 = key[:8], key[8:16]
    padded = _pad(data)
    blocks = [padded[i:i+8] for i in range(0, len(padded), 8)]
    h = b"\x00" * 8
    for block in blocks[:-1]:
        h = DES.new(k1, DES.MODE_ECB).encrypt(bytes(a ^ b for a, b in zip(h, block)))
    last = bytes(a ^ b for a, b in zip(h, blocks[-1]))
    h = DES.new(k1, DES.MODE_ECB).encrypt(last)
    h = DES.new(k2, DES.MODE_ECB).decrypt(h)
    h = DES.new(k1, DES.MODE_ECB).encrypt(h)
    return h


def _3des_enc(key: bytes, data: bytes, iv: bytes = b"\x00" * 8) -> bytes:
    return DES3.new(key + key[:8], DES3.MODE_CBC, iv).encrypt(data)


def _3des_dec(key: bytes, data: bytes, iv: bytes = b"\x00" * 8) -> bytes:
    return DES3.new(key + key[:8], DES3.MODE_CBC, iv).decrypt(data)


# ── BER-TLV helpers ───────────────────────────────────────────────────────────

def _encode_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x100:
        return bytes([0x81, n])
    return bytes([0x82, n >> 8, n & 0xFF])


def _decode_len(data: bytes, pos: int) -> tuple[int, int]:
    b = data[pos]
    if b < 0x80:
        return b, 1
    if b == 0x81:
        return data[pos + 1], 2
    if b == 0x82:
        return (data[pos + 1] << 8) | data[pos + 2], 3
    return b, 1


def _tlv_total_length(header: bytes) -> int:
    """Return total byte count of the TLV from its first few bytes."""
    pos = 0
    if pos < len(header) and (header[pos] & 0x1F) == 0x1F:
        pos += 1
        while pos < len(header) and (header[pos] & 0x80):
            pos += 1
    pos += 1
    if pos >= len(header):
        return 0
    length, consumed = _decode_len(header, pos)
    return pos + consumed + length


def _tlv_find(data: bytes, target_tag: bytes) -> Optional[bytes]:
    """Walk BER-TLV data and return the value of the first matching tag."""
    pos = 0
    while pos < len(data):
        tag_start = pos
        b = data[pos]
        tag = bytes([b])
        pos += 1
        if (b & 0x1F) == 0x1F:
            while pos < len(data):
                b = data[pos]
                tag += bytes([b])
                pos += 1
                if not (b & 0x80):
                    break
        if pos >= len(data):
            break
        length, consumed = _decode_len(data, pos)
        pos += consumed
        if pos + length > len(data):
            break
        value = data[pos: pos + length]
        if tag == target_tag:
            return value
        if data[tag_start] & 0x20:  # constructed — recurse
            result = _tlv_find(value, target_tag)
            if result is not None:
                return result
        pos += length
    return None


# ── Secure Messaging ──────────────────────────────────────────────────────────

class SecureMessaging:
    """ICAO 9303 Secure Messaging. SSC increments once per command, once per response."""

    def __init__(self, ks_enc: bytes, ks_mac: bytes, ssc: bytes):
        self.ks_enc = ks_enc
        self.ks_mac = ks_mac
        self._ssc = int.from_bytes(ssc, "big")

    def _inc(self) -> bytes:
        self._ssc += 1
        return self._ssc.to_bytes(8, "big")

    def wrap(self, cla: int, ins: int, p1: int, p2: int,
             data: bytes = b"", le: Optional[int] = None) -> list[int]:
        ssc = self._inc()
        mcla = (cla & 0xF0) | 0x0C
        header = bytes([mcla, ins, p1, p2])

        do87 = b""
        if data:
            enc = _3des_enc(self.ks_enc, _pad(data))
            do87 = b"\x87" + _encode_len(len(enc) + 1) + b"\x01" + enc

        do97 = b""
        if le is not None:
            do97 = b"\x97\x01" + bytes([le & 0xFF])

        mac = _retail_mac(self.ks_mac, ssc + _pad(header) + do87 + do97)
        sm_data = do87 + do97 + b"\x8E\x08" + mac
        return [mcla, ins, p1, p2, len(sm_data)] + list(sm_data) + [0x00]

    def unwrap(self, resp: bytes, sw1: int, sw2: int) -> bytes:
        ssc = self._inc()
        pos = 0
        do87_raw = do99_raw = mac = b""

        while pos < len(resp):
            tag = resp[pos]; pos += 1
            length, consumed = _decode_len(resp, pos); pos += consumed
            value = resp[pos: pos + length]; pos += length
            if tag == 0x87:
                do87_raw = bytes([0x87]) + _encode_len(length) + value
            elif tag == 0x99:
                do99_raw = bytes([0x99]) + _encode_len(length) + value
            elif tag == 0x8E:
                mac = value

        if mac:
            expected = _retail_mac(self.ks_mac, ssc + do87_raw + do99_raw)
            if mac != expected:
                logger.warning("SM MAC mismatch — possible SSC desync")

        if do87_raw:
            inner_start = 1 + len(_encode_len(_decode_len(do87_raw, 1)[0])) + 1
            return _unpad(_3des_dec(self.ks_enc, do87_raw[inner_start:]))

        return b""

    def skip_response(self):
        """Increment SSC for a plain 9000 response (no SM data)."""
        self._inc()


# ── BAC ───────────────────────────────────────────────────────────────────────

def _perform_bac(conn, doc_number: str, dob: str, expiry: str) -> SecureMessaging:
    kseed = _compute_kseed(doc_number, dob, expiry)
    kenc  = _kdf(kseed, 1)
    kmac  = _kdf(kseed, 2)

    data, sw1, sw2 = _send(conn, [0x00, 0x84, 0x00, 0x00, 0x08])
    if sw1 != 0x90:
        raise RuntimeError(f"GET CHALLENGE failed: {sw1:02X}{sw2:02X}")
    rnd_ic = bytes(data)

    rnd_ifd = os.urandom(8)
    kifd    = os.urandom(16)
    eifd    = _3des_enc(kenc, rnd_ifd + rnd_ic + kifd)
    mifd    = _retail_mac(kmac, eifd)

    cmd = list(eifd + mifd)
    data, sw1, sw2 = _send(conn, [0x00, 0x82, 0x00, 0x00, len(cmd)] + cmd + [0x28])
    if sw1 != 0x90:
        raise RuntimeError(
            f"BAC failed ({sw1:02X}{sw2:02X}) — "
            "check card number, date of birth, and expiry date"
        )

    resp = bytes(data[:40])
    eic, mic = resp[:32], resp[32:40]
    if _retail_mac(kmac, eic) != mic:
        raise RuntimeError("BAC: card MAC verification failed")

    dec = _3des_dec(kenc, eic)
    if dec[:8] != rnd_ic or dec[8:16] != rnd_ifd:
        raise RuntimeError("BAC: RND mismatch")

    kic    = dec[16:32]
    seed   = bytes(a ^ b for a, b in zip(kifd, kic))
    ks_enc = _kdf(seed, 1)
    ks_mac = _kdf(seed, 2)
    ssc    = rnd_ic[4:] + rnd_ifd[4:]

    logger.info("BAC successful")
    return SecureMessaging(ks_enc, ks_mac, ssc)


# ── SM file I/O ───────────────────────────────────────────────────────────────

def _sm_select(conn, sm: SecureMessaging, file_id: bytes) -> bool:
    apdu = sm.wrap(0x00, 0xA4, 0x02, 0x0C, data=file_id)
    data, sw1, sw2 = _send(conn, apdu)
    # P2=0x0C: card returns plain 9000 (no SM data); still increment SSC for response
    sm.skip_response()
    return sw1 in (0x90, 0x61)


def _sm_read_chunk(conn, sm: SecureMessaging, offset: int, le: int) -> Optional[bytes]:
    apdu = sm.wrap(0x00, 0xB0, offset >> 8, offset & 0xFF, le=le)
    data, sw1, sw2 = _send(conn, apdu)
    if sw1 in (0x90, 0x62):
        return sm.unwrap(bytes(data), sw1, sw2)
    sm.skip_response()
    return None


def _sm_read_file(conn, sm: SecureMessaging, max_bytes: int = 65536) -> Optional[bytes]:
    """Read the currently selected EF in SM-encrypted chunks."""
    header = _sm_read_chunk(conn, sm, 0, 6)
    if not header:
        return None

    total = min(_tlv_total_length(header), max_bytes)
    if total == 0:
        return None

    result = bytearray(header)
    offset = len(header)

    while len(result) < total:
        to_read = min(SM_CHUNK, total - len(result))
        chunk   = _sm_read_chunk(conn, sm, offset, to_read)
        if not chunk:
            break
        result.extend(chunk)
        offset += len(chunk)
        if len(chunk) < to_read:
            break

    return bytes(result)


# ── MRZ parsing ───────────────────────────────────────────────────────────────

def _yymmdd_to_iso(s: str) -> Optional[str]:
    if len(s) != 6 or not s.isdigit():
        return None
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:])
    year = 2000 + yy if yy <= 30 else 1900 + yy
    try:
        from datetime import date
        return date(year, mm, dd).isoformat()
    except ValueError:
        return None


def _extract_mrz(raw: bytes) -> Optional[tuple[str, list[str]]]:
    try:
        text = raw.decode("ascii", errors="replace")
    except Exception:
        return None

    # TD1 (3×30): Identity cards — DREWQ uses this format
    for doc_type in ("I", "A", "C"):
        for country in ("GHA", ""):
            marker = f"{doc_type}<{country}" if country else f"{doc_type}<"
            idx = text.find(marker)
            if idx != -1 and len(text) >= idx + 90:
                return "TD1", [
                    text[idx: idx + 30],
                    text[idx + 30: idx + 60],
                    text[idx + 60: idx + 90],
                ]

    # TD3 (2×44): Passports
    for marker in ("P<GHA", "P<"):
        idx = text.find(marker)
        if idx != -1 and len(text) >= idx + 88:
            return "TD3", [text[idx: idx + 44], text[idx + 44: idx + 88]]

    return None


def _parse_td1(line1: str, line2: str, line3: str) -> dict:
    result: dict = {}
    result["card_number"] = line1[5:14].replace("<", "").strip()

    opt1 = line1[15:30].replace("<", "").strip()
    if len(opt1) >= 10:
        result["personal_id_number"] = f"GHA-{opt1[:9]}-{opt1[9]}"
    elif opt1:
        result["personal_id_number"] = opt1

    if not result.get("personal_id_number"):
        opt2 = line2[18:29].replace("<", "").strip()
        if len(opt2) >= 10:
            result["personal_id_number"] = f"GHA-{opt2[:9]}-{opt2[9]}"
        elif opt2:
            result["personal_id_number"] = opt2

    result["date_of_birth"] = _yymmdd_to_iso(line2[0:6])
    result["sex"]           = line2[7] if len(line2) > 7 and line2[7] in ("M", "F") else "M"
    result["expiry_date"]   = _yymmdd_to_iso(line2[8:14])
    result["nationality"]   = line2[15:18].replace("<", "").strip() or "GHANAIAN"

    parts = line3[:30].split("<<", 1)
    result["surname"]     = parts[0].replace("<", " ").strip()
    result["given_names"] = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""

    return result


def _parse_td3(line1: str, line2: str) -> dict:
    result: dict = {}

    if len(line1) >= 44:
        parts = line1[5:44].split("<<", 1)
        result["surname"]     = parts[0].replace("<", " ").strip()
        result["given_names"] = parts[1].replace("<", " ").strip() if len(parts) > 1 else ""

    if len(line2) >= 44:
        result["card_number"]   = line2[0:9].replace("<", "").strip()
        result["nationality"]   = line2[10:13].replace("<", "").strip() or "GHANAIAN"
        result["date_of_birth"] = _yymmdd_to_iso(line2[13:19])
        result["sex"]           = line2[20] if line2[20] in ("M", "F") else "M"
        result["expiry_date"]   = _yymmdd_to_iso(line2[21:27])
        raw_id = line2[28:42].replace("<", "").strip()
        if len(raw_id) >= 10:
            result["personal_id_number"] = f"GHA-{raw_id[:9]}-{raw_id[9]}"
        elif raw_id:
            result["personal_id_number"] = raw_id

    return result


def _parse_dg2_photo(raw: bytes) -> Optional[str]:
    jpeg_idx = raw.find(b"\xFF\xD8\xFF")
    if jpeg_idx != -1:
        return base64.b64encode(raw[jpeg_idx:]).decode()

    for magic in (b"\x00\x00\x00\x0C\x6A\x50", b"\xFF\x4F"):
        idx = raw.find(magic)
        if idx != -1:
            try:
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(raw[idx:]))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=90)
                return base64.b64encode(buf.getvalue()).decode()
            except Exception as exc:
                logger.warning("JPEG 2000 conversion failed: %s", exc)
                return base64.b64encode(raw[idx:]).decode()

    return None


def _parse_dg11(raw: bytes) -> dict:
    result: dict = {}
    personal_number = _tlv_find(raw, bytes([0x5F, 0x10]))
    if personal_number:
        result["personal_id_number"] = personal_number.decode("utf-8", errors="replace").strip()
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def get_card_atr(reader_name: Optional[str] = None) -> Optional[str]:
    """Connect to the card and return its ATR as an uppercase hex string.
    No BAC required — ATR is returned automatically on connection."""
    try:
        from smartcard.System import readers as get_readers
        available = get_readers()
        if not available:
            return None
        target = available[0]
        if reader_name:
            for r in available:
                if reader_name.lower() in str(r).lower():
                    target = r
                    break
        conn = target.createConnection()
        conn.connect()
        atr = bytes(conn.getATR()).hex().upper()
        conn.disconnect()
        return atr
    except Exception:
        return None


def read_card(doc_number: str, date_of_birth: str, expiry_date: str,
              reader_name: Optional[str] = None,
              skip_photo: bool = False) -> ScanResult:
    """Read a DREWQ ECOWAS card and return a ScanResult."""
    if not all([doc_number, date_of_birth, expiry_date]):
        return ScanResult(success=False, error="doc_number, date_of_birth, expiry_date are all required.")

    try:
        from smartcard.System import readers as get_readers
        available = get_readers()
    except Exception as exc:
        return ScanResult(success=False, error=f"Card reader unavailable: {exc}")

    if not available:
        return ScanResult(success=False, error="No smart card readers found. Check the reader is plugged in.")

    target = available[0]
    if reader_name:
        for r in available:
            if reader_name.lower() in str(r).lower():
                target = r
                break
    logger.info("Using reader: %s", target)

    try:
        conn = target.createConnection()
        conn.connect()
        atr = bytes(conn.getATR()).hex().upper()
        logger.info("Card ATR: %s", atr)
    except Exception as exc:
        return ScanResult(success=False, error=f"No card detected: {exc}")

    try:
        aid = list(MRTD_AID)
        _send(conn, [0x00, 0xA4, 0x04, 0x0C, len(aid)] + aid)

        try:
            sm = _perform_bac(conn, doc_number, date_of_birth, expiry_date)
        except RuntimeError as exc:
            return ScanResult(success=False, error=str(exc))

        chip: dict = {}

        # DG1 — MRZ (required)
        if not _sm_select(conn, sm, EF_DG1):
            return ScanResult(success=False, error="Could not select DG1 (MRZ).")
        raw = _sm_read_file(conn, sm, max_bytes=300)
        if not raw:
            return ScanResult(success=False, error="DG1 returned no data.")
        parsed = _extract_mrz(raw)
        if not parsed:
            return ScanResult(success=False, error="DG1 read but MRZ could not be parsed.")
        fmt, lines = parsed
        chip.update(_parse_td1(*lines) if fmt == "TD1" else _parse_td3(*lines))
        chip["mrz_line1"] = lines[0]
        chip["mrz_line2"] = lines[1]
        logger.info("DG1 parsed as %s", fmt)

        # DG2 — Photo (skip when only identifying, not registering)
        if not skip_photo and _sm_select(conn, sm, EF_DG2):
            raw = _sm_read_file(conn, sm, max_bytes=40000)
            if raw:
                photo = _parse_dg2_photo(raw)
                if photo:
                    chip["photo_data"] = photo

        # DG11 — Additional personal data
        if _sm_select(conn, sm, EF_DG11):
            raw = _sm_read_file(conn, sm, max_bytes=1024)
            if raw:
                chip.update(_parse_dg11(raw))

        if not chip.get("personal_id_number"):
            return ScanResult(success=False, error="Personal ID number not found (DG11 tag 5F10 missing).")

        return ScanResult(success=True, atr=atr, **chip)

    except Exception as exc:
        logger.exception("Unexpected error reading card")
        return ScanResult(success=False, error=str(exc))
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass


def list_readers() -> list[str]:
    try:
        from smartcard.System import readers as get_readers
        return [str(r) for r in get_readers()]
    except Exception:
        return []


class CardPresenceMonitor:
    """
    Event-driven card presence monitor using SCardGetStatusChange(INFINITE).

    Blocks in the PC/SC subsystem until a card is actually inserted or removed,
    then fires the callback immediately. This eliminates polling gaps and false
    negatives — the badge reflects reality within ~100 ms of any state change.

    Usage:
        monitor = CardPresenceMonitor(lambda present: ...)
        monitor.start()
        # ... later ...
        monitor.stop()
    """

    # SCARD_STATE_CHANGED bit — strip before passing back as known state
    _CHANGED = 0x0002

    def __init__(self, callback: Callable[[bool], None]) -> None:
        self._callback = callback
        self._hcontext = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        t = threading.Thread(target=self._run, daemon=True, name="card-monitor")
        t.start()

    def stop(self) -> None:
        self._running = False
        with self._lock:
            if self._hcontext is not None:
                try:
                    from smartcard.scard import SCardCancel
                    SCardCancel(self._hcontext)
                except Exception:
                    pass

    def _run(self) -> None:
        try:
            from smartcard.scard import (
                SCardEstablishContext, SCardReleaseContext, SCardListReaders,
                SCardGetStatusChange, SCARD_SCOPE_USER,
                SCARD_STATE_PRESENT, SCARD_STATE_UNAWARE,
            )
        except ImportError:
            logger.warning("pyscard not available — card monitoring disabled")
            return

        # SCARD_E_TIMEOUT is returned when no state change occurred within the timeout
        try:
            from smartcard.scard import SCARD_E_TIMEOUT
        except ImportError:
            SCARD_E_TIMEOUT = 0x8010000A  # standard PCSC value on all platforms

        # macOS PCSC crashes pyscard's C extension with SCARD_INFINITE (0xFFFFFFFF).
        # Use 5-second chunks instead: event-driven with ≤5s detection latency.
        POLL_MS = 5_000

        hresult, hcontext = SCardEstablishContext(SCARD_SCOPE_USER)
        if hresult != 0:
            logger.warning("SCardEstablishContext failed: %d", hresult)
            return

        with self._lock:
            self._hcontext = hcontext

        try:
            hresult, readers = SCardListReaders(hcontext, [])
            if hresult != 0 or not readers:
                logger.warning("No readers found for card monitoring")
                return

            # Snapshot current state (non-blocking)
            states = [(r, SCARD_STATE_UNAWARE) for r in readers]
            hresult, states = SCardGetStatusChange(hcontext, 0, states)
            if hresult != 0:
                return

            present = any(s & SCARD_STATE_PRESENT for _, s, *_ in states)
            self._callback(present)
            logger.info("Card monitor started — initial state: card_present=%s", present)

            # Strip CHANGED so the next call blocks until a real change occurs
            states = [(r, s & ~self._CHANGED) for r, s, *_ in states]

            while self._running:
                hresult, new_states = SCardGetStatusChange(hcontext, POLL_MS, states)
                if hresult == SCARD_E_TIMEOUT:
                    continue  # no change — loop and wait again
                if hresult != 0:
                    break  # SCardCancel called or fatal error — exit cleanly
                # Only fire callback when the presence bit actually changes
                # (ignores non-presence bits like SCARD_STATE_INUSE / EXCLUSIVE)
                new_present = any(s & SCARD_STATE_PRESENT for _, s, *_ in new_states)
                if new_present != present:
                    present = new_present
                    logger.info("Card state changed: card_present=%s", present)
                    self._callback(present)
                states = [(r, s & ~self._CHANGED) for r, s, *_ in new_states]

        except Exception as exc:
            logger.warning("Card monitor error: %s", exc)
        finally:
            with self._lock:
                self._hcontext = None
            try:
                SCardReleaseContext(hcontext)
            except Exception:
                pass
            logger.info("Card monitor stopped")
