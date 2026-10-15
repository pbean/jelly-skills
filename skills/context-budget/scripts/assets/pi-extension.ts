// ctxcheck-managed: written by `ctxcheck install-hook pi`; remove with `--uninstall`. Edits are overwritten.
//
// context-budget backstop for Pi. After each tool call it passes Pi's own
// context usage to `ctxcheck --hook --runtime pi`, which answers with a
// warning only when this session is armed and over its gate; the warning is
// appended to the tool result the model reads. Every failure is swallowed: a
// broken backstop must never break a tool call. No dependencies beyond
// node:child_process.
// LIVE-UNVERIFIED: written against the source below, not run in Pi.
//
// verified 2026-10-15 against earendil-works/pi v1.3.0 (71023aec), paths
// under packages/coding-agent/:
//   docs/extensions.md L13-30, src/core/extensions/loader.ts L581-584: the
//     default export is a function receiving the ExtensionAPI.
//   src/core/extensions/types.ts L1662: pi.on("tool_result", (event, ctx));
//     L1267-1330: event {toolName, content: (TextContent|ImageContent)[],
//     structuredContent?, isError, ...}; L1476-1490 ToolResultEventResult:
//     return {content} to change the result (mutating the event does nothing,
//     runner.ts L1190-1246); replacing content drops structuredContent unless
//     it is returned too.
//   types.ts L306-311, L360: ctx.getContextUsage() -> {tokens: number|null,
//     contextWindow, percent} | undefined; tokens is null right after a
//     compaction (then ctxcheck is not asked: it would read the same unknown).
//   src/core/session-manager.ts L245-250: ctx.sessionManager.getSessionId(),
//     the session header id that `ctxcheck --arm` records (PI_SESSION_ID).

import { spawn } from "node:child_process";

const CTXCHECK = "<CTXCHECK_PATH>";
const TIMEOUT_MS = 5000;

// ctxcheck's warning, or undefined (silent, or any error).
function ctxcheckMessage(payload: object): Promise<string | undefined> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (msg?: string) => {
      if (!settled) {
        settled = true;
        resolve(msg);
      }
    };
    try {
      const child = spawn("python3", [CTXCHECK, "--hook", "--runtime", "pi"], {
        stdio: ["pipe", "pipe", "ignore"],
      });
      let out = "";
      const timer = setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {}
        finish();
      }, TIMEOUT_MS);
      child.on("error", () => {
        clearTimeout(timer);
        finish();
      });
      child.stdout?.setEncoding("utf8");
      child.stdout?.on("data", (chunk: string) => {
        out += chunk;
      });
      child.on("close", () => {
        clearTimeout(timer);
        try {
          const msg = JSON.parse(out).message;
          finish(typeof msg === "string" && msg ? msg : undefined);
        } catch {
          finish();
        }
      });
      child.stdin?.on("error", () => {});
      child.stdin?.end(JSON.stringify(payload));
    } catch {
      finish();
    }
  });
}

export default function (pi: any) {
  pi.on("tool_result", async (event: any, ctx: any) => {
    try {
      const usage = ctx?.getContextUsage?.();
      if (!usage || typeof usage.tokens !== "number") return;
      const sessionId = ctx?.sessionManager?.getSessionId?.();
      if (typeof sessionId !== "string" || !sessionId) return;
      const msg = await ctxcheckMessage({
        runtime: "pi",
        session_id: sessionId,
        used: usage.tokens,
        window: usage.contextWindow,
      });
      if (!msg) return;
      const content = Array.isArray(event?.content) ? event.content : [];
      const change: Record<string, unknown> = {
        content: [...content, { type: "text", text: msg }],
      };
      if (event?.structuredContent !== undefined) change.structuredContent = event.structuredContent;
      return change;
    } catch {
      return;
    }
  });
}
