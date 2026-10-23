/** Blank app surfaces should not expose browser navigation/developer menus. */
export function preserveTextContextMenu(target: EventTarget | null, selectedText: string): boolean {
  return target instanceof Element && Boolean(target.closest('input, textarea, [contenteditable="true"]')) || selectedText.trim().length > 0;
}

export function installContextMenuPolicy(document: Document): () => void {
  const handle = (event: MouseEvent) => {
    if (!preserveTextContextMenu(event.target, document.getSelection()?.toString() ?? '')) event.preventDefault();
  };
  document.addEventListener('contextmenu', handle);
  return () => document.removeEventListener('contextmenu', handle);
}
