"""Make tracked sources readable by container users; never touch .env or backups."""

from pathlib import Path
import subprocess
root = Path.cwd()
for entry in subprocess.check_output(['git', 'ls-files', '--stage', '-z']).split(b'\0'):
    if not entry:
        continue
    metadata, name = entry.split(b'\t', 1)
    path = root / name.decode()
    if path.is_symlink():
        continue
    path.chmod(0o755 if metadata.split()[0] == b'100755' else 0o644)
    for parent in path.parents:
        parent.chmod(0o755)
        if parent == root:
            break
