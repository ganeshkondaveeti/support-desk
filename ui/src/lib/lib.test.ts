import { describe, expect, it } from "vitest";

import { countSentences, daysSince, formatDate, formatDateTime, formatWindow, formatWindowString, parseDay } from "./format";
import { isAllowedUrl, normalizeHex, payloadMatchesHash, sha256Hex, splitRedactions, urlLabel } from "./safe";

const policy = {
  publicDomains: ["groww.in", "amfiindia.com", "sebi.gov.in", "hdfcfund.com"],
  eduUrls: ["https://www.amfiindia.com/investor-corner", "https://investor.sebi.gov.in/"],
};

describe("isAllowedUrl (plan P9.4, audit S2)", () => {
  it("accepts corpus and edu URLs", () => {
    expect(isAllowedUrl("https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth", policy)).toBe(true);
    expect(isAllowedUrl("https://www.amfiindia.com/investor-corner", policy)).toBe(true);
    expect(isAllowedUrl("https://investor.sebi.gov.in/", policy)).toBe(true);
  });
  it("rejects other hosts, lookalikes, http and junk", () => {
    expect(isAllowedUrl("https://console.internal/runs/x", policy)).toBe(false);
    expect(isAllowedUrl("https://groww.in.evil.com/x", policy)).toBe(false);
    expect(isAllowedUrl("https://evilgroww.in/x", policy)).toBe(false);
    expect(isAllowedUrl("http://groww.in/x", policy)).toBe(false);
    expect(isAllowedUrl("javascript:alert(1)", policy)).toBe(false);
    expect(isAllowedUrl("not a url", policy)).toBe(false);
    expect(isAllowedUrl(null, policy)).toBe(false);
  });
  it("fails closed with an empty policy", () => {
    expect(isAllowedUrl("https://groww.in/x", { publicDomains: [], eduUrls: [] })).toBe(false);
  });
});

describe("splitRedactions", () => {
  it("splits known tokens only", () => {
    expect(splitRedactions("PAN [PAN], call [PHONE]. [NOTATOKEN]")).toEqual([
      { kind: "text", value: "PAN " },
      { kind: "token", value: "PAN" },
      { kind: "text", value: ", call " },
      { kind: "token", value: "PHONE" },
      { kind: "text", value: ". [NOTATOKEN]" },
    ]);
  });
  it("keeps markup as plain text", () => {
    expect(splitRedactions("<img src=x onerror=alert(1)>")).toEqual([{ kind: "text", value: "<img src=x onerror=alert(1)>" }]);
  });
});

describe("payload hash (arch §10.3)", () => {
  it("strips the display dashes", () => {
    expect(normalizeHex("F0B-222-d63")).toBe("f0b222d63");
  });
  it("matches sha256 of the UTF-8 payload, as the server computes it", async () => {
    const payload = "Exit load of 1% if redeemed within 1 year. ₹ →";
    const hex = await sha256Hex(payload);
    expect(hex).toMatch(/^[0-9a-f]{64}$/);
    const dashed = hex.match(/.{1,3}/g)?.join("-") ?? "";
    expect(await payloadMatchesHash(payload, dashed)).toBe(true);
    expect(await payloadMatchesHash(`${payload} `, dashed)).toBe(false);
  });
  it("known vector", async () => {
    expect(await sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});

describe("format", () => {
  it("formats dates and windows", () => {
    expect(formatDate("2026-07-04")).toBe("04-Jul-2026");
    expect(formatDate("01-Sep-2026")).toBe("01-Sep-2026");
    expect(formatDate(null)).toBe("—");
    expect(formatDateTime("2026-11-24T21:02:04.5")).toBe("24-Nov 21:02");
    expect(formatWindow("2026-04-12", "2026-07-04")).toBe("12-Apr-2026 → 04-Jul-2026");
    expect(formatWindowString("2026-11-18 → 2026-11-25")).toBe("18-Nov-2026 → 25-Nov-2026");
  });
  it("parses both date styles and measures staleness", () => {
    expect(parseDay("01-Sep-2026")?.toISOString()).toBe("2026-09-01T00:00:00.000Z");
    expect(daysSince("01-Sep-2026", new Date("2026-11-25T12:00:00Z"))).toBe(85);
    expect(daysSince("garbage")).toBeNull();
  });
  it("counts sentences", () => {
    expect(countSentences("One. Two? Three!")).toBe(3);
    expect(countSentences("Exit load of 1% if redeemed within 1 year.")).toBe(1);
  });
  it("labels URLs", () => {
    expect(urlLabel("https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth")).toBe("groww.in · hdfc-large-cap-fund-direct-growth");
  });
});
