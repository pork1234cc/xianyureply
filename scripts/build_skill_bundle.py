"""为根 Skill 生成发布清单，可导出仓库外干净安装源。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT
from bundle import VERSION, export, release_files, verify  # noqa: E402


def sources():
    return sorted(release_files(ROOT))


def build(output=None):
    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                               encoding="utf-8", check=False)
    previous_path = ROOT / "bundle-manifest.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    commit = completed.stdout.strip() if completed.returncode == 0 else previous.get("source_commit")
    manifest = {"schema_version": 1, "version": VERSION, "source_commit": commit,
                "compatibility": {"os": ["windows"], "python": ">=3.11", "database_schema": 4},
                "layout": "repository-root",
                "files": release_files(SKILL)}
    (SKILL / "bundle-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verify(SKILL)
    if output is not None:
        export(SKILL, output)
    print(f"已校验根 Skill 的 {len(manifest['files'])} 个资源文件；源码未复制到内层目录")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成根 Skill 清单及干净安装源")
    parser.add_argument("--output", type=Path, help="仓库外尚不存在的导出目录")
    build(parser.parse_args().output)
