import sys, yaml
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from pathlib import Path
from solver import function_boundary as fb, byte_certificate as cert
from solver.sdk_intake import rom_offset
R = Path("/home/grant/decomp/sbk1"); fn = "updateRaceSplitscreenSelectOption1Frame"
t = (R / "nonmatchings" / fn / "target.o").read_bytes()
img = cert.object_image(t)["sections"]
print({k: (v["size"], len(v["relocations"])) for k, v in img.items()})
print({k: v["relocations"][:3] for k, v in img.items() if k != ".text"})
labels = fb._data_symbols(t); print(labels)
asm = (R / "nonmatchings" / fn / "target.s").read_text()
print(fb.DATA_LABEL.findall(asm)[:5])
[print(k, fb._section_bytes(t, k)[:32].hex()) for k in img if k != ".text"]
mapping = yaml.safe_load((R / "snowboardkids.yaml").read_bytes()); rom = (R / "snowboardkids.z64").read_bytes()
for name, romoff, vram in fb.DATA_LABEL.findall(asm)[:3]:
    print(name, romoff, vram, rom[int(romoff, 16):int(romoff, 16) + 32].hex())
