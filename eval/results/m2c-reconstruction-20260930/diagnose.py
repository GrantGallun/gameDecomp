"""Read-only diagnostic of one exposed sprite's late pointer store."""
import contextlib, io, json
from pathlib import Path
from m2c import main
from m2c.translate import StoreStmt, StructAccess, late_unwrap
from byte_address import ByteAddresses

here=Path(__file__).resolve().parent
folder=here/'portable/drafts/drawMenuSpriteClipped/byte'
records=[]
with ByteAddresses() as adapter:
    previous=StoreStmt.format
    def observe(statement,fmt):
        result=previous(statement,fmt)
        if 'var_t2 << 5' in result:
            source,dest=statement.source,statement.dest
            row={'output':result,'source_class':type(source).__name__,
                 'source_type':source.type.data().kind,'source_pointer':source.type.is_pointer(),
                 'dest_class':type(dest).__name__,'dest_type':dest.type.data().kind}
            if isinstance(dest,StructAccess):
                path,typ,_=late_unwrap(dest.struct_var).type.get_deref_field(dest.offset,target_size=dest.target_size)
                row.update(field_path=path,field_type=typ.data().kind,field_pointer=typ.is_pointer(),
                           field_format=typ.format(fmt))
            records.append(row)
        return result
    StoreStmt.format=observe
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            main.run(main.parse_flags(['--target','mips-ido-c','--no-cache','--valid-syntax',
                '--context',str(folder/'context.c'),str(folder/'input.s')]))
    finally:
        StoreStmt.format=previous
print(json.dumps(records))
