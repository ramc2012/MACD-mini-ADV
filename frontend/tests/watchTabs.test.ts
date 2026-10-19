import assert from "node:assert/strict";
import { test } from "node:test";
import { optionRowsForTab, WATCH_TABS } from "../src/watchTabs.ts";

// One underlying's six-leg ladder: ATM 22800, one strike either side.
const ladder = [
  { symbol: "CE-22750", option_type: "CE", moneyness: "ITM" },
  { symbol: "CE-22800", option_type: "CE", moneyness: "ATM" },
  { symbol: "CE-22850", option_type: "CE", moneyness: "OTM" },
  { symbol: "PE-22850", option_type: "PE", moneyness: "ITM" },
  { symbol: "PE-22800", option_type: "PE", moneyness: "ATM" },
  { symbol: "PE-22750", option_type: "PE", moneyness: "OTM" },
];

const symbols = (tab: Parameters<typeof optionRowsForTab>[1]) => optionRowsForTab(ladder, tab).map((row) => row.symbol);

test("ATM tabs list only the at-the-money leg per side", () => {
  assert.deepEqual(symbols("CE_ATM"), ["CE-22800"]);
  assert.deepEqual(symbols("PE_ATM"), ["PE-22800"]);
});

test("ATM+1 is one strike out of the money: above for calls, below for puts", () => {
  assert.deepEqual(symbols("CE_ATM1"), ["CE-22850"]);
  assert.deepEqual(symbols("PE_ATM1"), ["PE-22750"]);
});

test("ITM legs and the spot tab list no options", () => {
  const listed = new Set(WATCH_TABS.flatMap((tab) => symbols(tab.key)));
  assert.equal(listed.has("CE-22750") || listed.has("PE-22850"), false);
  assert.deepEqual(symbols("SPOT"), []);
});

test("a legacy row without moneyness counts as ATM", () => {
  const rows = [{ symbol: "OLD", option_type: "CE", moneyness: undefined }];
  assert.deepEqual(optionRowsForTab(rows, "CE_ATM").map((row) => row.symbol), ["OLD"]);
  assert.deepEqual(optionRowsForTab(rows, "CE_ATM1"), []);
});
