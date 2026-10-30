from pathlib import Path
import urllib.request as u,json,concurrent.futures as cf,hashlib,time,gzip,tarfile,subprocess
root=Path('/YunaGames/Android/redroid/download');root.mkdir(parents=True,exist_ok=True)
config='202754df4d668c2bd2467889ce74e25b69d366552f312391e0a1052a4e4ce605';layer='5c010910096953e774cdbb3687e00a315c8d59101fa240b3789d4274db03fe9b';size=646059401
data=(root/'config-source.json').read_bytes()
assert hashlib.sha256(data).hexdigest()==config
(root/(config+'.json')).write_bytes(data)
assert hashlib.sha256((root/'layer.gz').read_bytes()).hexdigest()==layer
h=hashlib.sha256()
with gzip.open(root/'layer.gz','rb') as src,(root/'layer.tar').open('wb') as dest:
 while b:=src.read(1048576):h.update(b);dest.write(b)
assert 'sha256:'+h.hexdigest() in json.loads(data)['rootfs']['diff_ids']
(root/'manifest.json').write_text(json.dumps([{'Config':config+'.json','RepoTags':['docker.m.daocloud.io/redroid/redroid:12.0.0_64only-latest'],'Layers':['layer.tar']}]))
print('VERIFIED_LOADING',flush=True)
proc=subprocess.Popen(['docker','-H','unix:///YunaGames/Android/redroid/run/docker.sock','load'],stdin=subprocess.PIPE)
with tarfile.open(fileobj=proc.stdin,mode='w|') as tar:
 for name in [config+'.json','manifest.json','layer.tar']:tar.add(root/name,arcname=name)
proc.stdin.close()
assert proc.wait()==0
(root/'layer.tar').unlink()
print('VERIFIED_LOADED',flush=True)
