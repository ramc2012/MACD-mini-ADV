import assert from "node:assert/strict";
import { test } from "node:test";
import { apiErrorMessage, responseErrorMessage } from "../src/apiError.ts";

test("FastAPI validation details become safe text for React", () => {
  assert.equal(
    apiErrorMessage([
      { loc: ["body", "client_id"], msg: "Field required", input: { secret: "do-not-display" } },
      { loc: ["body", "redirect_uri"], msg: "Invalid URL" },
    ], "Fallback"),
    "client_id: Field required; redirect_uri: Invalid URL",
  );
  assert.equal(apiErrorMessage([{ loc: ["body"], msg: "Invalid data" }], "Fallback"), "Invalid data");
  assert.equal(apiErrorMessage([{ unexpected: true }], "Fallback"), "Fallback");
  assert.equal(apiErrorMessage("Bad credentials", "Fallback"), "Bad credentials");
});

test("non-JSON upstream errors keep a useful HTTP status", async () => {
  const response = new Response("<html>Bad gateway</html>", { status: 502 });
  assert.equal(await responseErrorMessage(response, "Fyers connection failed"), "Fyers connection failed (HTTP 502)");
});
