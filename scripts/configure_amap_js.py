"""Hidden local input for JS API credentials; never enables Web Service routing."""
import getpass
import os
from pathlib import Path
import re
import tempfile
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]

def main():
    path = ROOT / '.env'
    if path.is_symlink():
        raise SystemExit('拒绝写入符号链接 .env')
    values = dotenv_values(path) if path.exists() else {}
    key = getpass.getpass('高德 Web端 JS API Key（不回显）：').strip()
    code = getpass.getpass('与此 Key 配套的安全密钥（不回显）：').strip()
    if not all(re.fullmatch(r'[a-fA-F0-9]{32}', x) for x in (key, code)):
        raise SystemExit('格式不符合预期，未保存。')
    if key == values.get('AMAP_WEB_SERVICE_KEY'):
        raise SystemExit('不能使用 Web服务 Key，未保存。')
    content = path.read_text() if path.exists() else ''
    for name, value in [('AMAP_JS_KEY', key), ('AMAP_JS_SECURITY_CODE', code)]:
        pattern = rf'(?m)^\s*(?:export\s+)?{name}\s*=.*$'
        line = name + '=' + value
        content = re.sub(pattern, lambda _: line, content) if re.search(pattern, content) else content.rstrip('\n')+'\n'+line+'\n'
    fd, temp = tempfile.mkstemp(prefix='.amap-js-env-', dir=ROOT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    print('已保存本机 .env（0600）。安全密钥仅供后端代理使用；未重启服务。')

if __name__ == '__main__': main()
