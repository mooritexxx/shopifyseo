import Image from "@tiptap/extension-image";
import Placeholder from "@tiptap/extension-placeholder";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Bold,
  Heading2,
  Heading3,
  Italic,
  Link2,
  List,
  ListOrdered,
  Redo2,
  Strikethrough,
  Underline as UnderlineIcon,
  Undo2,
  Unlink
} from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import { Button } from "./button";
import { cn } from "../../lib/utils";

export type RichBodyEditorProps = {
  /** Stored as HTML (e.g. from Shopify). */
  value: string;
  onChange: (html: string) => void;
  placeholder?: string;
  /** For <label htmlFor> */
  id?: string;
  className?: string;
  disabled?: boolean;
};

function ToolbarButton({
  onClick,
  active,
  disabled,
  title,
  children
}: {
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  title: string;
  children: ReactNode;
}) {
  return (
    <Button
      type="button"
      variant={active ? "secondary" : "ghost"}
      size="sm"
      className={cn("h-8 w-8 shrink-0 p-0", active && "bg-slate-200/80")}
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-pressed={active}
    >
      {children}
    </Button>
  );
}

/**
 * WYSIWYG body editor — persists HTML compatible with Shopify descriptions / page bodies.
 */
export function RichBodyEditor({ value, onChange, placeholder, id, className, disabled }: RichBodyEditorProps) {
  const lastEmitted = useRef(value);

  const editor = useEditor({
    editable: !disabled,
    extensions: [
      StarterKit.configure({
        heading: { levels: [2, 3] },
        bulletList: { keepMarks: true, keepAttributes: false },
        orderedList: { keepMarks: true, keepAttributes: false },
        link: {
          openOnClick: false,
          HTMLAttributes: {
            class: "text-[#155eef] underline font-medium"
          }
        }
      }),
      Placeholder.configure({
        placeholder: placeholder ?? "Write your content…"
      }),
      Image.configure({
        inline: true,
        allowBase64: false,
        HTMLAttributes: {
          class:
            "max-h-[min(480px,70vh)] w-auto max-w-full rounded-lg border border-slate-200 align-middle"
        }
      })
    ],
    content: value || "<p></p>",
    editorProps: {
      attributes: {
        id: id ?? "",
        class: cn(
          "rendered-body-content min-h-[220px] max-w-none px-3 py-3 text-slate-700 outline-none",
          "prose-editor"
        ),
        "aria-label": placeholder ?? "Body content"
      }
    },
    onUpdate: ({ editor: ed }) => {
      const html = ed.getHTML();
      lastEmitted.current = html;
      onChange(html);
    }
  });

  // Sync when `value` changes from outside (navigation, AI regenerate, load) without fighting local typing.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    if (value === lastEmitted.current) return;
    editor.commands.setContent(value && value.trim() ? value : "<p></p>", { emitUpdate: false });
    lastEmitted.current = value;
  }, [value, editor]);

  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    editor.setEditable(!disabled);
  }, [disabled, editor]);

  if (!editor) {
    return (
      <div
        className={cn(
          "min-h-[280px] rounded-xl border border-[#dbe5f3] bg-white/80 animate-pulse",
          className
        )}
      />
    );
  }

  const setLink = () => {
    const previous = editor.getAttributes("link").href as string | undefined;
    const next = window.prompt("Link URL (https://…)", previous ?? "https://");
    if (next === null) return;
    const trimmed = next.trim();
    if (trimmed === "") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href: trimmed }).run();
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-[#dbe5f3] bg-[linear-gradient(180deg,#ffffff_0%,#fbfdff_100%)] shadow-sm",
        className
      )}
    >
      <div className="flex flex-wrap items-center gap-0.5 border-b border-[#e8eef6] bg-slate-50/90 px-2 py-1.5">
        <ToolbarButton
          title="Bold"
          active={editor.isActive("bold")}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleBold().run()}
        >
          <Bold size={16} />
        </ToolbarButton>
        <ToolbarButton
          title="Italic"
          active={editor.isActive("italic")}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        >
          <Italic size={16} />
        </ToolbarButton>
        <ToolbarButton
          title="Underline"
          active={editor.isActive("underline")}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleUnderline().run()}
        >
          <UnderlineIcon size={16} />
        </ToolbarButton>
        <ToolbarButton
          title="Strikethrough"
          active={editor.isActive("strike")}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleStrike().run()}
        >
          <Strikethrough size={16} />
        </ToolbarButton>
        <span className="mx-1 h-5 w-px bg-[#dbe5f3]" aria-hidden />
        <ToolbarButton
          title="Heading 2"
          active={editor.isActive("heading", { level: 2 })}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        >
          <Heading2 size={16} />
        </ToolbarButton>
        <ToolbarButton
          title="Heading 3"
          active={editor.isActive("heading", { level: 3 })}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
        >
          <Heading3 size={16} />
        </ToolbarButton>
        <span className="mx-1 h-5 w-px bg-[#dbe5f3]" aria-hidden />
        <ToolbarButton
          title="Bullet list"
          active={editor.isActive("bulletList")}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleBulletList().run()}
        >
          <List size={16} />
        </ToolbarButton>
        <ToolbarButton
          title="Numbered list"
          active={editor.isActive("orderedList")}
          disabled={disabled}
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
        >
          <ListOrdered size={16} />
        </ToolbarButton>
        <span className="mx-1 h-5 w-px bg-[#dbe5f3]" aria-hidden />
        <ToolbarButton title="Add link" disabled={disabled} onClick={setLink}>
          <Link2 size={16} />
        </ToolbarButton>
        <ToolbarButton
          title="Remove link"
          disabled={disabled || !editor.isActive("link")}
          onClick={() => editor.chain().focus().unsetLink().run()}
        >
          <Unlink size={16} />
        </ToolbarButton>
        <span className="mx-1 h-5 w-px bg-[#dbe5f3]" aria-hidden />
        <ToolbarButton title="Undo" disabled={disabled || !editor.can().undo()} onClick={() => editor.chain().focus().undo().run()}>
          <Undo2 size={16} />
        </ToolbarButton>
        <ToolbarButton title="Redo" disabled={disabled || !editor.can().redo()} onClick={() => editor.chain().focus().redo().run()}>
          <Redo2 size={16} />
        </ToolbarButton>
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
