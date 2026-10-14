"""共享截图校验和内容寻址存储；沿用已有目录以兼容历史验收截图。"""
import base64
import binascii
import hashlib
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

_DATA_URL = re.compile(r'^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/]+={0,2})$')
_MAGIC = {'image/png': (b'\x89PNG\r\n\x1a\n',), 'image/jpeg': (b'\xff\xd8\xff',), 'image/gif': (b'GIF87a', b'GIF89a'), 'image/webp': (b'RIFF',)}
_EXT = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif'}
_MIME = {ext: mime for mime, ext in _EXT.items()}
_NAME = re.compile(r'^[0-9a-f]{64}\.(png|jpg|webp|gif)$')


def validate_images(images, *, max_images=4, max_bytes=5_000_000):
    """请求体校验入口：返回 [(文件名, 字节, mime)]，格式或大小不合法时抛 ValueError。"""
    if len(images) > max_images:
        raise ValueError(f'最多附带 {max_images} 张截图')
    parsed = []
    for image in images:
        match = _DATA_URL.match(image)
        if not match:
            raise ValueError('截图必须是 PNG、JPEG、WebP 或 GIF 图片')
        mime = match.group(1)
        try:
            content = base64.b64decode(match.group(2), validate=True)
        except binascii.Error as exc:
            raise ValueError('截图编码无效') from exc
        if len(content) > max_bytes:
            raise ValueError(f'单张截图不能超过 {max_bytes // 1_000_000}MB')
        if not content.startswith(_MAGIC[mime]) or (mime == 'image/webp' and content[8:12] != b'WEBP'):
            raise ValueError('截图内容与图片格式不符')
        name = f'{hashlib.sha256(content).hexdigest()}.{_EXT[mime]}'
        if all(name != item[0] for item in parsed):
            parsed.append((name, content, mime))
    return parsed


def _directory(db_path):
    return Path(db_path).resolve().parent / 'feedback_images'


def save_images(db_path, images, **limits):
    """保存已校验的截图，返回文件名列表。"""
    parsed = validate_images(images or [], **limits)
    directory = _directory(db_path)
    if parsed:
        directory.mkdir(parents=True, exist_ok=True)
    for name, content, _ in parsed:
        path = directory / name
        if not path.exists():
            # 完成写入后再原子替换，其他请求不会读到半张图片。
            temporary = NamedTemporaryFile(dir=directory, delete=False)
            temporary_path = Path(temporary.name)
            try:
                with temporary:
                    temporary.write(content)
                temporary_path.replace(path)
            finally:
                temporary_path.unlink(missing_ok=True)
    return [name for name, _, _ in parsed]


def _read_data_url(directory, name):
    if not _NAME.match(name):
        return None
    path = directory / name
    if not path.is_file():
        return None
    mime = _MIME[name.rsplit('.', 1)[1]]
    return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')


def load_images(db_path, names):
    """快照证据缺失时明确失败，不允许静默丢失截图后继续修复。"""
    loaded = [_read_data_url(_directory(db_path), name) for name in names]
    if any(image is None for image in loaded):
        raise ValueError('已保存的截图文件缺失，请重新上传截图后执行修复')
    return loaded
