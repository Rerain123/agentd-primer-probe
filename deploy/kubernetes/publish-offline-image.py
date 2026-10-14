"""Append a reviewed source layer to a pinned image using Registry V2.

No build daemon or external builder image is needed. Registry credentials stay
in the mounted Kubernetes secret and are never written to the layer or logs.
"""
import base64
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOST = 'harbor-core.base.svc.cluster.local'
BASE = 'http://' + HOST
REPO = 'qcyz/oligosmith'
SOURCE = 'sha256:a4654140604d08a9b0d60e05a13471375a61ed8600d935a997966c8740df1eec'
TAG = os.environ.get('OLIGOSMITH_RELEASE_TAG', 'offline-20261012-r02')
if not re.fullmatch(r'offline-\d{8}-r\d+', TAG):
    raise ValueError('Invalid versioned offline release tag')
auths = json.loads(Path('/registry/config.json').read_text())['auths']
auth = next(v for k,v in auths.items() if HOST in k)
basic = auth.get('auth') or base64.b64encode((auth['username']+':'+auth['password']).encode()).decode()
token = None


def request(path, data=None, method='GET', headers=None):
    global token
    url = urllib.parse.urljoin(BASE, path)
    if urllib.parse.urlparse(url).netloc != HOST:
        raise RuntimeError('Unexpected registry redirect host')
    hdr = dict(headers or {})
    if token:
        hdr['Authorization'] = 'Bearer '+token
    try:
        return urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=hdr),timeout=120)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
        challenge = dict(re.findall(r'(\w+)="([^"]*)"', error.headers.get('WWW-Authenticate','')))
        realm = challenge['realm']
        # Harbor advertises its public URL. Use the same service's token path
        # on the already selected internal registry, never an arbitrary host.
        token_url = BASE + urllib.parse.urlparse(realm).path + '?' + urllib.parse.urlencode({'service':challenge['service'],'scope':'repository:'+REPO+':pull,push'})
        with urllib.request.urlopen(urllib.request.Request(token_url,headers={'Authorization':'Basic '+basic}),timeout=30) as response:
            credential = json.load(response)
        token = credential.get('token') or credential['access_token']
        hdr['Authorization']='Bearer '+token
        return urllib.request.urlopen(urllib.request.Request(url,data=data,method=method,headers=hdr),timeout=120)


def digest(data):
    return 'sha256:'+hashlib.sha256(data).hexdigest()


def blob(data):
    sha = digest(data)
    with request('/v2/'+REPO+'/blobs/uploads/', data=b'', method='POST') as response:
        location = response.headers['Location']
    with request(location + ('&' if '?' in location else '?')+'digest='+sha, data=data, method='PUT', headers={'Content-Type':'application/octet-stream'}) as response:
        assert response.status == 201
    return sha


accept = 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'
with request('/v2/'+REPO+'/manifests/'+SOURCE,headers={'Accept':accept}) as response:
    raw=response.read()
assert digest(raw) == SOURCE
manifest=json.loads(raw)
with request('/v2/'+REPO+'/blobs/'+manifest['config']['digest']) as response:
    raw=response.read()
assert digest(raw) == manifest['config']['digest']
config=json.loads(raw)
assert config['os']=='linux' and config['architecture']=='amd64'
stream=io.BytesIO()
with tarfile.open(fileobj=stream,mode='w') as archive:
    for folder in ('src','config','schemas','skills'):
        for path in sorted((Path('/context')/folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                info=archive.gettarinfo(str(path),arcname='app/'+path.relative_to('/context').as_posix())
                info.uid=info.gid=0;info.uname=info.gname='root';info.mode=0o644
                with path.open('rb') as handle:
                    archive.addfile(info,handle)
layer=stream.getvalue()
compressed=gzip.compress(layer,mtime=0)
layer_digest=blob(compressed)
config['rootfs']['diff_ids'].append(digest(layer))
config.setdefault('history',[]).append({'created_by':'OligoSmith reviewed source overlay '+TAG})
config.setdefault('config',{}).setdefault('Labels',{})['org.opencontainers.image.revision']=TAG
raw=json.dumps(config,separators=(',',':')).encode()
manifest['config'].update(digest=blob(raw),size=len(raw))
manifest['layers'].append({'mediaType':'application/vnd.docker.image.rootfs.diff.tar.gzip','size':len(compressed),'digest':layer_digest})
raw=json.dumps(manifest,separators=(',',':')).encode()
with request('/v2/'+REPO+'/manifests/'+TAG,data=raw,method='PUT',headers={'Content-Type':manifest['mediaType']}) as response:
    assert response.status==201
with request('/v2/'+REPO+'/manifests/'+TAG,headers={'Accept':accept}) as response:
    assert digest(response.read())==digest(raw)
result={'image':HOST+'/'+REPO+'@'+digest(raw),'tag':TAG,'base':SOURCE,'source_layer':layer_digest}
Path('/tmp/image-result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
