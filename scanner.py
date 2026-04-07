"""
Local card reading module for the DREWQ Reader Agent.

Wraps the pyscard/PC-SC interface and returns a plain dict
that is sent back to the Heroku backend via WebSocket.

This is a self-contained copy of the backend card reading logic
so the agent can be packaged independently.
"""

import base64
import hashlib
import logging
import struct
from dataclasses import dataclass, asdict
from typing import Optional

from Crypto.Cipher import DES, DES3

logger = logging.getLogger(__name__)

# ── ICAO constants ────────────────────────────────────────────────────────────

MRTD_AID = bytes([0xA0, 0x00, 0x00, 0x02, 0x47, 0x10, 0x01])
EF_DG1   = bytes([0x01, 0x01])
EF_DG2   = bytes([0x01, 0x02])
EF_DG11  = bytes([0x01, 0x0B])


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
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ── BAC helpers ───────────────────────────────────────────────────────────────

def _mrz_check_digit(s: str) -> int:
    weights = [7, 3, 1]
    vals = {str(i): i for i in range(10)}
    vals.update({chr(c): c - 55 for c in range(65, 91)})
    vals["<"] = 0
    return sum(vals.get(c, 0) * weights[i % 3] for i, c in enumerate(s)) % 10


def _bac_key(mrz_seed: str, c: int) -> bytes:
    h = hashlib.sha1((mrz_seed + f"{c:08x}").encode()).digest()
    ka, kb = bytearray(h[:8]), bytearray(h[8:16])
    for k in (ka, kb):
        p = sum(bin(b).count("1") for b in k)
        if p % 2 == 0:
            k[7] ^= 1
    return bytes(ka) + bytes(kb)


def _pad(data: bytes) -> bytes:
    data += b"\x80"
    while len(data) % 8:
        data += b"\x00"
    return data


def _unpad(data: bytes) -> bytes:
    i = len(data) - 1
    while i >= 0 and data[i] == 0:
        i -= 1
    if data[i] == 0x80:
        return data[:i]
    return data


def _mac(key: bytes, data: bytes) -> bytes:
    k1, k2 = key[:8], key[8:16]
    padded = _pad(data)
    blk = DES.new(k1, DES.MODE_CBC, iv=b"\x00" * 8).encrypt(padded)
    last = DES.new(k1, DES.MODE_ECB).decrypt(blk[-8:])
    last = DES.new(k2, DES.MODE_ECB).encrypt(last)
    return DES.new(k1, DES.MODE_ECB).encrypt(last)


def _perform_bac(conn, doc_number: str, dob: str, expiry: str):
    """Perform Basic Access Control; return SM state dict."""
    seed_str = (
        doc_number[:9].upper().ljust(9, "<")
        + str(_mrz_check_digit(doc_number[:9].upper().ljust(9, "<")))
        + dob + str(_mrz_check_digit(dob))
        + expiry + str(_mrz_check_digit(expiry))
    )
    k_enc = _bac_key(seed_str, 1)
    k_mac = _bac_key(seed_str, 2)

    # GET CHALLENGE
    rnd_icc = bytes(_send(conn, [0x00, 0x84, 0x00, 0x00, 0x08])[:8])

    import os
    rnd_ifd = os.urandom(8)
    k_ifd   = os.urandom(16)
    s = rnd_ifd + rnd_icc + k_ifd
    e_ifd = DES3.new(k_enc, DES3.MODE_CBC, iv=b"\x00" * 8).encrypt(_pad(s))
    m_ifd = _mac(k_mac, _pad(e_ifd))
    cmd_data = list(e_ifd) + list(m_ifd)

    resp = _send(conn, [0x00, 0x82, 0x00, 0x00, len(cmd_data)] + cmd_data + [0x28])
    if len(resp) < 40:
        raise RuntimeError("BAC EXTERNAL AUTHENTICATE failed — wrong BAC credentials?")

    e_icc, m_icc = bytes(resp[:32]), bytes(resp[32:40])
    if _mac(k_mac, _pad(e_icc)) != m_icc:
        raise RuntimeError("BAC MAC verification failed.")

    r = DES3.new(k_enc, DES3.MODE_CBC, iv=b"\x00" * 8).decrypt(e_icc)
    r = _unpad(r)
    k_icc = r[16:32]
    k_seed = bytes(a ^ b for a, b in zip(k_ifd, k_icc))
    ks_enc = _bac_key(k_seed.hex(), 1)
    ks_mac = _bac_key(k_seed.hex(), 2)
    return {"ks_enc": ks_enc, "ks_mac": ks_mac, "ssc": struct.unpack(">Q", rnd_icc[-4:] + rnd_ifd[-4:])[0]}


