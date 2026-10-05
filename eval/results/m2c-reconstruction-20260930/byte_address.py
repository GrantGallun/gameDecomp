"""Throwaway byte-address fallback, scoped to the installed m2c lifting API.

No persistent monkeypatch, layout fact, prototype mutation or production wiring.
Successful reconstructed field/array accesses retain upstream behavior. Only
unreconstructed pointer-plus-integer machine additions receive byte views.
"""
from dataclasses import dataclass, field
from m2c import evaluate
from m2c.translate import BinaryOp, Expression, StoreStmt, StructAccess, Cast, late_unwrap
from m2c.types import Type

@dataclass(frozen=True, eq=False)
class BytePointerView(Expression):
    expr: Expression
    type: Type = field(default_factory=lambda:Type.ptr(Type.u8()))

    def dependencies(self):
        return [self.expr]

    def format(self, fmt):
        # Cast.use() unifies operand types upstream; a view must not do so.
        return f'({self.type.format(fmt)})({self.expr.format(fmt)})'

class ByteAddresses:
    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.changes = []
        self.store_views = []

    def __enter__(self):
        self.saved = (evaluate.handle_add_real, evaluate.add_imm, StoreStmt.format)
        if self.enabled:
            def add(lhs,rhs,args):
                result = self.saved[0](lhs,rhs,args)
                return self._view(result,args,'register-address-add')
            def imm(output_reg,source,value,args):
                result = self.saved[1](output_reg,source,value,args)
                return self._view(result,args,'immediate-address-add')
            evaluate.handle_add_real, evaluate.add_imm = add,imm
            def store(statement,fmt):
                source,dest = statement.source,statement.dest
                destination_type = dest.type
                if isinstance(dest,StructAccess) and dest.late_field_path() is not None:
                    _,destination_type,_ = late_unwrap(dest.struct_var).type.get_deref_field(
                        dest.offset,target_size=dest.target_size)
                # A required C pointer view can become known after this store
                # was lifted. Make it explicit without changing machine bits
                # or propagating a new pointee type into the source expression.
                underlying = late_unwrap(source.expr) if isinstance(source,Cast) else source
                address_cast = (isinstance(source,Cast) and isinstance(underlying,BinaryOp)
                    and underlying.op == '+' and
                    underlying.left.type.is_pointer_or_array() != underlying.right.type.is_pointer_or_array())
                if (source.type.is_pointer() and destination_type.is_pointer()
                        and (source.type.data() is not destination_type.data() or address_cast)):
                    self.store_views.append({'kind':'late-pointer-store-view',
                        'authority':'mutable source/destination type hypotheses; not binary evidence'})
                    source = BytePointerView(expr=underlying if address_cast else source,type=destination_type)
                    return self.saved[2](StoreStmt(source=source,dest=dest),fmt)
                return self.saved[2](statement,fmt)
            StoreStmt.format = store
        return self

    def __exit__(self,*exc):
        evaluate.handle_add_real,evaluate.add_imm,StoreStmt.format = self.saved

    def _view(self,result,args,kind):
        if not isinstance(result,BinaryOp) or result.op != '+':
            return result
        left,right = result.left,result.right
        lp,rp = left.type.is_pointer_or_array(),right.type.is_pointer_or_array()
        if lp == rp:
            return result
        base,offset = (left,right) if lp else (right,left)
        # A register-valued offset is a machine word; unknown pointer targets
        # remain hypotheses. Two-pointer and floating additions are excluded.
        if offset.type.is_float() or not offset.type.is_reg():
            return result
        target = base.type.get_pointer_target()
        if target is not None and target.get_size_bytes() == 1:
            return result
        instruction = args.instruction_ref.instruction
        self.changes.append({'kind':kind,'instruction':{'filename':instruction.meta.filename,
            'line':instruction.meta.lineno,'synthetic':instruction.meta.synthetic,
            'mnemonic':instruction.mnemonic}, 'base_type_hypothesis':base.type.data().kind,
            'target_size_hypothesis':target.get_size_bytes() if target is not None else None,
            'scope':'source hypothesis for a machine byte addition; compiler and semantic gates required'})
        return BinaryOp(left=BytePointerView(base),op='+',right=offset,type=Type.ptr(Type.u8()))
