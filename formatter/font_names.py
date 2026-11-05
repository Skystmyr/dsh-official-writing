"""Print the family names stored in TrueType/OpenType font files."""
import struct
import sys
from pathlib import Path


def family_names(path):
    data = Path(path).read_bytes()
    offsets = [0]
    if data[:4] == b"ttcf":
        count = struct.unpack(">I", data[8:12])[0]
        offsets = list(struct.unpack(">%dI" % count, data[12:12 + 4 * count]))
    names = set()
    for base in offsets:
        num_tables = struct.unpack(">H", data[base + 4:base + 6])[0]
        for i in range(num_tables):
            rec = base + 12 + i * 16
            tag = data[rec:rec + 4]
            if tag != b"name":
                continue
            table = struct.unpack(">I", data[rec + 8:rec + 12])[0]
            count, string_offset = struct.unpack(">HH", data[table + 2:table + 6])
            for j in range(count):
                entry = table + 6 + j * 12
                platform, encoding, language, name_id, length, offset = struct.unpack(">HHHHHH", data[entry:entry + 12])
                if name_id not in (1, 4) or platform != 3:
                    continue
                raw = data[table + string_offset + offset: table + string_offset + offset + length]
                try:
                    names.add((name_id, hex(language), raw.decode("utf-16-be")))
                except UnicodeDecodeError:
                    pass
    return sorted(names)


if __name__ == "__main__":
    for file in sys.argv[1:]:
        print(Path(file).name)
        for name_id, language, name in family_names(file):
            print("   ", "family" if name_id == 1 else "full", language, name)
