/** Copy only after a user click. Never reads the clipboard or another frame. */
export async function copyCode(text: string): Promise<void> {
  let modernFailed = false;
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return; }
  } catch { modernFailed = true; }
  // Embedded WebViews can reject the async Clipboard API while still supporting
  // their explicit user-gesture copy command. Failure remains visible to the UI.
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
  const field = document.createElement('textarea');
  field.value = text;
  field.readOnly = true;
  field.tabIndex = -1;
  field.setAttribute('aria-hidden', 'true');
  field.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;';
  document.body.appendChild(field);
  try {
    field.select();
    if (!document.execCommand('copy')) throw new Error(modernFailed ? 'Clipboard permission denied.' : 'Copy command unavailable.');
  } finally {
    field.remove();
    previousFocus?.focus({ preventScroll: true });
    if (selection) { selection.removeAllRanges(); for (const range of ranges) selection.addRange(range); }
  }
}
