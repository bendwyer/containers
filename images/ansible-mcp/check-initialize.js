// Build check: start the server over stdio and require an initialize result.
const { spawn } = require("node:child_process");

const server = spawn(process.execPath, ["dist/cli.cjs", "--stdio"], {
  stdio: ["pipe", "pipe", "inherit"],
});

let output = "";
const fail = (why) => {
  console.error(`ansible-mcp build check: ${why}`);
  server.kill();
  process.exit(1);
};
const timer = setTimeout(() => fail("no initialize result within 20s"), 20000);

server.stdout.on("data", (chunk) => {
  output += chunk;
  for (const line of output.split("\n")) {
    try {
      const message = JSON.parse(line);
      if (message.id === 1 && message.result) {
        clearTimeout(timer);
        server.kill();
        process.exit(0);
      }
    } catch {
      // Partial line; wait for more output.
    }
  }
});
server.on("exit", (code) => fail(`server exited with code ${code} before answering`));

server.stdin.write(
  JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-06-18",
      capabilities: {},
      clientInfo: { name: "build-check", version: "0" },
    },
  }) + "\n",
);
