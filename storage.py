"""Shared ComfyUI output layout for MV and talking projects."""
from pathlib import Path

MV_PREFIX = 'H3LongVideo/MV'
TALKING_PREFIX = 'H3LongVideo/口播'


def output_path(prefix):
    import folder_paths
    return Path(folder_paths.get_output_directory())/prefix


def project_roots(projects):
    """Prefer the new bucket while reading existing pre-upgrade projects in place."""
    projects = Path(projects).resolve()
    roots = [projects]
    if projects.name == 'projects' and projects.parent.parent.name == 'H3LongVideo':
        if projects.parent.name == 'MV':
            roots.append(projects.parent.parent/'projects')
        elif projects.parent.name == '口播':
            roots.append(projects.parents[2]/'H3TalkingRH/projects')
    return tuple(roots)


def project_plans(projects):
    seen = set()
    for root in project_roots(projects):
        for file in root.glob('*/state/segments.json'):
            pid = file.parent.parent.name
            if pid not in seen:
                seen.add(pid)
                yield file


def final_file(projects, path):
    path = Path(path).resolve()
    if not any(path.is_relative_to(root.parent/'final_videos') for root in project_roots(projects)):
        raise ValueError('成片文件不属于此插件的保存目录。')
    return path


def comfy_output(projects):
    """Resolve preview URLs from a project root, including legacy test roots."""
    projects = Path(projects)
    if projects.parent.parent.name == 'H3LongVideo' and projects.parent.name in ('MV', '口播'):
        return projects.parents[2]
    return projects.parents[1]
