// FastAPI validation errors use an array of {loc, msg, ...} objects. Keep
// response details as text before passing them into React or Error.
export function apiErrorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail.trim() || fallback;

  if (Array.isArray(detail)) {
    const messages = detail.flatMap((entry) => {
      if (typeof entry === "string") return entry.trim() ? [entry.trim()] : [];
      if (!entry || typeof entry !== "object") return [];
      const issue = entry as { loc?: unknown; msg?: unknown };
      if (typeof issue.msg !== "string" || !issue.msg.trim()) return [];
      const location = Array.isArray(issue.loc)
        ? issue.loc.filter((part): part is string | number =>
            typeof part === "string" || typeof part === "number")
            .filter((part) => part !== "body")
            .join(".")
        : "";
      return [location ? `${location}: ${issue.msg.trim()}` : issue.msg.trim()];
    });
    return messages.join("; ") || fallback;
  }

  return fallback;
}

export async function responseErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      return apiErrorMessage(body.detail, fallback);
    }
  } catch {
    // An upstream proxy may return HTML or an empty response.
  }
  return `${fallback} (HTTP ${response.status})`;
}

// Services sometimes pass along a slice of a broker's JSON error body inside a sentence, for example
//   token not accepted by the order API: error","errors":[{"errorCode":"UDAPI100050","message":"Invalid token…
// For display keep the sentence, the broker's message and its code; text without such a fragment is returned as is.
export function readableUpstreamError(text: string | null | undefined): string {
  const raw = (text ?? "").trim();
  const start = raw.search(/[{[]|"\s*,\s*"/);
  if (start < 0) return raw;
  const message = /"message"\s*:\s*"((?:[^"\\]|\\.)*)"/.exec(raw)?.[1]?.replace(/\\(.)/g, "$1").trim();
  if (!message) return raw;
  const code = /"(?:errorCode|error_code)"\s*:\s*"([\w.-]+)"/.exec(raw)?.[1];
  const lead = raw.slice(0, start).replace(/[\s:,"]+$/, "").replace(/:\s*(?:error|failure|failed)$/i, "");
  return `${lead ? `${lead}: ` : ""}${message}${code ? ` (${code})` : ""}`;
}
