"""统一图文输入：保存受限编辑器文档，图片仅记录数组索引，Agent 使用图文顺序文本。"""
import json

from .image_storage import save_images, validate_images

_CHILDREN = {
    'doc': {'paragraph', 'bulletList', 'orderedList', 'blockquote', 'codeBlock', 'image'},
    'paragraph': {'text', 'hardBreak'},
    'bulletList': {'listItem'}, 'orderedList': {'listItem'},
    'listItem': {'paragraph', 'bulletList', 'orderedList', 'blockquote', 'codeBlock', 'image'},
    'blockquote': {'paragraph', 'bulletList', 'orderedList', 'codeBlock', 'image'},
    'codeBlock': {'text'},
}


def normalize_document(document, image_map, max_length):
    if len(json.dumps(document, ensure_ascii=False)) > 200_000:
        raise ValueError('图文内容过大')
    count, characters, referenced = 0, 0, set()

    def visit(node, depth=0):
        nonlocal count, characters
        count += 1
        if count > 1500 or depth > 12 or not isinstance(node, dict):
            raise ValueError('图文内容结构无效或层级过深')
        kind = node.get('type')
        attrs = node.get('attrs') or {}
        if not isinstance(attrs, dict):
            raise ValueError('图文内容包含无效属性')
        if kind == 'text':
            text = node.get('text')
            if not isinstance(text, str) or not text:
                raise ValueError('图文内容包含无效文字')
            characters += len(text)
            result = {'type': kind, 'text': text}
            marks = node.get('marks', [])
            if not isinstance(marks, list) or any(not isinstance(mark, dict) or mark.get('type') not in ('bold', 'italic', 'strike', 'code') for mark in marks):
                raise ValueError('图文内容包含不支持的文字格式')
            if marks:
                result['marks'] = [{'type': mark['type']} for mark in marks]
            return result
        if kind == 'image':
            index = attrs.get('image_index')
            if type(index) is not int or index < 0 or index >= len(image_map):
                raise ValueError('图文中的截图引用无效')
            referenced.add(index)
            return {'type': kind, 'attrs': {'image_index': image_map[index]}}
        if kind == 'hardBreak':
            characters += 1
            return {'type': kind}
        if kind not in _CHILDREN or (kind == 'doc' and depth != 0):
            raise ValueError('图文内容包含不支持的节点')
        children = node.get('content', [])
        if not isinstance(children, list) or any(not isinstance(child, dict) or child.get('type') not in _CHILDREN[kind] for child in children):
            raise ValueError('图文内容结构无效')
        result = {'type': kind, 'content': [visit(child, depth + 1) for child in children]}
        if kind == 'orderedList':
            start = attrs.get('start', 1)
            if type(start) is not int or not 1 <= start <= 1_000_000:
                raise ValueError('有序列表起始编号无效')
            result['attrs'] = {'start': start}
        return result

    if not isinstance(document, dict) or document.get('type') != 'doc':
        raise ValueError('图文内容必须是有效文档')
    normalized = visit(document)
    if characters > max_length:
        raise ValueError(f'文字不能超过 {max_length} 字')
    if referenced != set(range(len(image_map))):
        raise ValueError('存在未关联到图文内容的截图')
    return normalized


def document_text(node):
    kind = node['type']
    if kind == 'text':
        return node['text']
    if kind == 'image':
        return f"【本条信息的第 {node['attrs']['image_index'] + 1} 张截图】"
    if kind == 'hardBreak':
        return '\n'
    children = node.get('content', [])
    if kind in ('bulletList', 'orderedList'):
        start = node.get('attrs', {}).get('start', 1)
        return '\n'.join(f"{start + index}. {document_text(child)}" if kind == 'orderedList' else f'- {document_text(child)}' for index, child in enumerate(children))
    return ('' if kind in ('paragraph', 'codeBlock') else '\n').join(document_text(child) for child in children)


def remap_document_images(node, image_map):
    """读取时缺图用文字占位，其余图片引用按返回数组重新编号。"""
    if node['type'] == 'image':
        index = image_map.get(node['attrs']['image_index'])
        if index is None:
            return {'type': 'paragraph', 'content': [{'type': 'text', 'text': '（截图文件缺失，请在此处重新粘贴）'}]}
        return {'type': 'image', 'attrs': {'image_index': index}}
    return {**node, **({'content': [remap_document_images(child, image_map) for child in node['content']]} if 'content' in node else {})}


def save_rich_input(db_path, text, images, document, *, max_length):
    """旧请求继续支持 text+images；新文档以自身内容为准，不信任客户端文字摘要。"""
    if document is None:
        return text.strip(), save_images(db_path, images), None
    # 先校验文档和全部图片，再落盘；重复图在文档中的位置仍保留。
    names = []
    image_map = []
    validate_images(images)
    for image in images:
        name = validate_images([image])[0][0]
        if name not in names:
            names.append(name)
        image_map.append(names.index(name))
    normalized = normalize_document(document, image_map, max_length)
    save_images(db_path, images)
    return document_text(normalized).strip(), names, normalized
