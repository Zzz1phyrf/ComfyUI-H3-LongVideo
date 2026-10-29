"""Bounded, ephemeral Codex CLI requests using the existing local login."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
import tomllib


REASONING = ('default', 'low', 'medium', 'high', 'xhigh')


def options(model='', reasoning='default', timeout=300):
    model = str(model or '').strip()
    if model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}', model):
        raise ValueError('Codex 模型名称无效，请填写模型 ID 或留空使用 CLI 配置。')
    if reasoning not in REASONING:
        raise ValueError('Codex 推理档位无效。')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 10 <= timeout <= 3600:
        raise ValueError('Codex 等待超时必须是 10–3600 秒。')
    return {'model': model, 'reasoning': reasoning, 'timeout': timeout}


def _config():
    home = Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    path = home/'config.toml'
    raw = path.read_bytes() if path.is_file() else b''
    return raw, tomllib.loads(raw.decode('utf-8-sig')) if raw else {}


def identity(model='', reasoning='default'):
    raw, _ = _config()
    return {'backend': 'codex_cli_v1', 'model': model, 'reasoning': reasoning,
            'config_hash': hashlib.sha256(raw).hexdigest()}


def resolve_executable():
    explicit = os.environ.get('CODEX_CLI_EXE')
    if explicit:
        path = Path(explicit)
        if not path.is_absolute() or not path.is_file() or (os.name == 'nt' and path.suffix.lower() != '.exe'):
            raise ValueError('CODEX_CLI_EXE 必须指向本机 Codex 可执行文件的绝对路径。')
        return str(path.resolve())
    for name in ('codex.exe', 'codex.cmd', 'codex.ps1', 'codex'):
        found = shutil.which(name)
        if not found:
            continue
        path = Path(found)
        if os.name != 'nt' or path.suffix.lower() == '.exe':
            return str(path.resolve())
        modules = path.parent/'node_modules'/'@openai'
        for pattern in ('codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe',
                        'codex-win32-*/vendor/*/bin/codex.exe'):
            for native in modules.glob(pattern):
                if native.is_file():
                    return str(native.resolve())
    raise ValueError('找不到 Codex CLI；请安装并登录 Codex，或用 CODEX_CLI_EXE 指定 codex.exe。')


class _WindowsJob:
    """Assign a suspended process before it can create any descendants."""
    def __init__(self):
        from ctypes import wintypes as w
        size = ctypes.c_size_t

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', w.DWORD), ('minimum', size), ('maximum', size),
                        ('process_limit', w.DWORD), ('affinity', size),
                        ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', size),
                        ('job_memory', size), ('peak_process', size), ('peak_job', size)]

        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.CloseHandle.restype = w.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError('无法配置 Codex 子进程任务组。')

    def assign_and_resume(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        resume = ctypes.WinDLL('ntdll').NtResumeProcess
        resume.argtypes = [ctypes.c_void_p]
        resume.restype = ctypes.c_long
        if resume(int(process._handle)) != 0:
            raise OSError('无法启动 Codex 子进程。')

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_process(argv, directory, stdin, stdout, stderr, env, timeout, cancelled):
    job = _WindowsJob() if os.name == 'nt' else None
    process = None
    try:
        process = subprocess.Popen(argv, cwd=directory, stdin=stdin, stdout=stdout, stderr=stderr,
            env=env, shell=False, creationflags=(0x08000000 | 0x00000004) if job else 0,
            start_new_session=not bool(job))
        if job:
            job.assign_and_resume(process)
        deadline = time.monotonic()+timeout
        while process.poll() is None:
            if cancelled:
                cancelled()
            if time.monotonic() >= deadline:
                raise ValueError(f'Codex CLI 等待超过 {timeout:g} 秒，已停止本次任务；没有自动重试。')
            time.sleep(.1)
        return process.returncode
    finally:
        if job:
            job.close()
        elif process:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def generate(prompt, images, model='', reasoning='default', timeout=300, cancelled=None,
             expected_identity=None):
    config = options(model, reasoning, timeout)
    executable = resolve_executable()
    raw, local_config = _config()
    def check_config():
        if expected_identity is not None and hashlib.sha256(_config()[0]).hexdigest() != expected_identity.get('config_hash'):
            raise ValueError('Codex CLI 配置已变化；请重新开始批次，避免混用旧配置。')
    if expected_identity is not None and hashlib.sha256(raw).hexdigest() != expected_identity.get('config_hash'):
        raise ValueError('Codex CLI 配置已变化；请重新开始批次，避免混用旧配置。')
    if cancelled:
        cancelled()
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in ('OPENAI_API_KEY', 'CODEX_API_KEY')}
    with tempfile.TemporaryDirectory(prefix='h3lv_codex_') as temporary:
        directory = Path(temporary)
        answer = directory/'answer.txt'
        source = directory/'prompt.txt'
        source.write_text(prompt, encoding='utf-8')
        argv = [executable, 'exec', '--ephemeral', '--color', 'never', '--sandbox', 'read-only',
                '--skip-git-repo-check', '-C', str(directory), '--output-last-message', str(answer),
                '-c', 'approval_policy="never"', '-c', 'project_doc_max_bytes=0',
                '--disable', 'shell_tool', '--disable', 'apps']
        for name in local_config.get('mcp_servers', {}):
            if re.fullmatch(r'[A-Za-z0-9_-]+', name):
                argv.extend(['-c', f'mcp_servers.{name}.enabled=false'])
        if config['model']:
            argv.extend(['--model', config['model']])
        if reasoning != 'default':
            argv.extend(['-c', 'model_reasoning_effort='+json.dumps(reasoning)])
        if images:
            from PIL import Image, ImageOps
            argv.append('--image')
            for index, path in enumerate(images):
                image_path = directory/f'picture_{index+1:02d}.jpg'
                with Image.open(path) as image:
                    image = ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((1280, 1280))
                    image.save(image_path, format='JPEG', quality=88)
                argv.append(str(image_path))
        with source.open('rb') as input_file, (directory/'stdout.log').open('wb') as out, \
             (directory/'stderr.log').open('wb') as err:
            try:
                check_config()
                code = run_process(argv, directory, input_file, out, err, env, timeout, cancelled)
            except OSError:
                raise ValueError('Codex CLI 启动失败，请检查本机 CLI 安装与运行权限。') from None
        if code:
            error = (directory/'stderr.log').read_text(encoding='utf-8', errors='replace').lower()
            if any(token in error for token in ('unauthorized', 'not logged in', '401', 'authentication')):
                raise ValueError('Codex CLI 未登录或登录已失效，请在当前 Windows 用户下运行 codex login。')
            if 'model' in error and any(token in error for token in ('not found', 'unsupported', 'not supported', 'does not exist')):
                raise ValueError('Codex CLI 不支持所选模型或推理档位，请检查 CLI 设置。')
            raise ValueError(f'Codex CLI 生成失败（退出码 {code}），请检查 CLI 登录、网络和模型设置；没有自动重试。')
        if not answer.is_file() or answer.stat().st_size > 128000:
            raise ValueError('Codex CLI 没有返回有效的最终文本。')
        text = answer.read_text(encoding='utf-8-sig').strip()
        if not text:
            raise ValueError('Codex CLI 返回的最终文本为空。')
        check_config()
        if cancelled:
            cancelled()
        return text
