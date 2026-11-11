import assert from "node:assert/strict";
import { test } from "node:test";
import { apiErrorMessage, readableUpstreamError, responseErrorMessage } from "../src/apiError.ts";

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

test("a broker JSON fragment inside an error sentence is reduced to its message and code", () => {
  const raw = 'token not accepted by the order API: error","errors":[{"errorCode":"UDAPI100050","message":"Invalid token used to access API","propertyPath":null,"invalidValue":null,"error_code":"UDAPI100050"}]}';
  assert.equal(readableUpstreamError(raw), "token not accepted by the order API: Invalid token used to access API (UDAPI100050)");
  assert.equal(readableUpstreamError('{"status":"error","errors":[{"message":"Rate limit \\"exceeded\\""}]}'), 'Rate limit "exceeded"');
});

test("plain error text and unknown shapes are shown unchanged", () => {
  assert.equal(readableUpstreamError("order window closed (09:15-15:40 IST)"), "order window closed (09:15-15:40 IST)");
  assert.equal(readableUpstreamError('HTTP 500 {"unexpected":true}'), 'HTTP 500 {"unexpected":true}');
  assert.equal(readableUpstreamError(null), "");
});
