"""Persistent expansion candidates; workers never write a project's segment plan."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import uuid

from . import expansion

_LOCK = threading.RLock()
_RUNNING = {}
_DEFAULTS = dict(mode='vision', model='qwen/qwen3.8-flash', rule='', revision=0,
                 provider='api', codex_model='', codex_reasoning='default', codex_timeout=300)
_MATERIAL_FIELDS = frozenset((
    'paths', 'hashes', 'refs', 'project_id', 'segment_index', 'material_note', 'visual_type', 'mode', 'brief', 'duration',
    'contract_version', 'brief_source', 'shot_intent',
    'generation_frames', 'generation_seconds', 'audio_role', 'audio_section',
    'audio_role_reason', 'audio_reference_available', 'audio_reference_slot',
    'cache_dir', 'expanded_prompt', 'expanded_key'))
_SUCCESS = frozenset(('succeeded', 'cached', 'protected'))
_LIVE = frozenset(('pending', 'running', 'cancelling'))


def _now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds')


def _directory(project_dir):
    return Path(project_dir).resolve()/'state'/'expansion_jobs'


def _path(project_dir, job_id):
    try:
        canonical = str(uuid.UUID(str(job_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('批次 ID 无效。') from None
    return _directory(project_dir)/(canonical+'.json')


def _identity(project_dir, job_id):
    return str(_path(project_dir, job_id))


def _counts(job):
    counts = {}
    for item in job['items']:
        status = item['status']
        counts[status] = counts.get(status, 0)+1
    job['counts'] = counts
    job['state'] = job['status']
    job['total'] = len(job['items'])
    job['completed'] = sum(value for status, value in counts.items() if status not in ('pending', 'running'))


def _persist(runtime):
    job = runtime['job']
    job['updated_at'] = _now()
    _counts(job)
    path = runtime['path']
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(job, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def _snapshot(job):
    result = copy.deepcopy(job)
    _counts(result)
    return result


def _load(project_dir, job_id):
    path = _path(project_dir, job_id)
    if not path.is_file():
        raise ValueError('找不到扩写批次。')
    job = json.loads(path.read_text(encoding='utf-8'))
    if job.get('status') in _LIVE:
        for item in job['items']:
            if item['status'] in ('pending', 'running'):
                item.update(status='interrupted', error='服务已重启，此段未自动重发；可选择未完成段重新开始。')
        job.update(status='interrupted', error='服务已重启；成功候选已保留，未完成请求不会自动重发。', finished_at=_now())
        _persist({'job': job, 'path': path})
    return job


def active(project_id):
    """Remain true until all local workers have exited, including API waits."""
    with _LOCK:
        return any(value['job']['project_id'] == project_id for value in _RUNNING.values())


def get(project_dir, job_id):
    with _LOCK:
        runtime = _RUNNING.get(_identity(project_dir, job_id))
        return _snapshot(runtime['job'] if runtime else _load(project_dir, job_id))


def list_jobs(project_dir):
    with _LOCK:
        directory = _directory(project_dir)
        jobs = [get(project_dir, path.stem) for path in directory.glob('*.json')]
        return sorted(jobs, key=lambda value: value.get('created_at', ''), reverse=True)


def _safe_error(error, runtime):
    return expansion.safe_error(error, runtime['profile'])


def _finish(runtime):
    job = runtime['job']
    if runtime['cancel'].is_set():
        job['status'] = 'cancelled'
    elif runtime.get('paused'):
        job['status'] = 'paused'
    else:
        job['status'] = 'succeeded' if all(item['status'] in _SUCCESS for item in job['items']) else 'failed'
    job['finished_at'] = _now()
    _persist(runtime)


def _next(runtime):
    with _LOCK:
        if runtime['cancel'].is_set() or runtime.get('paused'):
            return None
        changed = False
        rows = runtime['job']['items']
        for item in rows:
            if item['status'] != 'pending':
                continue
            # Repeated segments can have identical inputs. A failure is also
            # one attempted key: do not turn its aliases into implicit retries.
            same = [other for other in rows if other is not item and item['key'] and other['key'] == item['key']]
            previous = next((other for other in same if other['status'] in ('succeeded', 'cached', 'failed')), None)
            if previous is not None:
                success = previous['status'] in ('succeeded', 'cached')
                item.update(status='cached' if success else 'failed', text=previous['text'],
                            source='cache' if success else previous['source'], error=previous['error'],
                            finished_at=_now())
                changed = True
                continue
            if any(other['status'] == 'running' for other in same):
                continue
            item.update(status='running', attempt=item['attempt']+1, started_at=_now(), error='')
            _persist(runtime)
            return item
        if changed:
            _persist(runtime)
        return None


def _worker(runtime):
    try:
        while (item := _next(runtime)) is not None:
            def cancelled():
                if runtime['cancel'].is_set():
                    raise expansion.ExpansionCancelled('本批次已取消，未写入项目。')
            info = {}
            try:
                text = expansion.expand(item['material'], **runtime['job']['settings'],
                    request_profile=runtime['profile'], cancelled=cancelled, result_info=info)
                with _LOCK:
                    cancelled()
                    item.update(status='cached' if info.get('source') in ('saved', 'cache') else 'succeeded',
                                text=text, key=info.get('key', item['key']), source=info.get('source', ''), error='')
            except expansion.ExpansionCancelled as error:
                with _LOCK:
                    item.update(status='cancelled', error=_safe_error(error, runtime))
            except Exception as error:
                with _LOCK:
                    was_cancelled = runtime['cancel'].is_set()
                    item.update(status='cancelled' if was_cancelled else 'failed',
                                error='本批次已取消，未写入项目。' if was_cancelled else _safe_error(error, runtime))
                    if not was_cancelled and expansion.configuration_error(error):
                        runtime['paused'] = True
                        runtime['job']['error'] = '配置或鉴权错误，已暂停排队段；修复后可手动重新开始。 '+item['error']
                        for pending in runtime['job']['items']:
                            if pending['status'] == 'pending':
                                pending['error'] = '批次已暂停，未发送请求。'
            finally:
                with _LOCK:
                    item['finished_at'] = _now()
                    _persist(runtime)
    finally:
        with _LOCK:
            runtime['workers'] -= 1
            if runtime['workers'] == 0:
                try:
                    _finish(runtime)
                finally:
                    # The in-memory credential snapshot is released with the last
                    # worker. Completed candidates remain only in the safe JSON.
                    _RUNNING.pop(str(runtime['path']), None)


def start(project_dir, project_id, project_revision, items, settings,
          request_profile=None, concurrency=2):
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency not in (1, 2, 3, 4):
        raise ValueError('批量扩写并发只能选择整数 1、2、3 或 4。')
    if not isinstance(items, list) or not items:
        raise ValueError('请至少选择一个分段。')
    if not isinstance(settings, dict):
        raise ValueError('扩写设置无效。')
    selected = {name: copy.deepcopy(settings.get(name, default)) for name, default in _DEFAULTS.items()}
    if any(not isinstance(value, (str, int, float)) or isinstance(value, bool) for value in selected.values()):
        raise ValueError('扩写设置必须是文本或数字。')
    if selected['mode'] not in ('vision', 'text', 'manual'):
        raise ValueError('扩写方式无效。')
    if selected['mode'] == 'manual':
        selected['provider'] = 'api'
    expansion.generation_options(selected['provider'], selected['codex_model'],
                                 selected['codex_reasoning'], selected['codex_timeout'])
    profile = copy.deepcopy(request_profile) if request_profile is not None else expansion.capture_request_profile(
        selected['provider'], selected['codex_model'], selected['codex_reasoning'])
    if selected['provider'] == 'api':
        profile.setdefault('base_url', 'https://api.ofox.io/v1')
        profile.update(expansion.api_options(profile))
    rows, seen = [], set()
    for supplied in items:
        if not isinstance(supplied, dict):
            raise ValueError('批量扩写分段参数无效。')
        index = supplied.get('index')
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index in seen:
            raise ValueError('批量扩写段号无效或重复。')
        seen.add(index)
        source = supplied.get('material')
        material = {name: copy.deepcopy(value) for name, value in source.items() if name in _MATERIAL_FIELDS} if isinstance(source, dict) else None
        item = dict(index=index, material=material, key='', text='', source='', error='', attempt=0,
                    protected=bool(supplied.get('protected')), status='pending')
        if item['protected']:
            item.update(status='protected', source='saved')
        elif supplied.get('error'):
            item.update(status='failed', error=expansion.safe_error(supplied['error'], profile))
        else:
            try:
                if not material or not material.get('paths'):
                    raise ValueError('此段没有有效参考图；请补充素材后手动重试。')
                item['key'] = expansion.cache_key(material, **selected, profile=profile)
            except Exception as error:
                item.update(status='failed', error=expansion.safe_error(error, profile))
        rows.append(item)
    job_id = str(uuid.uuid4())
    job = dict(schema=1, job_id=job_id, id=job_id, project_id=project_id,
               project_revision=project_revision, settings=selected, concurrency=concurrency,
               created_at=_now(), status='running', error='', items=rows)
    path = _path(project_dir, job_id)
    runtime = dict(job=job, path=path, profile=profile, cancel=threading.Event(), workers=0)
    with _LOCK:
        if active(project_id):
            raise ValueError('此项目已有批量扩写正在运行。')
        pending = sum(item['status'] == 'pending' for item in rows)
        runtime['workers'] = min(concurrency, pending)
        if not runtime['workers']:
            _finish(runtime)
            return _snapshot(job)
        _persist(runtime)
        _RUNNING[str(path)] = runtime
        for _ in range(runtime['workers']):
            threading.Thread(target=_worker, args=(runtime,), daemon=True,
                             name='h3lv-expand-'+job_id[:8]).start()
        return _snapshot(job)


def cancel(project_dir, job_id):
    with _LOCK:
        runtime = _RUNNING.get(_identity(project_dir, job_id))
        if runtime:
            runtime['cancel'].set()
            runtime['job']['status'] = 'cancelling'
            for item in runtime['job']['items']:
                if item['status'] == 'pending':
                    item.update(status='cancelled', error='本批次已取消，此段未发送请求。')
            _persist(runtime)
            return _snapshot(runtime['job'])
        job = _load(project_dir, job_id)
        if job['status'] in ('paused', 'interrupted'):
            for item in job['items']:
                if item['status'] in ('pending', 'running', 'interrupted'):
                    item.update(status='cancelled', error='本批次已取消。')
            job.update(status='cancelled', finished_at=_now())
            _persist({'job': job, 'path': _path(project_dir, job_id)})
        return _snapshot(job)
