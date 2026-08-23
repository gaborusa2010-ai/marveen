#!/usr/bin/env python3
"""Synthetic EXIF fixtures: verify the GPS maths without needing a real photo.

The degrees/minutes/seconds to decimal conversion and the hemisphere sign are
exactly where a parser returns a confident wrong answer. A carpenter sent to
the southern hemisphere by a missing minus sign would notice; a job logged
half a mile away would not be noticed at all.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from exif import read


def _rational(num, den=1):
    return struct.pack("<II", num, den)


def build_jpeg(lat_dms, lat_ref, lon_dms, lon_ref, when=b"2026:08:22 09:14:03"):
    """Minimal little-endian JPEG carrying one GPS IFD and a DateTimeOriginal."""
    gps_entries = [
        (1, 2, len(lat_ref) + 1, lat_ref + b"\x00"),          # GPSLatitudeRef
        (2, 5, 3, b"".join(_rational(*v) for v in lat_dms)),  # GPSLatitude
        (3, 2, len(lon_ref) + 1, lon_ref + b"\x00"),
        (4, 5, 3, b"".join(_rational(*v) for v in lon_dms)),
    ]
    exif_entries = [(0x9003, 2, len(when) + 1, when + b"\x00")]

    def pack_ifd(entries, tiff_base_len, heap_start):
        """Returns (ifd_bytes, heap_bytes). Values over 4 bytes go to the heap."""
        body, heap = b"", b""
        for tag, typ, count, raw in entries:
            if len(raw) <= 4:
                val = raw + b"\x00" * (4 - len(raw))
            else:
                val = struct.pack("<I", heap_start + len(heap))
                heap += raw
            body += struct.pack("<HHI", tag, typ, count) + val
        return struct.pack("<H", len(entries)) + body + struct.pack("<I", 0), heap

    # Layout: header(8) | IFD0 | EXIF IFD | GPS IFD | heap
    ifd0_off = 8
    ifd0_len = 2 + 2 * 12 + 4
    exif_off = ifd0_off + ifd0_len
    exif_len = 2 + len(exif_entries) * 12 + 4
    gps_off = exif_off + exif_len
    gps_len = 2 + len(gps_entries) * 12 + 4
    heap_start = gps_off + gps_len

    exif_ifd, exif_heap = pack_ifd(exif_entries, 8, heap_start)
    gps_ifd, gps_heap = pack_ifd(gps_entries, 8, heap_start + len(exif_heap))

    ifd0 = struct.pack("<H", 2)
    ifd0 += struct.pack("<HHI", 0x8769, 4, 1) + struct.pack("<I", exif_off)
    ifd0 += struct.pack("<HHI", 0x8825, 4, 1) + struct.pack("<I", gps_off)
    ifd0 += struct.pack("<I", 0)

    tiff = b"II" + struct.pack("<HI", 42, ifd0_off) + ifd0 + exif_ifd + gps_ifd \
        + exif_heap + gps_heap
    app1 = b"Exif\x00\x00" + tiff
    return (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xd9")


CASES = [
    # London, Croydon area: 51.3762 N, 0.0982 W. The west sign is the trap.
    ("Croydon", [(51, 1), (22, 1), (3432, 100)], b"N",
     [(0, 1), (5, 1), (5352, 100)], b"W", 51.376200, -0.098200),
    # Southern and eastern hemisphere, to prove both signs.
    ("Sydney", [(33, 1), (51, 1), (3540, 100)], b"S",
     [(151, 1), (12, 1), (2400, 100)], b"E", -33.859833, 151.206667),
]


def main():
    tmp = os.environ.get("TMPDIR", "/tmp")
    failures = 0
    for name, lat, latref, lon, lonref, want_lat, want_lon in CASES:
        path = os.path.join(tmp, f"exif-{name}.jpg")
        with open(path, "wb") as fh:
            fh.write(build_jpeg(lat, latref, lon, lonref))
        r = read(path)
        ok = (r.get("available")
              and abs(r.get("lat", 999) - want_lat) < 1e-5
              and abs(r.get("lon", 999) - want_lon) < 1e-5
              and r.get("datetime") == "2026:08:22 09:14:03")
        print(f"{'PASS' if ok else 'FAIL'}  {name}: "
              f"lat={r.get('lat')} (var {want_lat}), "
              f"lon={r.get('lon')} (var {want_lon}), ido={r.get('datetime')}")
        if not ok:
            failures += 1
            print(f"      teljes kimenet: {r}")
        os.remove(path)

    # A photo with no EXIF at all must report that, not crash or invent.
    plain = os.path.join(tmp, "exif-plain.jpg")
    with open(plain, "wb") as fh:
        fh.write(b"\xff\xd8" + b"\xff\xd9")
    r = read(plain)
    ok = not r.get("available") and "EXIF" in r.get("reason", "")
    print(f"{'PASS' if ok else 'FAIL'}  EXIF nelkuli kep: {r.get('reason','')[:50]}")
    failures += 0 if ok else 1
    os.remove(plain)

    print(f"\n{'MIND JO' if failures == 0 else str(failures) + ' BUKOTT'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
