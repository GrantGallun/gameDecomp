import json,time
from pathlib import Path
from solver import binary_data
OUT=Path(__file__).resolve().parent
snapshot=json.loads((OUT/'inventory.json').read_text()); pins=snapshot['pins']; root=Path('/home/grant/decomp/sbk1')
assemblies={p:Path(p).read_text() for p in sorted(pins) if '/asm/data/' in p and p.endswith('.s')}
rompath=str(root/'snowboardkids.z64'); rom=Path(rompath).read_bytes()
started=time.monotonic()
catalog=binary_data.build(assemblies,pins,rom=rom,rom_sha256=pins[rompath])
(OUT/'binary-data-catalog.json').write_text(json.dumps(catalog,indent=2))
packets={}
for name in ['Fcutoff','Fdrums','Fendit','initRaceCameraChase','initRaceCameraPositionTransition','getRaceCourseNextSurface']:
 p=str(root/'nonmatchings'/name/'target.s'); text=Path(p).read_text()
 packets[name]=binary_data.packet(catalog,name,text,pins[p])
(OUT/'binary-data-packets.json').write_text(json.dumps(packets,indent=2))
print(json.dumps({'seconds':time.monotonic()-started,'inputs':len(assemblies),'counts':catalog['counts'],'declines':catalog['declines'][:3],'packet_regions':{n:len(p['regions']) for n,p in packets.items()}},indent=2))
