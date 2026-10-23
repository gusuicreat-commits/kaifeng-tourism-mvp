"""Run in a local terminal. Hidden input, server-side .env only, never starts a provider."""
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
    key = getpass.getpass('粘贴高德 Web服务 Key（不回显；不是 Web端 Key）：').strip()
    if not re.fullmatch(r'[a-fA-F0-9]{32}', key):
        raise SystemExit('格式不符合预期，未保存；请在高德控制台检查服务平台类型。')
    values = dotenv_values(path) if path.exists() else {}
    if key in (values.get('VITE_AMAP_KEY'), values.get('VITE_AMAP_SECURITY_CODE')):
        raise SystemExit('与 Web端凭据相同，未保存；需要单独的 Web服务 Key。')
    content = path.read_text() if path.exists() else ''
    pattern = r'(?m)^\s*(?:export\s+)?AMAP_WEB_SERVICE_KEY\s*=.*$'
    line = 'AMAP_WEB_SERVICE_KEY=' + key
    content = re.sub(pattern, lambda _: line, content) if re.search(pattern, content) else content.rstrip('\n') + '\n' + line + '\n'
    fd, name = tempfile.mkstemp(prefix='.amap-env-', dir=ROOT)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print('已保存到本机 .env（0600）；未验证有效性，未启用正式 Provider。')


if __name__ == '__main__':
    main()
