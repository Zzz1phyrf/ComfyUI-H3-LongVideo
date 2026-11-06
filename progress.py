"""Persistent, credential-free generation stages shared by canvas and panel."""
import time
from .core import LOCK, read_plan, write_plan


def report(root, pid, stage, index=None):
    with LOCK:
        plan = read_plan(root, pid)
        previous = plan.get('progress') or {}
        if previous.get('stage') == stage and previous.get('index') == index:
            return
        progress = {'stage': stage, 'index': index, 'since': time.time()}
        plan['progress'] = progress
        write_plan(root, plan)
    try:
        from server import PromptServer
        PromptServer.instance.send_sync('h3lv-progress', {
            'project_id': pid, 'count': len(plan['segments']),
            'backend': plan.get('generation_backend'), 'progress': progress})
    except (ImportError, AttributeError):
        pass  # Offline tools have no live websocket server.
