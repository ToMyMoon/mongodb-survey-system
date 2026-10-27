"""Create or explicitly replace a verified source snapshot, excluding local data."""
import argparse
import os
from datetime import date
from pathlib import Path
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--replace", action="store_true", help="校验完成后原子替换已有源码压缩包")
parser.add_argument("--date", default=date.today().isoformat(), help="源码包日期 YYYY-MM-DD")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
try:
    package_date = date.fromisoformat(args.date).isoformat()
except ValueError:
    parser.error("日期格式应为 YYYY-MM-DD")
target = root.parent / "提交材料" / f"第二阶段源码_{package_date}.zip"
if target.exists() and not args.replace:
    parser.error("源码压缩包已存在；明确更新时请使用 --replace")
target.parent.mkdir(parents=True, exist_ok=True)
excluded = {".venv", ".mongo", "node_modules", "__pycache__", ".pytest_cache", ".git",
            "提交材料", "报告", "历史备份", ".npm-cache"}
suffixes = {".py", ".js", ".cjs", ".css", ".html", ".json", ".md", ".txt", ".ps1", ".lock", ".xml", ".png"}
files = []
for directory, subdirectories, names in os.walk(root):
    subdirectories[:] = [name for name in subdirectories if name not in excluded]
    for name in names:
        source = Path(directory) / name
        if source.suffix in suffixes or source.name == ".gitignore":
            files.append(source)
files.sort()
with tempfile.NamedTemporaryFile(prefix=".source-", suffix=".zip", dir=root, delete=False) as temporary:
    staged = Path(temporary.name)
try:
    with ZipFile(staged, "w", compression=ZIP_DEFLATED) as archive:
        for source in files:
            archive.write(source, source.relative_to(root).as_posix())
    with ZipFile(staged) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(files)
        for source in files:
            assert archive.read(source.relative_to(root).as_posix()) == source.read_bytes()
    os.replace(staged, target)
finally:
    staged.unlink(missing_ok=True)
print(f"已生成并逐文件校验 {target.name}，共 {len(files)} 个文件，{target.stat().st_size} 字节。")
