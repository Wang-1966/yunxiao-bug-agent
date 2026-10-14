import type { JSONContent } from '@tiptap/react';
import type { RichInputNode, RichInputValue } from '../types';

export function richText(node: RichInputNode): string {
  if (node.type === 'text') return node.text || '';
  if (node.type === 'image') return '';
  if (node.type === 'hardBreak') return '\n';
  return (node.content || []).map(richText).join(['paragraph', 'codeBlock'].includes(node.type) ? '' : '\n');
}

export function editorContent(value: RichInputValue): JSONContent {
  const inflate = (node: RichInputNode): JSONContent => node.type === 'image'
    ? { type: 'image', attrs: { src: value.images[node.attrs?.image_index ?? -1] } }
    : { ...node, ...(node.content ? { content: node.content.map(inflate) } : {}) };
  if (value.document) return inflate(value.document);
  return { type: 'doc', content: [
    ...value.text.split('\n').map((text) => ({ type: 'paragraph', ...(text ? { content: [{ type: 'text', text }] } : {}) })),
    ...value.images.map((src) => ({ type: 'image', attrs: { src } })),
  ] };
}

export function serializeEditor(content: JSONContent): RichInputValue {
  const images: string[] = [];
  const visit = (node: JSONContent): RichInputNode => {
    const type = node.type || 'paragraph';
    if (type === 'image') {
      const src = String(node.attrs?.src || '');
      let index = images.indexOf(src);
      if (index < 0) { index = images.length; images.push(src); }
      return { type, attrs: { image_index: index } };
    }
    return {
      type,
      ...(node.text !== undefined ? { text: node.text } : {}),
      ...(type === 'orderedList' ? { attrs: { start: node.attrs?.start || 1 } } : {}),
      ...(node.marks?.length ? { marks: node.marks.map((mark) => ({ type: mark.type })) } : {}),
      ...(node.content ? { content: node.content.map(visit) } : ['paragraph', 'doc', 'listItem', 'bulletList', 'orderedList', 'blockquote', 'codeBlock'].includes(type) ? { content: [] } : {}),
    };
  };
  const document = visit(content);
  return { text: richText(document).trim(), images, document };
}
