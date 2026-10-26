"""
make_zip.py - build a clean permission-rag-api.zip from this directory.

Uses an explicit allowlist, so nothing else can slip in: no .venv, caches,
.pyc files, .env files, or Git metadata. As a second check, the script also
refuses to proceed if an allowlisted path looks like one of those.

Run:  python make_zip.py
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "permission-rag-api.zip"
TOP_LEVEL_DIR = "permission-rag-api"  # files unpack into this folder, not into the current directory
SIZE_LIMIT_KB = 100

ESSENTIAL_FILES = [
    "api.py", 
    "auth.py",
    "rag.py",
    "app.py",
    "test_isolation.py",
    "requirements.txt",
    "README.md",
]

FORBIDDEN_PARTS = {".venv", "__pycache__", ".pytest_cache", ".git"}
FORBIDDEN_SUFFIXES = {".pyc"}


def is_forbidden(path: Path) -> bool:
    name = path.name
    return (
        bool(FORBIDDEN_PARTS & set(path.parts))
        or path.suffix in FORBIDDEN_SUFFIXES
        or name == ".env"
        or name.startswith(".env.")
        or name == ".gitignore"
        or name == ".gitattributes"
    )


def main() -> int:
    missing = [f for f in ESSENTIAL_FILES if not (ROOT / f).is_file()]
    if missing:
        print(f"ERROR: missing required file(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    blocked = [f for f in ESSENTIAL_FILES if is_forbidden(Path(f))]
    if blocked:
        print(f"ERROR: allowlist contains forbidden path(s): {', '.join(blocked)}", file=sys.stderr)
        return 1

    ARCHIVE.unlink(missing_ok=True)  # never append to a stale archive
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in ESSENTIAL_FILES:
            zf.write(ROOT / name, arcname=f"{TOP_LEVEL_DIR}/{name}")

    with zipfile.ZipFile(ARCHIVE) as zf:
        bad = zf.testzip()
        entries = zf.infolist()
    if bad:
        print(f"ERROR: archive failed integrity check at {bad}", file=sys.stderr)
        return 1

    print(f"Created {ARCHIVE.name} with {len(entries)} files:")
    for info in entries:
        print(f"  {info.filename:<40} {info.file_size:>8,} B")

    size_kb = ARCHIVE.stat().st_size / 1024
    print(f"\nFinal size: {size_kb:.1f} KB ({ARCHIVE.stat().st_size:,} bytes)")
    if size_kb >= SIZE_LIMIT_KB:
        print(f"WARNING: archive is not under {SIZE_LIMIT_KB} KB, so check what was included.", file=sys.stderr)
        return 1
    print(f"OK: under {SIZE_LIMIT_KB} KB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
