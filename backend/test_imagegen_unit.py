import struct
import sys
import zlib

sys.path.insert(0, ".")
from images import schemas, storage, utils

p = f = 0
def T(n, c):
    global p, f
    if c:
        print(f"  PASS  {n}"); p += 1
    else:
        print(f"  FAIL  {n}"); f += 1

print("=== IMAGE API UNIT TESTS ===\n")

# [1] Schema validation
print("[1] Schema validation")
ok, payload, err = schemas.validate_generate_payload({"prompt": "cat"})
T("Valid defaults applied", ok and payload["width"] == 1024 and payload["height"] == 1024
  and payload["model"] == schemas.DEFAULT_MODEL and payload["prompt"] == "cat")

ok, payload, err = schemas.validate_generate_payload({
    "prompt": "cat", "negative_prompt": "blur", "model": "dall-e-3",
    "width": 256, "height": 128})
T("Valid full payload", ok and payload["model"] == "dall-e-3"
  and payload["width"] == 256 and payload["height"] == 128 and payload["negative_prompt"] == "blur")

ok, payload, err = schemas.validate_generate_payload({})
T("Missing prompt rejected", not ok and "prompt" in err)

ok, payload, err = schemas.validate_generate_payload({"prompt": "   "})
T("Blank prompt rejected", not ok and "prompt" in err)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x", "model": "gpt-4"})
T("Unsupported model rejected", not ok and "model" in err)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x", "width": 4})
T("Width below min", not ok and "width" in err)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x", "width": 99999})
T("Width above max", not ok)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x", "height": "tall"})
T("Height non-integer", not ok and "height" in err)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x", "width": True})
T("Bool is not an integer", not ok)

ok, payload, err = schemas.validate_generate_payload([1, 2])
T("Non-dict body rejected", not ok)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x" * (schemas.MAX_PROMPT_LENGTH + 1)})
T("Prompt too long rejected", not ok and "prompt" in err)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x", "negative_prompt": "y" * (schemas.MAX_PROMPT_LENGTH + 1)})
T("Negative prompt too long rejected", not ok and "negative_prompt" in err)

ok, payload, err = schemas.validate_generate_payload({"prompt": "x", "negative_prompt": "   "})
T("Blank negative prompt normalized", ok and payload["negative_prompt"] == "")

# [2] Placeholder PNG
print("\n[2] Placeholder PNG")
png = utils.make_placeholder_png(64, 48, "test prompt")
T("PNG signature", png[:8] == b"\x89PNG\r\n\x1a\n")
w, h = struct.unpack(">II", png[16:24])
T("PNG dimensions encoded", w == 64 and h == 48)

idat = None
i = 8
while i < len(png):
    length = struct.unpack(">I", png[i:i+4])[0]
    tag = png[i+4:i+8]
    data = png[i+8:i+8+length]
    if tag == b"IDAT":
        idat = data
    i += 12 + length
T("PNG has IDAT chunk", idat is not None)

raw = zlib.decompress(idat)
T("PNG raw pixel size matches", len(raw) == h * (1 + w * 3))

T("Different prompts produce different images",
  utils.make_placeholder_png(64, 48, "a") != utils.make_placeholder_png(64, 48, "b"))

# [3] Storage
print("\n[3] Storage")
name1 = storage.make_filename(512, 512)
name2 = storage.make_filename(512, 512)
T("Filenames unique", name1 != name2)
T("Filename ends in .png", name1.endswith(".png"))

for bad in ("../../secret.png", ".", "", "a/b.png", r"..\\secret.png"):
    blocked = False
    try:
        storage.resolve(bad)
    except ValueError:
        blocked = True
    T(f"Path traversal blocked: {bad!r}", blocked)

ok_resolve = False
try:
    storage.resolve("abc123_512x512.png")
    ok_resolve = True
except ValueError:
    pass
T("Normal filename resolves", ok_resolve)

name = storage.make_filename(256, 256)
filepath = storage.save(b"test-bytes", name)
T("Save returns normalized path", filepath == f"storage/images/{name}")
T("File written to disk", (storage.STORAGE_DIR / name).exists())
storage.remove(name)
T("Remove deletes file", not (storage.STORAGE_DIR / name).exists())
storage.remove(name)
T("Remove missing file is a no-op", True)
storage.remove(".")
T("Remove of invalid name is a no-op", True)

print(f"\n=== {p} passed, {f} failed ===")
sys.exit(1 if f else 0)
