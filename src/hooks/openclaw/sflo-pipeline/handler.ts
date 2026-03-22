import { execFile } from "node:child_process";
import { promisify } from "node:util";
import {
  existsSync,
  readFileSync,
  writeFileSync,
  unlinkSync,
  mkdirSync,
} from "node:fs";
import { join, dirname } from "node:path";

const execFileAsync = promisify(execFile);

/**
 * OpenClaw hook event shape.
 * Based on OpenClaw hook spec as of 2026-03. This interface is hand-written
 * from observed event payloads — not from an official SDK. If OpenClaw changes
 * the event structure, this may need updating.
 */
interface HookEvent {
  type: string;
  action: string;
  sessionKey: string;
  timestamp: Date;
  messages: string[];
  context: {
    workspaceDir?: string;
    content?: string;
    success?: boolean;
    [key: string]: unknown;
  };
}

/**
 * Resolve the workspace directory containing .sflo/state.json.
 * Checks (in order):
 *   1. event.context.workspaceDir (if OpenClaw provides it)
 *   2. SFLO_WORKSPACE env var (explicit user config)
 *   3. Walk common locations from HOME
 */
function resolveWorkspace(event: HookEvent): string | null {
  // 1. Event context (may be available for some event types)
  if (event.context.workspaceDir) {
    const candidate = event.context.workspaceDir as string;
    if (existsSync(join(candidate, ".sflo", "state.json"))) {
      return candidate;
    }
  }

  // 2. Explicit env var
  const envWorkspace = process.env.SFLO_WORKSPACE;
  if (envWorkspace && existsSync(join(envWorkspace, ".sflo", "state.json"))) {
    return envWorkspace;
  }

  // 3. Walk common locations from HOME (fragile — prefer SFLO_WORKSPACE)
  const home = process.env.HOME;
  if (!home) return null;

  const candidates = [
    join(home, "clawd"),
    join(home, "workspace"),
    home,
  ];

  for (const dir of candidates) {
    if (existsSync(join(dir, ".sflo", "state.json"))) {
      return dir;
    }
  }

  return null;
}

/** Safely remove a file, ignoring errors. */
function safeRemove(path: string): void {
  try {
    unlinkSync(path);
  } catch {
    /* ignore — file may not exist or be locked */
  }
}

const handler = async (event: HookEvent): Promise<void> => {
  // Only trigger on successful outbound messages
  if (event.type !== "message" || event.action !== "sent") return;
  if (event.context.success === false) return;

  const workspaceDir = resolveWorkspace(event);
  if (!workspaceDir) return;

  const sfloDir = join(workspaceDir, ".sflo");
  const stateFile = join(sfloDir, "state.json");

  let state: { current_state?: string };
  try {
    state = JSON.parse(readFileSync(stateFile, "utf-8"));
  } catch {
    return;
  }

  const current = state.current_state ?? "";

  // Terminal states — pipeline done, clean up marker
  if (["done", "escalate", ""].includes(current)) {
    safeRemove(join(sfloDir, ".last_hook_state"));
    return;
  }

  // Loop protection
  const marker = join(sfloDir, ".last_hook_state");
  if (existsSync(marker)) {
    try {
      const last = readFileSync(marker, "utf-8").trim();
      if (last === current) {
        return;
      }
    } catch {
      /* ignore */
    }
  }

  // Find scaffold.py relative to workspace
  const scaffoldPaths = [
    join(workspaceDir, "sflo", "src", "scaffold.py"),
    join(workspaceDir, "sflo-dev", "sflo", "src", "scaffold.py"),
  ];
  const scaffoldPath = scaffoldPaths.find((p) => existsSync(p));
  if (!scaffoldPath) return;

  // Get next instruction from scaffold (async — does not block event loop)
  const pythonCmd = process.env.SFLO_PYTHON ?? "python3";
  let data: { ok?: boolean; prompt?: string };
  try {
    const { stdout } = await execFileAsync(
      pythonCmd,
      [scaffoldPath, "prompt", "--sflo-dir", sfloDir],
      { timeout: 10_000, cwd: workspaceDir }
    );
    data = JSON.parse(stdout);
  } catch {
    return;
  }

  if (!data.ok || !data.prompt) return;

  // Record state for loop detection
  try {
    mkdirSync(dirname(marker), { recursive: true });
    writeFileSync(marker, current);
  } catch {
    /* ignore */
  }

  // Reinject next instruction
  event.messages.push(data.prompt);
};

export default handler;
