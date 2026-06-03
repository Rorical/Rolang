/**
 * Rolang VS Code Extension — Language Server Client
 *
 * Starts the `rolang-langserver` process and connects to it via stdio,
 * enabling diagnostics, hover, go-to-definition, and document outline
 * for .rl source files.
 */

import * as path from "path";
import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("rolang");
  const serverPath = config.get<string>("serverPath", "rolang-langserver");
  const extraArgs = config.get<string[]>("extraArgs", []);

  const serverOptions: ServerOptions = {
    command: serverPath,
    args: ["--stdio", ...extraArgs],
    transport: TransportKind.stdio,
    options: {
      env: process.env,
    },
  };

  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "rolang" },
    ],
    synchronize: {
      // Re-analyze when any .rl file changes on disk (e.g. after git pull)
      fileEvents: vscode.workspace.createFileSystemWatcher("**/*.rl"),
    },
    traceOutputChannel: vscode.window.createOutputChannel(
      "Rolang Language Server (trace)"
    ),
  };

  client = new LanguageClient(
    "rolang",
    "Rolang Language Server",
    serverOptions,
    clientOptions
  );

  client.start();
  context.subscriptions.push(client);

  // Show an error notification if the server crashes at startup so the
  // user knows to install rolang-langserver via:
  //   pip install rolang[langserver]   or   uv tool install rolang[langserver]
  client.onDidChangeState((event) => {
    if (
      event.oldState === 2 /* Running */ &&
      event.newState === 1 /* Starting */
    ) {
      vscode.window.showErrorMessage(
        "Rolang language server stopped unexpectedly. " +
          "Make sure rolang-langserver is installed and on your PATH."
      );
    }
  });
}

export function deactivate(): Thenable<void> | undefined {
  if (!client) {
    return undefined;
  }
  return client.stop();
}
