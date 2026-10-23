import assert from "node:assert/strict";
import test from "node:test";
import { canonicalSiteOrigin, DEFAULT_SITE_ORIGIN } from "../scripts/site-origin.mjs";

test("unset canonical origin preserves the existing preview", () => {
  assert.equal(canonicalSiteOrigin(), DEFAULT_SITE_ORIGIN);
  assert.equal(canonicalSiteOrigin(undefined), DEFAULT_SITE_ORIGIN);
});

test("HTTPS canonical overrides normalize the origin", () => {
  assert.equal(canonicalSiteOrigin("https://bizwax.org"), "https://bizwax.org");
  assert.equal(canonicalSiteOrigin("https://BIZWAX.org:443/"), "https://bizwax.org");
  assert.equal(canonicalSiteOrigin("https://preview.example.com:8443/"), "https://preview.example.com:8443");
});

test("invalid explicit origins fail without disclosing the supplied value", () => {
  for (const value of ["", " ", null, "bizwax.org", "http://bizwax.org", "//bizwax.org",
    "https://", "https://bizwax.org:99999", "https://*.bizwax.org", "https://bizwax.org/path",
    "https://bizwax.org/../", "https://bizwax.org?", "https://bizwax.org#",
    "https://user:private-token@bizwax.org", "https://@bizwax.org", "https://bizwax.org\\path",
    " https://bizwax.org", "https://bizwax.org\n", "https://biz\twax.org"]) {
    assert.throws(() => canonicalSiteOrigin(value), {
      message: "NEXT_PUBLIC_SITE_URL must be an HTTPS origin without credentials, path, query, or fragment.",
    });
  }
});
