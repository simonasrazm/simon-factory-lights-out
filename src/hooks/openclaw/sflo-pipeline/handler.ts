import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import {
  existsSync,
  readFileSync,
  writeFileSync,
  unlinkSync,
  mkdirSync,
} from "node:fs";
import { join, dirname, resolve } from "node:path";

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
 * Resolve the workspace directory that may contain SFLO state.
 * Checks (in order):
 *   1. event.context.workspaceDir (if OpenClaw provides it)
 *   2. SFLO_WORKSPACE env var (explicit user config)
 */
function resolveWorkspace(event: HookEvent): string | null {
  // 1. Event context (may be available for some event types)
  if (event.context.workspaceDir) {
    return event.context.workspaceDir as string;
  }

  // 2. Explicit env var
  const envWorkspace = process.env.SFLO_WORKSPACE;
  if (envWorkspace) {
    return envWorkspace;
  }

  return null;
}

function hasState(sfloDir: string): boolean {
  return existsSync(join(sfloDir, "state.json"));
}

/**
 * Resolve the active SFLO state directory.
 * Prefer active named factories recorded in .sflo/registry.json, with the
 * legacy .sflo/state.json layout kept as fallback for old local state.
 */
function resolveSfloDir(workspaceDir: string): string | null {
  const sfloParent = join(workspaceDir, ".sflo");
  const registryPath = join(sfloParent, "registry.json");

  if (existsSync(registryPath)) {
    let factories: Record<string, { status?: string; last_active?: string; sflo_dir?: string }>;
    try {
      const registry = JSON.parse(readFileSync(registryPath, "utf-8"));
      factories = registry.factories ?? {};
    } catch {
      factories = {};
    }

    const entries = Object.entries(factories)
      .filter(([, entry]) => entry?.status === "active")
      .sort(([, a], [, b]) => (b.last_active ?? "").localeCompare(a.last_active ?? ""));

    for (const [name, entry] of entries) {
      const candidate = entry.sflo_dir ?? join(sfloParent, name);
      if (hasState(candidate)) return candidate;
    }
  }

  if (hasState(sfloParent)) {
    return sfloParent;
  }

  return null;
}

/**
 * Resolve the self-contained SFLO skill that owns scaffold.py.
 * The installed hook is a symlink into that skill, so its real module path
 * provides the normal location. SFLO_HOME remains an explicit override.
 */
function resolveSfloHome(workspaceDir: string): string | null {
  const candidates: string[] = [];

  if (process.env.SFLO_HOME) {
    candidates.push(process.env.SFLO_HOME);
  }

  try {
    const hookDir = dirname(fileURLToPath(import.meta.url));
    candidates.push(resolve(hookDir, "../../../.."));
    const recordedHome = join(hookDir, ".sflo-home");
    if (existsSync(recordedHome)) {
      const value = readFileSync(recordedHome, "utf-8").trim();
      if (value) candidates.push(value);
    }
  } catch {
    /* ignore — fallback candidates below */
  }

  candidates.push(join(workspaceDir, "sflo"));

  for (const candidate of candidates) {
    if (existsSync(join(candidate, "src", "scaffold.py"))) {
      return candidate;
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

  const sfloDir = resolveSfloDir(workspaceDir);
  if (!sfloDir) return;
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

  const sfloHome = resolveSfloHome(workspaceDir);
  if (!sfloHome) return;
  const scaffoldPath = join(sfloHome, "src", "scaffold.py");

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
