"""Install the pinned official CLI locally, no global npm or shared config changes."""
import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path
URL='https://registry.npmjs.org/@fly-ai/flyai-cli/-/flyai-cli-1.0.16.tgz'

def main():
    raw=urllib.request.urlopen(URL,timeout=30).read()
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        source=archive.extractfile('package/dist/flyai-bundle.cjs').read()
    if hashlib.sha256(source).hexdigest()!='194a66eb84094f3d8ee20fac0a2d6cae10a405cd59ac100b7e7880ebc97297da':
        raise RuntimeError('Official CLI content does not match the reviewed version')
    target=Path(__file__).resolve().parents[1]/'.runtime/flyai/flyai.cjs'
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source)
    print('Installed FlyAI CLI 1.0.16:',target,'sha256:',hashlib.sha256(source).hexdigest())

if __name__=='__main__':main()
