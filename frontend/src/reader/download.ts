export function downloadFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  // Leave the browser time to start reading the Blob before releasing it.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
