"""Per-take sampler seeds; retries reuse the intent persisted before submission."""
import secrets

SEED_FIELDS = {'SelfLiftAvatarH3Sampler':'seed', 'SamplerCustom':'noise_seed',
               'KSampler':'seed', 'KSamplerAdvanced':'noise_seed'}


def seed_values(graph, bound_seeds=None):
    values = {}
    for key, node in graph.items():
        field = SEED_FIELDS.get(node.get('class_type'))
        if not field:
            continue
        value = node.get('inputs', {}).get(field)
        if isinstance(value, list):
            value = (bound_seeds or {}).get(key)
        if type(value) is int and 0 <= value < 2**64:
            values[key] = value
    return values


def apply_seed_overrides(graph, row, bound_seeds=None):
    overrides = row.get('seed_overrides', {})
    for key, value in overrides.items():
        field = SEED_FIELDS.get(graph.get(key, {}).get('class_type'))
        if not field or type(value) is not int or not 0 <= value < 2**64:
            raise ValueError('本段保存的抽卡种子与当前采样节点不匹配，请核对生成工作流。')
        graph[key]['inputs'][field] = value
    values = seed_values(graph, bound_seeds)
    if row.get('reroll_pending'):
        if not values:
            raise ValueError('未找到可更换种子的采样节点；请使用固定整数种子后再抽卡。')
        changed = {}
        for key, old in values.items():
            value = secrets.randbits(53)  # Exact in browser JSON and copyable from the panel.
            if value == old:
                value = (value + 1) % 2**53
            changed[key] = value
            graph[key]['inputs'][SEED_FIELDS[graph[key]['class_type']]] = value
        row['seed_overrides'] = values = changed
        row.pop('reroll_pending', None)
    return values


def save_seed_intent(target, source):
    """Persist alongside the task ID, including a chosen seed that survives failure."""
    if 'seed_overrides' in source:
        target['seed_overrides'] = dict(source['seed_overrides'])
    target.pop('reroll_pending', None)