def _sm_wrap(sm: dict, cla: int, ins: int, p1: int, p2: int,
             data: bytes = b"", le: Optional[int] = None) -> list[int]:
    sm["ssc"] += 1
    ssc_b = struct.pack(">Q", sm["ssc"])

    do87 = b""
    if data:
        padded = _pad(data)
        enc = DES3.new(sm["ks_enc"], DES3.MODE_CBC, iv=ssc_b).encrypt(padded)
        do87 = bytes([0x87, len(enc) + 1, 0x01]) + enc

    do97 = bytes([0x97, 0x01, le]) if le is not None else b""
    hdr = bytes([cla | 0x0C, ins, p1, p2])
    mac_input = _pad(ssc_b + _pad(hdr)) + do87 + do97
    mac = _mac(sm["ks_mac"], mac_input)
    do8e = bytes([0x8E, 0x08]) + mac

    body = do87 + do97 + do8e
    return [cla | 0x0C, ins, p1, p2, len(body)] + list(body) + ([le] if le is not None else [0])


def _sm_unwrap(sm: dict, resp: list[int]) -> bytes:
    sm["ssc"] += 1
    ssc_b = struct.pack(">Q", sm["ssc"])

    raw = bytes(resp[:-2])
    do87 = do99 = do8e = b""
    i = 0
    while i < len(raw):
        tag, length = raw[i], raw[i + 1]
        val = raw[i + 2: i + 2 + length]
        if tag == 0x87:
            do87 = raw[i: i + 2 + length]
            enc = val[1:]
            iv = DES3.new(sm["ks_enc"], DES3.MODE_ECB).encrypt(ssc_b)
            dec = DES3.new(sm["ks_enc"], DES3.MODE_CBC, iv=iv).decrypt(enc)
            do87_plain = _unpad(dec)
        elif tag == 0x99:
            do99 = raw[i: i + 2 + length]
        elif tag == 0x8E:
            do8e = val
        i += 2 + length

    mac_input = _pad(ssc_b) + do87 + do99
    expected = _mac(sm["ks_mac"], mac_input)
    if expected != do8e:
        raise RuntimeError("SM MAC mismatch on response.")

    return do87_plain if do87 else b""


def _send(conn, apdu: list[int]) -> list[int]:
    resp, sw1, sw2 = conn.transmit(apdu)
    if sw1 == 0x61:
        resp2, sw1, sw2 = conn.transmit([0x00, 0xC0, 0x00, 0x00, sw2])
        resp += resp2
    if (sw1, sw2) not in [(0x90, 0x00), (0x61, 0x00)]:
        raise RuntimeError(f"APDU error SW={sw1:02X}{sw2:02X}")
    return resp


def _sm_select(conn, sm: dict, ef: bytes) -> bool:
    try:
        apdu = _sm_wrap(sm, 0x00, 0xA4, 0x02, 0x0C, ef)
        _sm_unwrap(sm, _send(conn, apdu))
        return True
    except Exception:
        return False


def _sm_read_file(conn, sm: dict, max_bytes: int = 32768) -> bytes:
    result = b""
    offset = 0
    while offset < max_bytes:
        chunk = min(0xDF, max_bytes - offset)
        p1, p2 = (offset >> 8) & 0xFF, offset & 0xFF
        try:
            apdu = _sm_wrap(sm, 0x00, 0xB0, p1, p2, le=chunk)
            data = _sm_unwrap(sm, _send(conn, apdu))
            if not data:
                break
            result += data
            offset += len(data)
            if len(data) < chunk:
                break
        except RuntimeError as exc:
            if "6B00" in str(exc) or "6282" in str(exc):
                break
            raise
    return result


def _extract_mrz(raw: bytes):
    """Find MRZ lines in DG1 TLV data."""
    def find_tag(data, tag):
        i = 0
        while i < len(data) - 1:
            t = data[i]
            if data[i] == 0x5F and i + 1 < len(data):
                t = (data[i] << 8) | data[i + 1]
                i += 1
            i += 1
            if i >= len(data): break
            ln = data[i]; i += 1
            if ln == 0x81: ln = data[i]; i += 1
            elif ln == 0x82: ln = (data[i] << 8) | data[i + 1]; i += 2
            if t == tag:
                return data[i: i + ln]
            i += ln
        return None

    mrz_raw = find_tag(raw, 0x5F1F)
    if not mrz_raw:
        return None

    text = mrz_raw.decode("ascii", errors="replace").replace("\n", "")
    if len(text) == 90:
        return "TD1", [text[:30], text[30:60], text[60:90]]
    if len(text) == 88:
        return "TD3", [text[:44], text[44:88]]
    # Fallback: split raw lines
    lines = [l for l in mrz_raw.decode("ascii", errors="replace").split("\n") if l.strip()]
    if len(lines) >= 2:
        if len(lines[0]) == 30:
            return "TD1", lines[:3]
        return "TD3", lines[:2]
    return None


