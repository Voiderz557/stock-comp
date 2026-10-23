export const DEFAULT_SITE_ORIGIN = "https://stock-comp-private-preview.voider123.chatgpt.site";

// An unset override preserves the existing preview. An invalid explicit value
// fails before a build starts; never include its potentially sensitive contents
// in the error message.
export function canonicalSiteOrigin(value = DEFAULT_SITE_ORIGIN) {
  const invalid = () => new Error("NEXT_PUBLIC_SITE_URL must be an HTTPS origin without credentials, path, query, or fragment.");
  if (typeof value !== "string" || !/^https:\/\/[^/?#\\\s@]+\/?$/i.test(value)) throw invalid();
  let url;
  try {
    url = new URL(value);
  } catch {
    throw invalid();
  }
  if (url.protocol !== "https:" || !url.hostname || url.hostname.includes("*") || url.username || url.password) throw invalid();
  return url.origin;
}
