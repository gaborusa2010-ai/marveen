import { execFileSync } from 'node:child_process'

// `zip -q -r -y <out> .` run in <dir>, without the zip binary: a host may have
// unzip (which the import route needs) but no zip (measured 2026-11-03 on the
// live WSL box: 22 skill-quarantine tests red on "spawnSync zip ENOENT", the
// code under test fine). python3 is always there. Same archive shape as -r -y:
// a directory entry per directory, file modes kept (an executable stays one),
// a symlink stored as a link (its target as the data), not followed.
const PY = `
import os, stat, sys, zipfile
out, root = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for cur, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        rel = os.path.relpath(cur, root)
        names = [(d, True) for d in dirs] + [(f, False) for f in files]
        for name, is_dir in names:
            full = os.path.join(cur, name)
            arc = name if rel == '.' else os.path.join(rel, name)
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                zi = zipfile.ZipInfo(arc)
                zi.create_system = 3
                zi.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(zi, os.readlink(full))
            elif is_dir:
                zi = zipfile.ZipInfo(arc + '/')
                zi.create_system = 3
                zi.external_attr = (st.st_mode << 16) | 0x10
                z.writestr(zi, b'')
            else:
                z.write(full, arc)
`

export function zipDir(dir: string, out: string): void {
  execFileSync('python3', ['-c', PY, out, dir])
}
