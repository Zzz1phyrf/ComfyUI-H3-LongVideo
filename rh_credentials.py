"""Machine-local RH credentials, separate from workflows and project snapshots."""
import json
import os
from pathlib import Path
import tempfile
import threading

SITES = ('https://www.runninghub.ai', 'https://www.runninghub.cn')
LOCK = threading.RLock()


def settings_path():
    from .nodes import user_data_root
    return user_data_root() / 'runninghub_api.json'


def _read():
    try:
        path = settings_path()
    except (ImportError, AttributeError):
        # Offline tools may not have ComfyUI's folder_paths module.
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
        if not isinstance(value, dict) or any(k not in SITES or not isinstance(v, str) for k, v in value.items()):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise ValueError('RH本机配置读取失败，请检查用户目录中的runninghub_api.json。') from None


def _site(base_url):
    if base_url not in SITES:
        raise ValueError('请选择RunningHub国内站或海外站。')
    return base_url


def api_key(base_url):
    with LOCK:
        return _read().get(_site(base_url), '').strip() or os.environ.get('RUNNINGHUB_API_KEY', '').strip()


def public_settings(base_url):
    with LOCK:
        saved = bool(_read().get(_site(base_url), '').strip())
        environment = bool(os.environ.get('RUNNINGHUB_API_KEY', '').strip())
        return {'configured': saved or environment, 'source': 'local' if saved else 'environment' if environment else 'none'}


def save_settings(base_url, key=None, clear=False):
    _site(base_url)
    if not isinstance(clear, bool) or (key is not None and not isinstance(key, str)):
        raise ValueError('RH设置格式无效。')
    if clear and key:
        raise ValueError('清除和保存Key不能同时执行。')
    if not clear and (key is None or not key.strip() or len(key) > 4096 or any(c.isspace() for c in key.strip())):
        raise ValueError('请填写有效的RH API Key；留空不会覆盖原配置。')
    with LOCK:
        value = _read()
        if clear:
            value.pop(base_url, None)
        else:
            value[base_url] = key.strip()
        path = settings_path()
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.rh-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream)
            os.replace(temporary, path)
        except OSError:
            raise ValueError('RH设置保存失败，请检查ComfyUI用户目录的写入权限。') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return public_settings(base_url)


def redact_keys(text):
    keys = [os.environ.get('RUNNINGHUB_API_KEY', '')]
    try:
        keys.extend(_read().values())
    except ValueError:
        pass
    for key in keys:
        if key.strip():
            text = text.replace(key.strip(), '[REDACTED]')
    return text
