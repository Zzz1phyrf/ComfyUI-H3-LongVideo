"""Bounded, best-effort diagnostics; never serialize request bodies or node inputs."""
from datetime import datetime
from functools import wraps
import inspect
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
import traceback
from .storage import MV_PREFIX, TALKING_PREFIX, output_path

MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 2
LOG_LOCK = threading.RLock()
CONTEXT_FIELDS = {"project_id", "segment_index", "prompt_id", "node_id", "node_type",
                  "source", "action", "model", "backend", "task_id"}


class ErrorFileHandler(RotatingFileHandler):
    def handleError(self, record):
        # The caller reports only the failure type, not logging's raw debug dump.
        raise


def error_log_path(workflow='MV'):
    if workflow not in ('MV', '口播'):
        raise ValueError('日志类别无效。')
    return output_path(TALKING_PREFIX if workflow == '口播' else MV_PREFIX) / "logs" / "errors.log"


def configured_key():
    try:
        from .expansion import settings
        return str(settings().get("api_key") or "")
    except Exception:
        return ""


def redact(value, key="", limit=12000):
    text = str(value)[:131072]
    from .rh_credentials import redact_keys
    text = redact_keys(text)
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(
        r'''(?i)(["']?(?:api[_-]?key|x-api-key|authorization|access[_-]?token|refresh[_-]?token|password|secret)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)''',
        r"\1[REDACTED]", text)
    text = re.sub(r'''(?i)data:[^\s"'<>]+''', "[INLINE DATA REDACTED]", text)
    text = re.sub(r'''(?i)https?://[^\s"'<>]+''', "[URL REDACTED]", text)
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def exception_trace(error):
    # File/line/function and chained errors are enough; omit source lines and locals.
    parts, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        parts.append(f"{type(error).__name__}: {error}")
        for frame in traceback.extract_tb(error.__traceback__):
            parts.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}')
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
        if error is not None:
            parts.append("Caused by / during handling:")
    return "\n".join(parts)


def record_event(stage, message, exception_type="Error", traceback_text="", workflow='MV', **context):
    """Return False on logging failure, without changing the caller's result/error."""
    try:
        key = configured_key()
        event = {"timestamp": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                 "level": "ERROR", "workflow": workflow, "stage": redact(stage, key, 100),
                 "exception_type": redact(exception_type, key, 200),
                 "message": redact(message, key, 4000), "traceback": redact(traceback_text, key)}
        for name in CONTEXT_FIELDS:
            value = context.get(name)
            if value is not None and isinstance(value, (str, int, float)):
                event[name] = redact(value, key, 500) if isinstance(value, str) else value
        index = event.get("segment_index")
        if isinstance(index, int) and index >= 0:
            event["segment_number"] = index + 1
        text = json.dumps(event, ensure_ascii=False)
        with LOG_LOCK:
            path = error_log_path(workflow)
            path.parent.mkdir(parents=True, exist_ok=True)
            # Close each time: no lingering Windows handles when projects/outputs move.
            handler = ErrorFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                       encoding="utf-8")
            try:
                handler.setFormatter(logging.Formatter("%(message)s"))
                record = logging.LogRecord("H3LongVideo", logging.ERROR, "", 0, text, (), None)
                handler.emit(record)
            finally:
                handler.close()
        return True
    except (ImportError, AttributeError):
        return False  # Host unavailable, e.g. standalone source checks.
    except Exception as error:
        print(f"[H3LongVideo] 无法写入错误日志 ({type(error).__name__})；原任务错误保持不变。")
        return False


def record_error(stage, error, **context):
    try:
        return record_event(stage, str(error), type(error).__name__, exception_trace(error), **context)
    except Exception:
        return False


def logged(stage, workflow='MV'):
    """Log sync node/service errors and re-raise the original exception unchanged."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception as error:
                context = {"source": function.__qualname__}
                try:
                    values = signature.bind_partial(*args, **kwargs).arguments
                    for name in ("project_id", "segment_index", "model"):
                        if name in values:
                            context[name] = values[name]
                    if 'pid' in values:
                        context['project_id'] = values['pid']
                    material = values.get("material")
                    if isinstance(material, dict):
                        for name in ("project_id", "segment_index"):
                            if name in material:
                                context[name] = material[name]
                    if "project" in values:
                        context["project_id"] = Path(values["project"]).name
                except Exception:
                    pass
                record_error(stage, error, workflow=workflow, **context)
                raise
        return wrapped
    return decorate


def record_history_error(history, **context):
    """Keep the actual failing ComfyUI node, never current_inputs/current_outputs."""
    try:
        for item in history.get("status", {}).get("messages") or []:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            kind, detail = item
            if kind not in {"execution_error", "execution_interrupted"} or not isinstance(detail, dict):
                continue
            trace = detail.get("traceback", "")
            if isinstance(trace, (list, tuple)):
                trace = "".join(str(line) for line in trace)
            record_event("segment_generation", detail.get("exception_message") or kind,
                         detail.get("exception_type") or kind, trace,
                         **context, node_id=detail.get("node_id"), node_type=detail.get("node_type"))
    except Exception:
        pass  # Unexpected third-party history schemas must not fail a generation.


def download_log(workflow='MV'):
    """Download retained files oldest first. Reading never creates a log."""
    with LOG_LOCK:
        path = error_log_path(workflow)
        files = [Path(str(path) + f".{number}") for number in range(BACKUP_COUNT, 0, -1)] + [path]
        contents = []
        for file in files:
            if file.is_file():
                with file.open("rb") as stream:
                    contents.append(stream.read(MAX_BYTES + 131072))
    return b"".join(contents) or "尚未记录错误。错误发生后再下载日志。\n".encode("utf-8")


def download_report():
    """One shareable attachment from either node, with both error categories."""
    import platform
    import sys
    import hashlib
    revision = hashlib.sha256()
    for name in ('diagnostics.py', 'storage.py', 'controller.py', 'talking_rh/runner.py'):
        revision.update((Path(__file__).parent/name).read_bytes())
    header = {'report': 'H3LongVideo diagnostics', 'generated_at': datetime.now().astimezone().isoformat(),
              'python': sys.version.split()[0], 'platform': platform.system(),
              'log_schema': 2, 'plugin_revision': revision.hexdigest()[:16]}
    parts = [json.dumps(header, ensure_ascii=False).encode('utf-8') + b'\n']
    for workflow in ('MV', '口播'):
        parts.append((json.dumps({'section': workflow}, ensure_ascii=False)+'\n').encode('utf-8'))
        content = download_log(workflow)
        if content.startswith('尚未记录错误'.encode('utf-8')):
            content = (json.dumps({'workflow': workflow, 'message': '尚未记录错误。'},ensure_ascii=False)+'\n').encode('utf-8')
        parts.append(content.rstrip(b'\n')+b'\n')
    return b''.join(parts)
