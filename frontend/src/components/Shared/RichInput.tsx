import { useEffect, useRef, useState } from 'react';
import { Button, Image, Tooltip, message } from 'antd';
import { BoldOutlined, ItalicOutlined, OrderedListOutlined, UnorderedListOutlined, UndoOutlined, RedoOutlined, DeleteOutlined } from '@ant-design/icons';
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, generateJSON, useEditor } from '@tiptap/react';
import type { Editor, JSONContent, NodeViewProps } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import TiptapImage from '@tiptap/extension-image';
import Placeholder from '@tiptap/extension-placeholder';
import CharacterCount from '@tiptap/extension-character-count';
import DOMPurify from 'dompurify';
import type { RichInputValue } from '../../types';
import { editorContent, serializeEditor } from '../../utils/richInput';
import { IMAGE_TYPES, MAX_IMAGES, MAX_IMAGE_BYTES, readDataUrl } from '../../utils/images';

function InlineScreenshot({ node, selected, deleteNode, editor }: NodeViewProps) {
  return <NodeViewWrapper className={`rich-input-image${selected ? ' is-selected' : ''}`} contentEditable={false}>
    <Image src={node.attrs.src} alt="粘贴的复现截图" style={{ maxHeight: 240, objectFit: 'contain' }} />
    {editor.isEditable && <Button size="small" icon={<DeleteOutlined />} aria-label="删除这张截图" onClick={() => { if (editor.isEditable) deleteNode(); }} />}
  </NodeViewWrapper>;
}

const Screenshot = TiptapImage.extend({
  parseHTML() { return [{ tag: 'img[src^="data:image/"]' }]; },
  addNodeView() { return ReactNodeViewRenderer(InlineScreenshot); },
}).configure({ allowBase64: true });

function imageSources(content: JSONContent): string[] {
  return content.type === 'image' ? [String(content.attrs?.src || '')] : (content.content || []).flatMap(imageSources);
}

function validateImageSources(images: string[]) {
  for (const src of images) {
    if (!/^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$/.test(src)) throw new Error('截图仅支持 PNG、JPEG、WebP 或 GIF');
    const encoded = src.slice(src.indexOf(',') + 1);
    const bytes = encoded.length * 3 / 4 - (encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0);
    if (bytes > MAX_IMAGE_BYTES) throw new Error('单张截图不能超过 5MB');
  }
}

interface Props {
  id: string;
  value: RichInputValue;
  onChange: (value: RichInputValue) => void;
  placeholder: string;
  maxLength: number;
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
}

