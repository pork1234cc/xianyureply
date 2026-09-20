"""校验根 Skill 或隔离安装产物的完整性。"""

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from bundle import verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description="校验根 Skill 资源哈希")
    parser.add_argument("--skill", type=Path, default=ROOT)
    parser.add_argument("--source-check", action="store_true", help="兼容旧命令；根目录即唯一源码")
    parser.add_argument("--strict", action="store_true", help="干净发布目录不得含白名单外文件")
    args = parser.parse_args()
    manifest = verify(args.skill, strict=args.strict)
    print(f"校验通过：{len(manifest['files'])} 个资源，版本 {manifest['version']}")


if __name__ == "__main__":
    main()
