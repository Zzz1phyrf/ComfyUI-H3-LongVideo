"""Build the plugin with two public workflows, without media or user settings."""
import argparse
import hashlib
from pathlib import Path
import re
import zipfile


def build(output):
    root=Path(__file__).resolve().parents[1]
    names=['__init__.py','nodes.py','core.py','storage.py','audio_preparation.py','controller.py','routes.py','director_rules.py',
           'materials.py','diagnostics.py','workflow_errors.py','expansion.py','progress.py','worker.py','mv_rh.py','rh_credentials.py','requirements.txt',
           'README.md','README_TALKING_RH.md','FRIEND_TESTING.md','MV_RUNNINGHUB.md','TALKING_CANVAS.md','TALKING_OVERLAP.md',
           'scripts/migrate_talking_resolution.py','scripts/migrate_talking_sampler.py',
           'CHANGELOG.md','ref2va_performance_rule.txt',
           'examples/MiniMaxH3MV数字人工作流 By 像素幻想Lab.json',
           'examples/MiniMaxH3口播数字人工作流 By 像素幻想Lab.json']
    for folder,extensions in [('talking_rh',{'.py'}),('defaults',{'.json','.txt'}),('web',{'.js','.css','.html'})]:
        names.extend(str(p.relative_to(root)).replace('\\','/') for p in (root/folder).rglob('*') if p.is_file() and p.suffix in extensions)
    files={name:(root/name).read_bytes() for name in sorted(set(names))}
    for name,data in files.items():
        text=data.decode('utf-8-sig')
        if re.search(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]',text) or '__pycache__' in name:
            raise ValueError('Candidate has machine-coupled content: '+name)
        if any(token in text for token in ('2110742089565757442','rh-images-','submitted_at','RUNNINGHUB_API_KEY=')):
            raise ValueError('Candidate has experiment-only data: '+name)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr('ComfyUI-H3-LongVideo/'+name,data)
    return {'path':str(output.resolve()),'files':len(files),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__=='__main__':
    import json
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    print(json.dumps(build(parser.parse_args().output),ensure_ascii=False))