export default function RichInput({ id, value, onChange, placeholder, maxLength, disabled = false, onBusyChange }: Props) {
  const callbacks = useRef({ onChange, onBusyChange, disabled });
  callbacks.current = { onChange, onBusyChange, disabled };
  const [reading, setReading] = useState(false);
  const [characters, setCharacters] = useState(0);
  const [imageCount, setImageCount] = useState(value.images.length);
  const [, refreshToolbar] = useState(0);
  const editorRef = useRef<Editor | null>(null);
  const pending = useRef(false);
  const lastValue = useRef(JSON.stringify(value));

  const insertImages = async (editor: Editor, files: File[], position?: number) => {
    if (pending.current || callbacks.current.disabled) return;
    try {
      if (files.some((file) => !IMAGE_TYPES.includes(file.type))) throw new Error('截图仅支持 PNG、JPEG、WebP 或 GIF');
      if (files.some((file) => file.size > MAX_IMAGE_BYTES)) throw new Error('单张截图不能超过 5MB');
      if (imageSources(editor.getJSON()).length + files.length > MAX_IMAGES) throw new Error(`最多粘贴 ${MAX_IMAGES} 张截图`);
      const selection = editor.state.selection;
      const range = position === undefined ? { from: selection.from, to: selection.to } : { from: position, to: position };
      pending.current = true;
      setReading(true);
      callbacks.current.onBusyChange?.(true);
      editor.setEditable(false, false);
      const images = await Promise.all(files.map(readDataUrl));
      validateImageSources(images);
      if (!editor.isDestroyed) editor.chain().focus().insertContentAt(range, images.map((src) => ({ type: 'image', attrs: { src } }))).run();
    } catch (err) { message.warning(err instanceof Error ? err.message : '读取图片失败，请重新粘贴'); }
    finally {
      pending.current = false;
      if (!editor.isDestroyed) {
        editor.setEditable(!callbacks.current.disabled, false);
        setReading(false);
        callbacks.current.onBusyChange?.(false);
      }
    }
  };

  const editor = useEditor({
    extensions: [StarterKit.configure({ heading: false, horizontalRule: false, link: false, underline: false }), Screenshot,
      Placeholder.configure({ placeholder }), CharacterCount.configure({ limit: maxLength })],
    content: editorContent(value),
    immediatelyRender: true,
    editorProps: {
      attributes: { id, role: 'textbox', 'aria-multiline': 'true', 'aria-label': '文字和截图输入', spellcheck: 'false' },
      handlePaste: (_view, event) => {
        if (pending.current || callbacks.current.disabled) return true;
        const current = editorRef.current;
        if (!current) return false;
        const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith('image/'));
        if (files.length) { event.preventDefault(); void insertImages(current, files); return true; }
        const html = event.clipboardData?.getData('text/html');
        if (!html) return false;
        event.preventDefault();
        const sanitized = DOMPurify.sanitize(html, {
          ALLOWED_TAGS: ['p', 'div', 'br', 'strong', 'b', 'em', 'i', 's', 'strike', 'code', 'pre', 'ul', 'ol', 'li', 'blockquote', 'img'],
          ALLOWED_ATTR: ['src', 'start'],
        });
        const parsed = new DOMParser().parseFromString(sanitized, 'text/html');
        const remoteImages = parsed.querySelectorAll('img:not([src^="data:image/"])');
        if (remoteImages.length) {
          remoteImages.forEach((image) => image.remove());
          message.warning('网页图片未携带图片内容，请复制图片或截图后直接粘贴');
        }
        const content = generateJSON(parsed.body.innerHTML, current.extensionManager.extensions);
        try {
          const sources = imageSources(content);
          validateImageSources(sources);
          if (imageSources(current.getJSON()).length + sources.length > MAX_IMAGES) throw new Error(`最多粘贴 ${MAX_IMAGES} 张截图`);
          current.chain().focus().insertContent(content.content || []).run();
        } catch (err) { message.warning(err instanceof Error ? err.message : '无法粘贴这段内容'); }
        return true;
      },
      handleDrop: (view, event, _slice, moved) => {
        if (pending.current || callbacks.current.disabled) return true;
        if (moved) return false;
        const files = Array.from(event.dataTransfer?.files || []);
        if (!files.length) return false;
        event.preventDefault();
        const position = view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos;
        if (editorRef.current) void insertImages(editorRef.current, files, position);
        return true;
      },
    },
    onUpdate: ({ editor }) => {
      const next = serializeEditor(editor.getJSON());
      lastValue.current = JSON.stringify(next);
      setCharacters(editor.storage.characterCount.characters());
      setImageCount(imageSources(editor.getJSON()).length);
      callbacks.current.onChange(next);
    },
    onTransaction: () => refreshToolbar((version) => version + 1),
  }, []);
  editorRef.current = editor;

  useEffect(() => {
    if (!editor) return;
    const next = JSON.stringify(value);
    if (next !== lastValue.current) {
      editor.commands.setContent(editorContent(value), { emitUpdate: false });
      lastValue.current = next;
    }
    setCharacters(editor.storage.characterCount.characters());
    setImageCount(imageSources(editor.getJSON()).length);
  }, [editor, value]);

  useEffect(() => { editor?.setEditable(!disabled && !reading, false); }, [editor, disabled, reading]);

  if (!editor) return null;
  const controls = [
    { label: '加粗', icon: <BoldOutlined />, active: editor.isActive('bold'), run: () => editor.chain().focus().toggleBold().run() },
    { label: '斜体', icon: <ItalicOutlined />, active: editor.isActive('italic'), run: () => editor.chain().focus().toggleItalic().run() },
    { label: '无序列表', icon: <UnorderedListOutlined />, active: editor.isActive('bulletList'), run: () => editor.chain().focus().toggleBulletList().run() },
    { label: '有序列表', icon: <OrderedListOutlined />, active: editor.isActive('orderedList'), run: () => editor.chain().focus().toggleOrderedList().run() },
    { label: '撤销', icon: <UndoOutlined />, active: false, unavailable: !editor.can().undo(), run: () => editor.chain().focus().undo().run() },
    { label: '重做', icon: <RedoOutlined />, active: false, unavailable: !editor.can().redo(), run: () => editor.chain().focus().redo().run() },
  ];
  return <div className={`rich-input${disabled || reading ? ' is-disabled' : ''}`}>
    <div className="rich-input-toolbar" role="toolbar" aria-label="文字格式">
      {controls.map((control) => <Tooltip title={control.label} key={control.label}>
        <Button type={control.active ? 'primary' : 'text'} size="small" icon={control.icon} aria-label={control.label} aria-pressed={control.active}
          disabled={disabled || reading || control.unavailable} onMouseDown={(event) => event.preventDefault()} onClick={control.run} />
      </Tooltip>)}
    </div>
    <EditorContent editor={editor} />
    <div className="rich-input-footer"><span>{reading ? '正在粘贴图片…' : '直接粘贴文字或截图，支持拖入图片'}</span><span>{characters}/{maxLength} 字 · {imageCount}/{MAX_IMAGES} 张图</span></div>
  </div>;
}
