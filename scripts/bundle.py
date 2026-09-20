"""发布包完整性检查；安装后不依赖维护仓库。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath

VERSION = "0.2.0"
REQUIRED = {
    "SKILL.md", "scripts/bridge_control.py", "scripts/bootstrap.ps1", "scripts/bundle.py",
    "pyproject.toml", "uv.lock", "config.example.yaml", ".env.example",
    "LICENSE", "NOTICE", "README.md",
    "src/goofish_cli/static/goofish_js_version_2.js",
    "src/goofish_bridge/__main__.py", "src/goofish_bridge/lifecycle.py",
    "docs/skill-setup.md", "docs/skill-operations.md",
    "docs/skill-troubleshooting.md", "docs/skill-compatibility.md",
}
TOP_LEVEL = {
    "SKILL.md", "pyproject.toml", "uv.lock", "config.example.yaml", ".env.example",
    "README.md", "LICENSE", "NOTICE", "CHANGELOG.md", ".python-version",
    ".gitattributes", ".gitignore",
    "ROOT_SKILL_PLAN.md", "SKILL_STANDARDIZATION_PLAN.md", "goofish_feishu_bridge_plan.md",
}
PUBLIC_TREES = {
    "src": {".py", ".js"}, "scripts": {".py", ".ps1"}, "docs": {".md"},
    "tests": {".py", ".md", ".json", ".jsonl", ".yaml", ".txt"},
    ".github/workflows": {".yml", ".yaml"},
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError("发布资源路径无效")
    target = root / relative
    for part in (target, *target.parents):
        if part == root.parent:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("发布资源不允许链接")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("发布资源越界")
    return target


def files(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix()
        safe_path(root, relative)
        if path.is_file() and relative != "bundle-manifest.json":
            result[relative] = digest(path)
    return result


def release_files(root: Path) -> dict:
    """只枚举发布白名单，不遍历本机环境、账号、数据库与备份。"""
    selected = {}
    paths = [root / name for name in TOP_LEVEL]
    for folder, extensions in PUBLIC_TREES.items():
        base = safe_path(root, folder)
        if base.exists():
            paths.extend(p for p in base.rglob("*")
                         if p.suffix in extensions and "__pycache__" not in p.parts)
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        safe_path(root, relative)
        if path.is_file():
            selected[relative] = digest(path)
    return selected


def verify(root: Path, strict: bool = False) -> dict:
    root = root.resolve()
    manifest = json.loads((root / "bundle-manifest.json").read_text(encoding="utf-8"))
    expected = manifest["files"]
    if manifest.get("schema_version") != 1 or not REQUIRED.issubset(expected):
        raise ValueError("发布清单缺少必需资源")
    for name in expected:
        safe_path(root, name)
    if release_files(root) != expected or (strict and files(root) != expected):
        raise ValueError("发布资源缺失、额外添加或哈希不一致")
    if sum(Path(p).name == "SKILL.md" for p in expected) != 1:
        raise ValueError("发布包必须只有一个 SKILL.md")
    return manifest


def export(root: Path, destination: Path) -> dict:
    """导出到仓库外新目录；绝不直接复制含私有数据的整个工作目录。"""
    root, destination = root.resolve(), destination.resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("导出目录必须位于源码目录之外，且不能是其父目录")
    if destination.exists():
        raise ValueError("导出目录已存在，不覆盖已有文件")
    manifest = verify(root)
    destination.mkdir(parents=True)
    for relative in (*manifest["files"], "bundle-manifest.json"):
        source = safe_path(root, relative)
        target = safe_path(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    verify(destination, strict=True)
    return manifest
