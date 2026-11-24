# Corre na rig: vai buscar ao arquivo oficial do Ubuntu 24.04 as bibliotecas de sistema mais novas
# (glibc, libstdc++, libgcc, libgomp) e extrai-as para uma pasta à parte. Não toca no sistema.
import hashlib
import lzma
import os
import subprocess
import sys
import urllib.request

BASE = 'http://archive.ubuntu.com/ubuntu/'
WANT = {'libc6', 'libstdc++6', 'libgcc-s1', 'libgomp1'}
home = os.environ.get('RIG_HOME') or os.path.expanduser('~/meister-rig')
target = os.path.join(home, 'noble')
debs = os.path.join(home, 'dl')
os.makedirs(target, exist_ok=True)

found = {}
for suite in ('noble', 'noble-updates'):  # a de atualizações fica por cima
    raw = urllib.request.urlopen(BASE + 'dists/%s/main/binary-amd64/Packages.xz' % suite, timeout=60).read()
    for block in lzma.decompress(raw).decode('utf-8', 'replace').split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.split('\n') if ': ' in line and not line.startswith(' '))
        if fields.get('Package') in WANT:
            found[fields['Package']] = (fields['Version'], fields['Filename'], fields['SHA256'])

for name in sorted(WANT):
    version, filename, digest = found[name]
    path = os.path.join(debs, os.path.basename(filename))
    data = urllib.request.urlopen(BASE + filename, timeout=120).read()
    if hashlib.sha256(data).hexdigest() != digest:
        sys.exit('SHA-256 errado: ' + name)
    open(path, 'wb').write(data)
    subprocess.check_call(['dpkg-deb', '-x', path, target])
    print(name, version, len(data) // 1024, 'KB', 'ok')
