import struct
import sys
import zlib


def read_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h, bit, color = 8, b"", 0, 0, 0, 0
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bit, color = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln
    raw = zlib.decompress(idat)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = w * ch
    out = bytearray()
    prev = bytearray(stride)
    p = 0
    for _y in range(h):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out += line
        prev = line
    return w, h, ch, bytes(out)


def main(path, target=(255, 0, 0), tol=40, mode="rows"):
    if len(sys.argv) > 3:
        tol = int(sys.argv[3])
    w, h, ch, px = read_png(path)
    hits = []
    for y in range(h):
        row = px[y * w * ch:(y + 1) * w * ch]
        for x in range(w):
            r, g, b = row[x * ch], row[x * ch + 1], row[x * ch + 2]
            if abs(r - target[0]) <= tol and abs(g - target[1]) <= tol and abs(b - target[2]) <= tol:
                hits.append((x, y))
    if not hits:
        print("no hits, size", w, h)
        return
    xs = [p[0] for p in hits]
    ys = [p[1] for p in hits]
    print("img %dx%d  red bbox x=%d..%d y=%d..%d  count=%d" % (
        w, h, min(xs), max(xs), min(ys), max(ys), len(hits)))
    rows = {}
    for x, y in hits:
        rows.setdefault(y, 0)
        rows[y] += 1
    dense = sorted([(y, c) for y, c in rows.items() if c > w * 0.4])
    print("dense rows:", dense[:8])


if __name__ == "__main__":
    main(sys.argv[1], tuple(int(v) for v in sys.argv[2].split(",")) if len(sys.argv) > 2 else (255, 0, 0))
