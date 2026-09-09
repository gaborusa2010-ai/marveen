#!/usr/bin/env python3
"""
EXIF reader for job-site photos, dependency free.

A phone photo already knows where and when it was taken. Telegram strips that
when a picture is sent as a *photo* (it recompresses), but keeps the original
bytes when the same picture is sent as a *file*. So one action the carpenter
would take anyway, photographing the job, can carry the location and the
timestamp with it, and nothing has to be typed.

Reads the GPS and DateTime tags straight out of the JPEG APP1 segment.

Usage:  exif.py <photo.jpg> [--json]
"""
import json
import struct
import sys

# Only the tags this job needs. A full EXIF table would be noise.
GPS_TAGS = {1: "lat_ref", 2: "lat", 3: "lon_ref", 4: "lon", 5: "alt_ref", 6: "alt",
            7: "timestamp", 29: "datestamp"}
IFD_TAGS = {0x0110: "model", 0x010F: "make", 0x0132: "datetime",
            0x8825: "gps_ifd", 0x8769: "exif_ifd"}
EXIF_TAGS = {0x9003: "datetime_original", 0x9011: "offset_time_original"}

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}


def _app1(data):
    """Return the TIFF block inside the first EXIF APP1 segment."""
    if data[:2] != b"\xff\xd8":
        return None                      # not a JPEG
    i = 2
    while i < len(data) - 4:
        if data[i] != 0xFF:
            i += 1
            continue
        marker, size = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
            return data[i + 10:i + 2 + size]
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        i += 2 + size
    return None


def _read_ifd(tiff, offset, endian, wanted):
    out = {}
    if offset + 2 > len(tiff):
        return out
    count = struct.unpack(endian + "H", tiff[offset:offset + 2])[0]
    for n in range(count):
        e = offset + 2 + n * 12
        if e + 12 > len(tiff):
            break
        tag, typ, num = struct.unpack(endian + "HHI", tiff[e:e + 8])
        if tag not in wanted:
            continue
        size = TYPE_SIZE.get(typ, 0) * num
        if size == 0:
            continue
        raw = tiff[e + 8:e + 12]
        if size > 4:
            ptr = struct.unpack(endian + "I", raw)[0]
            raw = tiff[ptr:ptr + size]
        if typ == 2:
            out[wanted[tag]] = raw.split(b"\x00")[0].decode("utf-8", "replace")
        elif typ == 5:                                  # rational
            vals = []
            for k in range(num):
                a, b = struct.unpack(endian + "II", raw[k * 8:k * 8 + 8])
                vals.append(a / b if b else 0.0)
            out[wanted[tag]] = vals if num > 1 else vals[0]
        elif typ in (3, 4):
            fmt = "H" if typ == 3 else "I"
            vals = list(struct.unpack(endian + fmt * num, raw[:TYPE_SIZE[typ] * num]))
            out[wanted[tag]] = vals if num > 1 else vals[0]
        elif typ == 1:
            out[wanted[tag]] = raw[:num].decode("latin-1", "replace")
    return out


def _dms(vals, ref):
    if not isinstance(vals, list) or len(vals) != 3:
        return None
    deg = vals[0] + vals[1] / 60.0 + vals[2] / 3600.0
    if ref in ("S", "W"):
        deg = -deg
    return round(deg, 6)


def read(path):
    """Never raises on bad input: this runs on whatever the phone sends."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        return {"available": False, "reason": f"Nem tudom megnyitni a fajlt: {e}"}
    if len(data) < 4:
        return {"available": False, "reason": "A fajl ures vagy csonka."}
    tiff = _app1(data)
    if not tiff:
        return {"available": False, "reason": "Nincs EXIF blokk a fajlban. "
                "Ha Telegramon FOTOKENT kuldted, a Telegram ujratomoritette es "
                "levagta a metaadatot. Kuldd FAJLKENT (csatolas, majd Fajl)."}
    endian = "<" if tiff[:2] == b"II" else ">"
    ifd0 = struct.unpack(endian + "I", tiff[4:8])[0]
    base = _read_ifd(tiff, ifd0, endian, IFD_TAGS)
    out = {"available": True,
           "camera": " ".join(x for x in (base.get("make"), base.get("model")) if x) or None,
           "datetime": base.get("datetime")}
    if "exif_ifd" in base:
        ex = _read_ifd(tiff, base["exif_ifd"], endian, EXIF_TAGS)
        out["datetime"] = ex.get("datetime_original") or out["datetime"]
        out["utc_offset"] = ex.get("offset_time_original")
    if "gps_ifd" in base:
        g = _read_ifd(tiff, base["gps_ifd"], endian, GPS_TAGS)
        lat = _dms(g.get("lat"), g.get("lat_ref"))
        lon = _dms(g.get("lon"), g.get("lon_ref"))
        if lat is not None and lon is not None:
            out["lat"], out["lon"] = lat, lon
            out["maps"] = f"https://maps.google.com/?q={lat},{lon}"
    if "lat" not in out:
        out["gps_note"] = ("Nincs GPS koordinata a kepben. A telefon helymeghatarozasa "
                           "ki volt kapcsolva a kamera appban, vagy fotokent erkezett.")
    return out


def main(argv):
    # `--help` used to fall through to open(argv[1]) and die with a traceback
    # naming "--help" as a missing file. The usage text was always here; only
    # the flag that everyone tries first was not wired to it.
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2 if len(argv) < 2 else 0
    r = read(argv[1])
    if "--json" in argv:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0 if r.get("available") else 1
    if not r.get("available"):
        print(r["reason"])
        return 1
    print(f"Keszult:  {r.get('datetime') or 'ismeretlen'}")
    print(f"Keszulek: {r.get('camera') or 'ismeretlen'}")
    if "lat" in r:
        print(f"Hely:     {r['lat']}, {r['lon']}")
        print(f"Terkep:   {r['maps']}")
    else:
        print(r.get("gps_note", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
