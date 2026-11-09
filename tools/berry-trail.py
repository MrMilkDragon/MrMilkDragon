"""Render the public contribution calendar as a one-pass PNG snake game."""
import argparse
import struct
import urllib.request
import zlib
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

STEP, WIDTH, HEIGHT = 16, 910, 152
LEFT, TOP = 26, 28
BORDER, PAPER, LAWN, SHADE = b"\x65\x8e\x45", b"\xff\xf7\xd7", b"\xe0\xf3\xb4", b"\xb5\xd7\x80"
BERRIES = [b"", b"\xff\xbe\x50", b"\xff\x96\x4b", b"\xe8\x63\x55", b"\xad\x37\x54"]
LEAF, TAIL, FACE, DARK, WHITE = b"\x36\x88\x56", b"\x5a\xb8\x57", b"\x75\xd2\x5c", b"\x27\x52\x38", b"\xff\xff\xf1"
CHEEK, SPARK = b"\xf2\xa0\x63", b"\xff\xf0\x83"
SIGNATURE = b"\x89PNG\r\n\x1a\n"


class Calendar(HTMLParser):
    def __init__(self):
        super().__init__()
        self.levels = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "data-date" in attrs and "data-level" in attrs:
            self.levels[date.fromisoformat(attrs["data-date"])] = int(attrs["data-level"])


def read_cells(html):
    calendar = Calendar()
    calendar.feed(html)
    days = calendar.levels
    if not 360 <= len(days) <= 370 or any(level not in range(5) for level in days.values()):
        raise ValueError("GitHub did not return a complete public calendar")
    first = min(days)
    sunday = first - timedelta(days=(first.weekday() + 1) % 7)
    if (max(days) - sunday).days // 7 > 53:
        raise ValueError("Unexpected calendar width")
    return {divmod((day - sunday).days, 7): level for day, level in days.items()}


def rect(canvas, x, y, width, height, color, radius=0):
    for row in range(height):
        trim = max(0, radius - min(row, height - 1 - row))
        offset = 3 * ((y + row) * WIDTH + x + trim)
        length = width - 2 * trim
        canvas[offset:offset + length * 3] = color * length


def center(cell):
    x, y = cell
    return LEFT + x * STEP + 6, TOP + y * STEP + 6


def berry(canvas, cell, level):
    x, y = center(cell)
    rect(canvas, x - 4, y - 3, 9, 8, BERRIES[level], 2)
    rect(canvas, x, y - 5, 3, 2, LEAF)
    rect(canvas, x - 2, y - 2, 2, 2, PAPER)


def board(cells):
    canvas = bytearray(PAPER * (WIDTH * HEIGHT))
    rect(canvas, 5, 5, WIDTH - 10, HEIGHT - 10, BORDER, 10)
    rect(canvas, 8, 7, WIDTH - 16, HEIGHT - 17, SHADE, 8)
    rect(canvas, 12, 11, WIDTH - 24, HEIGHT - 27, LAWN, 7)
    for x in (30, 110, 179, 267, 373, 452, 560, 650, 770, 842):
        y = 17 + x % 3
        rect(canvas, x - 4, y - 1, 9, 3, SHADE)
        rect(canvas, x - 1, y - 3, 3, 7, SHADE)
    for cell in cells:
        x, y = center(cell)
        rect(canvas, x - 6, y - 6, 12, 12, PAPER, 2)
    return canvas


def snake(canvas, body, direction):
    for idx in range(len(body) - 1, 0, -1):
        x, y = center(body[idx])
        nx, ny = center(body[idx - 1])
        rect(canvas, min(x, nx) - 4, min(y, ny) - 4,
             abs(nx - x) + 9, abs(ny - y) + 9, LEAF, 2)
        rect(canvas, x - 3, y - 3, 7, 6, TAIL, 2)
    x, y = center(body[0])
    rect(canvas, x - 7, y - 7, 15, 15, DARK, 4)
    rect(canvas, x - 6, y - 6, 13, 12, FACE, 4)
    dx, dy = direction
    for side in (-1, 1):
        ex, ey = x + 2 * dx - 3 * dy * side, y + 2 * dy + 3 * dx * side
        rect(canvas, ex - 1, ey - 1, 3, 3, WHITE, 1)
        rect(canvas, ex, ey, 1, 1, DARK)
        rect(canvas, x - dx - 4 * dy * side, y - dy + 4 * dx * side, 2, 2, CHEEK)


