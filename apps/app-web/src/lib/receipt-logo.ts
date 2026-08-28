'use client';

const ALLOWED_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const MAX_SOURCE_BYTES = 5_000_000;
export const MAX_RECEIPT_LOGO_BYTES = 200_000;

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('The selected image could not be read.'));
    image.src = url;
  });
}

function canvasBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob(
    (blob) => blob ? resolve(blob) : reject(new Error('The logo could not be compressed.')),
    'image/webp',
    quality,
  ));
}

function blobDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error('The compressed logo could not be read.'));
    reader.readAsDataURL(blob);
  });
}

/** Normalize uploads before they enter the branch settings JSON or audit path. */
export async function compressReceiptLogo(file: File): Promise<string> {
  if (!ALLOWED_TYPES.has(file.type)) throw new Error('Choose a PNG, JPEG, or WebP image.');
  if (file.size > MAX_SOURCE_BYTES) throw new Error('Choose an image smaller than 5 MB.');
  const objectUrl = URL.createObjectURL(file);
  try {
    const image = await loadImage(objectUrl);
    const scale = Math.min(1, 640 / image.naturalWidth, 320 / image.naturalHeight);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext('2d');
    if (!context) throw new Error('This browser cannot process the logo.');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    for (const quality of [0.88, 0.76, 0.64, 0.52, 0.4]) {
      const blob = await canvasBlob(canvas, quality);
      if (blob.size <= MAX_RECEIPT_LOGO_BYTES) return blobDataUrl(blob);
    }
    throw new Error('The logo remains larger than 200 KB after compression. Choose a simpler image.');
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export function validReceiptLogoUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && value.length <= 2048;
  } catch {
    return false;
  }
}
