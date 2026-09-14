"""Production-compiler experiments for address materialization conditions."""
from solver.principle_variants import Variant
from solver import code_shapes


def probes(source, function):
    region=code_shapes._body(source,function)
    if not region:return []
    _,begin,end=region
    header=source[:begin]
    address='&gCallbackTaskActiveListSentinel'
    cases=[
      ('scalar','cur = gCallbackTaskActiveListSentinel.next;'),
      ('two-loads','cur = gCallbackTaskActiveListSentinel.next; if (priority) cur = gCallbackTaskActiveListSentinel.prev;'),
      ('pointer-phi','if (priority) cur = '+address+'; else cur = '+address+'; cur=cur->next;'),
      ('address-return','cur = '+address+'; if (priority) return cur; cur=cur->next;'),
      ('reuse-address-store','cur = '+address+'; cur->prev=cur; cur=cur->next;'),
      ('integer-address-escape','cur = '+address+'; if (priority) return (CallbackTask *)((u32)cur + type); cur=cur->next;'),
      ('wide-address','cur = ((CallbackTask *)(u32)(u64)(u32)'+address+')->next;'),
      ('signed-wide-address','cur = ((CallbackTask *)(s32)(s64)(s32)'+address+')->next;'),
      ('shift-pair','cur = ((CallbackTask *)(((u64)(u32)'+address+' << 32) >> 32))->next;'),
      ('xor-pair','cur = ((CallbackTask *)(((u32)'+address+' ^ 0x80000000U) ^ 0x80000000U))->next;'),
      ('offset-pair','cur = ((CallbackTask *)((u32)'+address+' + 0x10000U - 0x10000U))->next;'),
      ('union-view','{ union { CallbackTask *p; u32 n; } a; a.p='+address+';cur=((CallbackTask *)a.n)->next; }'),
      ('union-member','{ union { CallbackTask *p; u32 n; } a; a.p='+address+';cur=a.p->next; }'),
      ('address-array','{ CallbackTask *a[1]; a[0]='+address+';cur=a[0]->next; }'),
      ('pointer-double','{ CallbackTask *a; CallbackTask **p; a='+address+'; p=&a;cur=(*p)->next; }'),
      ('cfg-loop','cur='+address+';for (;;) { cur=cur->next;if(cur==0||cur->priority<priority)break;}'),
      ('cfg-guard','if(gCallbackTaskActiveListSentinel.next) {cur='+address+';cur=cur->next;}else cur=0;'),
      ('cfg-switch','switch(type){case 0:cur='+address+';break;default:cur='+address+';}cur=cur->next;'),
    ]
    result=[Variant(label,header+'\nCallbackTask *cur;\n'+body+'\nreturn cur;\n}'+source[end+1:]) for label,body in cases]
    for decl,expression,label in [
      ('extern CallbackTask gCallbackTaskActiveListSentinel[1];','gCallbackTaskActiveListSentinel[0].next','array-one'),
      ('extern CallbackTask gCallbackTaskActiveListSentinel[];','gCallbackTaskActiveListSentinel[0].next','array-unknown'),
      ('extern u8 gCallbackTaskActiveListSentinel[];','((CallbackTask *)gCallbackTaskActiveListSentinel)->next','byte-array'),
      ('extern union { CallbackTask node; u32 words[6]; } gCallbackTaskActiveListSentinel;','gCallbackTaskActiveListSentinel.node.next','union-global'),
    ]:
      changed=header.replace('extern CallbackTask gCallbackTaskActiveListSentinel;',decl)
      result.append(Variant(label,changed+'\nreturn '+expression+';\n}'+source[end+1:]))
    return result


def candidates(source,function,maximum=36):
    region=code_shapes._body(source,function)
    if not region or maximum<=0:return []
    _,begin,end=region
    marker='cur = insertAfter->next;'
    if source[begin:end].count(marker)!=1:return []
    statements=[
      ('union-reload','{ union { CallbackTask *p; u32 n; } a; a.p=insertAfter;cur=((CallbackTask *)a.n)->next; }'),
      ('wide-reload','cur=((CallbackTask *)(u32)(u64)(u32)insertAfter)->next;'),
      ('signed-wide-reload','cur=((CallbackTask *)(s32)(s64)(s32)insertAfter)->next;'),
      ('shift-reload','cur=((CallbackTask *)(((u64)(u32)insertAfter<<32)>>32))->next;'),
      ('array-reload','{ CallbackTask *a[1];a[0]=insertAfter;cur=a[0]->next; }'),
      ('phi-reload','if(priority)cur=insertAfter;else cur=&gCallbackTaskActiveListSentinel;cur=cur->next;'),
      ('null-guard-reload','cur=insertAfter;if(cur!=NULL)cur=cur->next;'),
      ('address-compare','cur=insertAfter;if(cur==&gCallbackTaskActiveListSentinel)cur=cur->next;else cur=insertAfter->next;'),
      ('switch-reload','switch(type){case 0:cur=insertAfter;break;default:cur=&gCallbackTaskActiveListSentinel;}cur=cur->next;'),
    ]
    return [Variant('callback-address:'+label,source[:begin]+source[begin:end].replace(marker,body)+source[end:]) for label,body in statements][:maximum]