def picture(base, food, body, direction, munch=False):
    canvas = base.copy()
    for cell, level in food.items():
        berry(canvas, cell, level)
    snake(canvas, body, direction)
    if munch:
        x, y = center(body[0])
        rect(canvas, x + 7, y - 6, 3, 3, SPARK, 1)
        rect(canvas, x - 8, y + 4, 2, 2, WHITE)
    return canvas


def chunk(kind, payload):
    crc = zlib.crc32(kind + payload)
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def region(canvas, x=0, y=0, w=WIDTH, h=HEIGHT):
    def row(offset):
        start = (offset * WIDTH + x) * 3
        return b"\0" + canvas[start:start + w * 3]
    return zlib.compress(b"".join(row(r) for r in range(y, y + h)), 6)


def bounds(previous, frame):
    stride = WIDTH * 3
    changed = [y for y in range(HEIGHT)
               if previous[y*stride:(y+1)*stride] != frame[y*stride:(y+1)*stride]]
    if not changed:
        return 0, 0, 1, 1
    left, right = WIDTH - 1, 0
    for y in changed:
        a = previous[y*stride:(y+1)*stride]
        b = frame[y*stride:(y+1)*stride]
        left = min(left, next(x for x in range(WIDTH) if a[x*3:x*3+3] != b[x*3:x*3+3]))
        right = max(right, next(x for x in range(WIDTH-1,-1,-1) if a[x*3:x*3+3] != b[x*3:x*3+3]))
    return left, changed[0], right - left + 1, changed[-1] - changed[0] + 1


def write_images(out, still, frames, frame_count):
    header = SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
    end = chunk(b"IEND", b"")
    (out / "berry-trail.png").write_bytes(header + chunk(b"IDAT", region(still)) + end)
    with (out / "berry-trail-motion.png").open("wb") as stream:
        stream.write(header + chunk(b"acTL", struct.pack(">II", frame_count, 1)))
        previous, seq = None, 0
        for frame, ms in frames:
            x, y, w, h = bounds(previous, frame) if previous is not None else (0, 0, WIDTH, HEIGHT)
            stream.write(chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, w, h, x, y, ms, 1000, 0, 0)))
            seq += 1
            pixels = region(frame, x, y, w, h)
            if previous is None:
                stream.write(chunk(b"IDAT", pixels))
            else:
                stream.write(chunk(b"fdAT", struct.pack(">I", seq) + pixels))
                seq += 1
            previous = frame
        stream.write(end)


def render(cells, out):
    base = board(cells)
    food = {cell: level for cell, level in cells.items() if level}
    first = min((x for x, _ in food), default=10)
    last = max((x for x, _ in food), default=10)
    route = [(x, 0) for x in range(first)]
    for x in range(first, last + 1):
        rows = range(7) if (x - first) % 2 == 0 else range(6, -1, -1)
        route.extend((x, y) for y in rows)
    assert food.keys() <= set(route)
    still = picture(base, food, [route[0]], (1, 0))
    delay = max(10, 3600 // (len(route) + len(food)))
    snack_delay = min(delay, (3900 - len(route) * delay) // max(1, len(food)))
    body = []
    initial_berries = len(food)

    def frames():
        yield still, 400
        for i, cell in enumerate(route):
            prev = route[i-1] if i else (cell[0] - 1, cell[1])
            direction = cell[0] - prev[0], cell[1] - prev[1]
            munch = food.pop(cell, 0)
            length = min(12, 4 + initial_berries - len(food))
            body.insert(0, cell)
            del body[length:]
            yield picture(base, food, body, direction, bool(munch)), delay + (snack_delay if munch else 0)
        yield still, 450

    write_images(out, still, frames(), len(route) + 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("user")
    parser.add_argument("output", type=Path)
    parser.add_argument("--html", type=Path)
    args = parser.parse_args()
    if args.html:
        html = args.html.read_text()
    else:
        request = urllib.request.Request(
            f"https://github.com/users/{args.user}/contributions",
            headers={"User-Agent": "Code-Island-Berry-Trail"},
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            html = response.read().decode("utf-8")
    args.output.mkdir(parents=True, exist_ok=True)
    render(read_cells(html), args.output)


if __name__ == "__main__":
    main()
