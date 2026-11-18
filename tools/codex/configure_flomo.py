#!/usr/bin/env python3
"""Local hidden-input setup; no note is written during configuration."""
import getpass
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path.home() / '.config' / 'stackchan'
STATUS = ROOT / 'flomo-setup-status.json'

def decode(body):
    if body.lstrip().startswith('{'):
        return json.loads(body)
    return next(json.loads(line[5:].strip()) for line in body.splitlines()
                if line.startswith('data:') and line[5:].strip())

def main():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous = ROOT / 'flomo.json'
    config = json.loads(previous.read_text()) if previous.exists() else {}
    print('配置 StackChan flomo。输入均隐藏；本次只验证连接，不会创建或修改笔记。')
    url = getpass.getpass('粘贴传统 API URL（回车保留/跳过）: ').strip()
    if url:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'flomoapp.com' or not parsed.path.startswith('/iwh/'):
            raise ValueError('Expected official HTTPS incoming webhook URL')
        config['api_url'] = url
    print('读取需要 MCP Token，可从 flomo 设置 → MCP / API → MCP → 添加其他应用 → Token 连接创建。')
    token = getpass.getpass('粘贴 MCP Token（回车保留/暂时跳过读取）: ').strip()
    if token:
        if any(char.isspace() for char in token):
            raise ValueError('Invalid token format')
        config['mcp_token'] = token
    schemas = []
    if config.get('mcp_token'):
        headers = {'Authorization': 'Bearer ' + config['mcp_token'], 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream'}
        def rpc(method, params, number):
            payload = {'jsonrpc': '2.0', 'method': method, 'params': params}
            if number is not None:
                payload['id'] = number
            req = Request('https://flomoapp.com/mcp', data=json.dumps(payload).encode(), headers=headers)
            with urlopen(req, timeout=25) as response:
                session = response.headers.get('Mcp-Session-Id')
                if session:
                    headers['Mcp-Session-Id'] = session
                body = response.read().decode()
            if not body.strip():
                return {}
            result = decode(body)
            if result.get('error'):
                raise RuntimeError('MCP request rejected')
            return result.get('result', {})
        print('正在验证 MCP Token，只获取工具定义……')
        init = rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                  'clientInfo': {'name': 'stackchan-setup', 'version': '1.0'}}, 1)
        headers['MCP-Protocol-Version'] = init.get('protocolVersion', '2024-11-05')
        rpc('notifications/initialized', {}, None)
        schemas = rpc('tools/list', {}, 2).get('tools', [])
        names = {tool['name'] for tool in schemas}
        if not {'memo_create', 'memo_search', 'memo_batch_get'} <= names:
            raise RuntimeError('Required memo tools unavailable')
    if not config.get('api_url') and not config.get('mcp_token'):
        raise ValueError('No credentials entered')
    fd, temporary = tempfile.mkstemp(dir=ROOT, prefix='.flomo-')
    with os.fdopen(fd, 'w') as stream:
        json.dump(config, stream)
    os.chmod(temporary, 0o600)
    os.replace(temporary, previous)
    if schemas:
        (ROOT / 'flomo-tool-schemas.json').write_text(json.dumps(schemas, ensure_ascii=False, indent=2))
        os.chmod(ROOT / 'flomo-tool-schemas.json', 0o600)
    STATUS.write_text(json.dumps({'ok': True, 'write_configured': bool(config.get('api_url') or config.get('mcp_token')),
                                 'read_configured': bool(config.get('mcp_token')), 'tools': [tool['name'] for tool in schemas]}))
    os.chmod(STATUS, 0o600)
    print('保存成功。写入已配置；' + ('读取连接验证成功。' if schemas else '读取等待 MCP Token。'))
    print('请返回 Codex 告知完成。请勿发送凭据。')

if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print('已取消。')
    except Exception as error:  # noqa: BLE001 - never print credential-bearing exception messages
        print('配置未完成：', type(error).__name__, '。未显示任何凭据，请返回 Codex 排查。')
