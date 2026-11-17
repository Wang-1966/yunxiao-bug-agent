"""生成本机 Langfuse 凭据并写入后端配置；重复运行保留已有服务凭据。"""
import os
from pathlib import Path
import secrets


def setup(directory=None, backend_env=None):
    directory = directory or Path(__file__).resolve().parent
    backend_env = backend_env or directory.parents[1] / 'backend' / '.env'
    path = directory / '.env'
    if not path.exists():
        values = {key: secrets.token_hex(32) for key in (
            'POSTGRES_PASSWORD', 'CLICKHOUSE_PASSWORD', 'REDIS_PASSWORD', 'MINIO_PASSWORD',
            'SALT', 'ENCRYPTION_KEY', 'NEXTAUTH_SECRET', 'LANGFUSE_ADMIN_PASSWORD')}
        values.update(LANGFUSE_PUBLIC_KEY='pk-lf-' + secrets.token_hex(16),
                      LANGFUSE_SECRET_KEY='sk-lf-' + secrets.token_hex(32),
                      LANGFUSE_ADMIN_EMAIL='admin@yunxiao.local')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(''.join(f'{key}={value}\n' for key, value in values.items()))
    else:
        values = dict(line.split('=', 1) for line in path.read_text().splitlines() if line and not line.startswith('#'))
    required = ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_ADMIN_EMAIL', 'LANGFUSE_ADMIN_PASSWORD')
    if not all(values.get(key) for key in required):
        raise ValueError('Langfuse .env 缺少项目或管理员凭据，请补齐后重试')
    config = {'LANGFUSE_ENABLED': 'true', 'LANGFUSE_BASE_URL': 'http://localhost:3002',
              'LANGFUSE_PUBLIC_KEY': values['LANGFUSE_PUBLIC_KEY'], 'LANGFUSE_SECRET_KEY': values['LANGFUSE_SECRET_KEY'],
              'LANGFUSE_ENVIRONMENT': 'local'}
    lines = backend_env.read_text().splitlines() if backend_env.exists() else []
    lines = [line for line in lines if line.partition('=')[0].strip().removeprefix('export ') not in config]
    fd = os.open(backend_env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write('\n'.join(lines).rstrip() + '\n' + ''.join(f'{key}={value}\n' for key, value in config.items()))
    os.chmod(backend_env, 0o600)


if __name__ == '__main__':
    setup()
    print('本地配置已生成。管理员账号及密码保存在 infra/langfuse/.env；后端重启后启用监控。')
