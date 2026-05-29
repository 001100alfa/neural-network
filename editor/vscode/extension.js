// AIO Coding Agent — minimal VS Code integration.
//
// Three commands:
//   • aio.ask            — prompt for a task and run `aio "<task>"` in a terminal
//   • aio.askSelection   — send the current editor selection as context
//   • aio.openDashboard  — launch `aio --web` and open the dashboard in a webview
//
// The extension shells out to the `aio` CLI that ships with this repo, so the
// terminal/CLI agent and the editor integration share one implementation.

const vscode = require("vscode");
const http = require("http");

function cfg() {
  return vscode.workspace.getConfiguration("aio");
}

function aioArgs() {
  const provider = cfg().get("provider");
  return provider ? `-p ${provider} ` : "";
}

function workspaceDir() {
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length ? folders[0].uri.fsPath : process.cwd();
}

function runInTerminal(prompt) {
  const term =
    vscode.window.terminals.find((t) => t.name === "AIO") ||
    vscode.window.createTerminal({ name: "AIO", cwd: workspaceDir() });
  term.show();
  const escaped = prompt.replace(/"/g, '\\"');
  term.sendText(`${cfg().get("command")} ${aioArgs()}"${escaped}"`);
}

async function ask() {
  const prompt = await vscode.window.showInputBox({
    prompt: "What should the AIO agent do?",
    placeHolder: "e.g. add tests for utils.py and run them",
  });
  if (prompt) runInTerminal(prompt);
}

async function askSelection() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    vscode.window.showWarningMessage("AIO: no active editor.");
    return;
  }
  const selection = editor.document.getText(editor.selection);
  const rel = vscode.workspace.asRelativePath(editor.document.uri);
  const question = await vscode.window.showInputBox({
    prompt: `Ask about selection in ${rel}`,
    placeHolder: "e.g. explain this / refactor this / find the bug",
  });
  if (!question) return;
  const context = selection
    ? `${question}\n\nContext from ${rel}:\n\n${selection}`
    : `${question} (file: ${rel})`;
  runInTerminal(context);
}

function waitForDashboard(port, attempts, cb) {
  http
    .get({ host: "127.0.0.1", port, path: "/api/info", timeout: 1000 }, () => cb(true))
    .on("error", () => {
      if (attempts <= 0) return cb(false);
      setTimeout(() => waitForDashboard(port, attempts - 1, cb), 500);
    });
}

function openDashboard() {
  const port = cfg().get("dashboardPort");
  const url = `http://127.0.0.1:${port}`;
  const term =
    vscode.window.terminals.find((t) => t.name === "AIO web") ||
    vscode.window.createTerminal({ name: "AIO web", cwd: workspaceDir() });
  term.sendText(`${cfg().get("command")} ${aioArgs()}--web --port ${port}`);

  const panel = vscode.window.createWebviewPanel(
    "aioDashboard",
    "AIO Dashboard",
    vscode.ViewColumn.Beside,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  panel.webview.html = `<!doctype html><html><body style="margin:0">
    <p style="font:14px sans-serif;color:#888;padding:8px">Starting AIO dashboard at ${url} …</p>
    </body></html>`;
  waitForDashboard(port, 20, (ok) => {
    if (ok) {
      panel.webview.html = `<!doctype html><html><body style="margin:0;height:100vh">
        <iframe src="${url}" style="border:0;width:100%;height:100vh"></iframe></body></html>`;
    } else {
      vscode.window.showErrorMessage(
        `AIO: dashboard did not start on ${url}. Is the 'aio' CLI installed?`
      );
    }
  });
}

function activate(context) {
  context.subscriptions.push(
    vscode.commands.registerCommand("aio.ask", ask),
    vscode.commands.registerCommand("aio.askSelection", askSelection),
    vscode.commands.registerCommand("aio.openDashboard", openDashboard)
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
