"""验证安装入口及消息桥资源完整。"""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path


def test_distribution_entrypoints_import():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    for target in metadata["project"]["scripts"].values():
        module, attribute = target.split(":")
        assert hasattr(importlib.import_module(module), attribute)
    for package in metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]:
        assert (root / package).is_dir()
    assert (root / "src/goofish_cli/static/goofish_js_version_2.js").is_file()
