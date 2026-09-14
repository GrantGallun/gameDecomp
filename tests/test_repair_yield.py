from eval.repair_yield import record, size_band


def test_exact_bytes_count_transitions_not_similarity_and_exclude_queue_time():
    metrics={}
    profile={'name':'finish'}
    result={'performance':{'model_prompt_seconds':2,'model_generation_seconds':3,
                           'model_queue_seconds':100,'model_seconds':7},'wall_seconds':110}
    node={'size':2048,'status':'pending','score':100}
    record(metrics, {'status':'pending'}, node, profile, result)
    assert metrics['repair_yield']['totals']['exact_bytes_gained']==0
    node['status']='object_exact'
    record(metrics, {'status':'pending'}, node, profile, result)
    record(metrics, {'status':'object_exact'}, node, profile, result)
    totals=metrics['repair_yield']['totals']
    assert totals['exact_bytes_gained']==2048 and totals['exact_functions_gained']==1
    assert totals['inference_seconds']==15 and totals['model_queue_seconds']==300
    assert metrics['repair_yield']['by_size']['at_least_1KiB']==totals


def test_unknown_size_and_any_loss_remain_explicit():
    metrics={}
    record(metrics,{'status':'pending'},{'status':'object_exact'}, {}, {})
    record(metrics,{'status':'object_exact'},{'status':'pending','size':64}, {}, {})
    totals=metrics['repair_yield']['totals']
    assert totals['exact_functions_gained']==1 and totals['exact_bytes_gained']==0
    assert totals['unknown_size_items']==1 and totals['exact_bytes_lost']==64
    assert totals['exact_functions_lost']==1
    assert [size_band(n) for n in (0,False,255,256,1024)]==['unknown','unknown','under_256B','256B_to_1KiB','at_least_1KiB']