def _parse_td1(l1: str, l2: str, l3: str) -> dict:
    l1 = l1.ljust(30, "<")
    l2 = l2.ljust(30, "<")
    l3 = l3.ljust(30, "<")
    card_number = l1[5:14].rstrip("<")
    personal_id = l1[15:].rstrip("<") or l2[18:29].rstrip("<")
    dob = l2[0:6]
    sex = l2[7]
    expiry = l2[8:14]
    nationality = l2[15:18].rstrip("<")
    name_field = l3.replace("<<", "|", 1)
    parts = name_field.split("|")
    surname = parts[0].replace("<", " ").strip()
    given_names = (parts[1].replace("<", " ").strip() if len(parts) > 1 else "")
    return dict(
        card_number=card_number, personal_id_number=personal_id,
        date_of_birth=_fmt_date(dob), expiry_date=_fmt_date(expiry),
        sex=sex if sex in ("M", "F") else None,
        nationality=nationality, surname=surname, given_names=given_names,
    )


def _parse_td3(l1: str, l2: str) -> dict:
    l1 = l1.ljust(44, "<")
    l2 = l2.ljust(44, "<")
    card_number = l2[0:9].rstrip("<")
    dob = l2[13:19]
    sex = l2[20]
    expiry = l2[21:27]
    nationality = l2[28:31].rstrip("<")
    name_field = l1[5:].replace("<<", "|", 1)
    parts = name_field.split("|")
    surname = parts[0].replace("<", " ").strip()
    given_names = (parts[1].replace("<", " ").strip() if len(parts) > 1 else "")
    return dict(
        card_number=card_number, personal_id_number=l2[28:42].rstrip("<"),
        date_of_birth=_fmt_date(dob), expiry_date=_fmt_date(expiry),
        sex=sex if sex in ("M", "F") else None,
        nationality=nationality, surname=surname, given_names=given_names,
    )


def _fmt_date(yymmdd: str) -> str:
    """Convert YYMMDD → YYYY-MM-DD."""
    yy, mm, dd = yymmdd[:2], yymmdd[2:4], yymmdd[4:6]
    year = int(yy)
    full_year = 2000 + year if year <= 30 else 1900 + year
    return f"{full_year:04d}-{mm}-{dd}"


def _parse_dg2_photo(raw: bytes) -> Optional[str]:
    """Extract JPEG from DG2 biometric data."""
    JPEG_SOI = bytes([0xFF, 0xD8, 0xFF])
    JPEG2K_SOC = bytes([0xFF, 0x4F])
    idx = raw.find(JPEG_SOI)
    if idx == -1:
        idx = raw.find(JPEG2K_SOC)
    if idx == -1:
        return None
    return base64.b64encode(raw[idx:]).decode()


def _parse_dg11(raw: bytes) -> dict:
    """Extract personal ID number from DG11 (tag 5F10)."""
    result = {}
    i = 0
    while i < len(raw) - 2:
        if raw[i] == 0x5F and i + 1 < len(raw) and raw[i + 1] == 0x10:
            ln = raw[i + 2]
            val = raw[i + 3: i + 3 + ln].decode("ascii", errors="replace").rstrip("<").strip()
            result["personal_id_number"] = val
            break
        i += 1
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def read_card(doc_number: str, date_of_birth: str, expiry_date: str,
              reader_name: Optional[str] = None) -> ScanResult:
    """
    Read a DREWQ ECOWAS card and return a ScanResult dict.
    Called by the WebSocket agent and sent back to the backend.
    """
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
    except Exception as exc:
        return ScanResult(success=False, error=f"No card detected: {exc}")

    try:
        _send(conn, [0x00, 0xA4, 0x04, 0x0C, len(MRTD_AID)] + list(MRTD_AID))
        sm = _perform_bac(conn, doc_number, date_of_birth, expiry_date)
        chip: dict = {}

        if not _sm_select(conn, sm, EF_DG1):
            return ScanResult(success=False, error="Could not select DG1 (MRZ).")
        raw = _sm_read_file(conn, sm, max_bytes=300)
        parsed = _extract_mrz(raw)
        if not parsed:
            return ScanResult(success=False, error="MRZ could not be parsed from DG1.")
        fmt, lines = parsed
        chip.update(_parse_td1(*lines) if fmt == "TD1" else _parse_td3(*lines))
        chip["mrz_line1"] = lines[0]
        chip["mrz_line2"] = lines[1]

        if _sm_select(conn, sm, EF_DG2):
            raw = _sm_read_file(conn, sm, max_bytes=40000)
            if raw:
                photo = _parse_dg2_photo(raw)
                if photo:
                    chip["photo_data"] = photo

        if _sm_select(conn, sm, EF_DG11):
            raw = _sm_read_file(conn, sm, max_bytes=1024)
            if raw:
                chip.update(_parse_dg11(raw))

        if not chip.get("personal_id_number"):
            return ScanResult(success=False, error="Personal ID number missing (DG11 tag 5F10 not found).")

        return ScanResult(success=True, **chip)

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
