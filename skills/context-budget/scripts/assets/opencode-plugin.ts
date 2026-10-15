// ctxcheck-managed: written by `ctxcheck install-hook opencode`; remove with `--uninstall`. Edits are overwritten.
//
// context-budget backstop for OpenCode v1 and v2. After each completed tool
// call it asks `ctxcheck --hook --runtime opencode` whether this session is
// armed and over its gate, and appends ctxcheck's warning, if any, to the
// tool result the model reads. Every failure is swallowed: a broken backstop
// must never break a tool call. No dependencies beyond node:child_process.
// LIVE-UNVERIFIED: written against the source below, not run in OpenCode.
//
// verified 2026-10-15 against anomalyco/opencode (formerly sst/opencode):
//   One file serves both versions: a default-exported object with `id`, a v2
//   `setup(ctx)` and a v1 `server(input)` (v2.0.30 services/www/src/docs/
//   content/build/plugins/index.mdx "Support V1"; v1.19.1 packages/opencode/
//   src/plugin/shared.ts L272-315: a path plugin must export `id`; needs
//   v1.18.29+).
//   v2 (v2.0.30): `ctx.tool.hook("execute.after", event => ...)`, event
//   {tool, sessionID, id, status, result?} (packages/plugin/src/promise/
//   tool.ts L47-63). Its fields are readonly: assign a new `event.result`,
//   which the core reads back and passes through normalizeContent (string
//   content -> one text part; missing/empty -> stringify(output))
//   (packages/core/src/tool.ts L140-155, tool/runtime.ts L261-273).
//   v1 (v1.19.1): `"tool.execute.after": (input {tool, sessionID, callID,
//   args}, output {title, output, metadata})`; mutating output.output changes
//   what the model sees; MCP tools pass the raw {content: [...]} instead
//   (packages/plugin/src/index.ts L274-281; packages/opencode/src/session/
//   tools.ts L111-129, L419-466).
//   Spawning: plugins load under Bun or Node, so node:child_process, not
//   Bun.spawn (v2.0.30 packages/plugin/package.json).

import { spawn } from "node:child_process";

const CTXCHECK = "<CTXCHECK_PATH>";
const TIMEOUT_MS = 5000;

type TextPart = { type: "text"; text: string };

// ctxcheck's warning for this session, or undefined (silent, or any error).
function ctxcheckMessage(sessionID: unknown): Promise<string | undefined> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (msg?: string) => {
      if (!settled) {
        settled = true;
        resolve(msg);
      }
    };
    if (typeof sessionID !== "string" || !sessionID) return finish();
    try {
      const child = spawn("python3", [CTXCHECK, "--hook", "--runtime", "opencode"], {
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
      child.stdin?.end(JSON.stringify({ runtime: "opencode", session_id: sessionID }));
    } catch {
      finish();
    }
  });
}

// v2: the content the model would have seen, as parts (normalizeContent).
function contentParts(result: any): any[] {
  const content = result?.content;
  if (typeof content === "string") return [{ type: "text", text: content }];
  if (Array.isArray(content) && content.length > 0) return [...content];
  const output = result?.output;
  if (output === undefined) return [];
  if (typeof output === "string") return [{ type: "text", text: output }];
  try {
    return [{ type: "text", text: JSON.stringify(output) ?? String(output) }];
  } catch {
    return [{ type: "text", text: String(output) }];
  }
}

export default {
  id: "ctxcheck-context-budget",

  // OpenCode v2
  async setup(ctx: any) {
    try {
      await ctx.tool.hook("execute.after", async (event: any) => {
        try {
          if (event?.status !== "completed" || !event.result) return;
          const msg = await ctxcheckMessage(event.sessionID);
          if (!msg) return;
          const note: TextPart = { type: "text", text: msg };
          event.result = { ...event.result, content: [...contentParts(event.result), note] };
        } catch {}
      });
    } catch {}
  },

  // OpenCode v1
  async server(_input: any) {
    return {
      "tool.execute.after": async (input: any, output: any) => {
        try {
          if (!output) return;
          const msg = await ctxcheckMessage(input?.sessionID);
          if (!msg) return;
          if (typeof output.output === "string") {
            output.output = output.output ? `${output.output}\n\n${msg}` : msg;
          } else if (Array.isArray(output.content)) {
            output.content.push({ type: "text", text: msg });
          }
        } catch {}
      },
    };
  },
};
