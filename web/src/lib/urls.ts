// Los enlaces de ofertas vienen de fuentes externas: solo se enlaza HTTPS
// para no publicar javascript:, data: ni http: en href.
export function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}
