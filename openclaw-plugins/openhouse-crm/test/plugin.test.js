import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createPluginDefinition } from "../dist/definition.js";


const root = new URL("../", import.meta.url);


test("plugin registers exactly one required CRM tool factory", async () => {
  const registrations = [];
  const plugin = createPluginDefinition(async (input, context) => ({ input, context }));
  plugin.register({ registerTool: (...args) => registrations.push(args) });

  assert.equal(plugin.id, "openhouse-crm");
  assert.equal(registrations.length, 1);
  const [factory, metadata] = registrations[0];
  assert.equal(typeof factory, "function");
  assert.equal(metadata, undefined);

  const tool = factory({ workspaceDir: "/trusted/workspace" });
  assert.equal(tool.name, "openhouse_crm");
  assert.equal(tool.parameters.type, "object");
  assert.equal(tool.parameters.additionalProperties, false);
  assert.equal(tool.parameters.required.includes("operation"), true);
  assert.equal(tool.parameters.properties.arguments.type, "object");
  assert.equal(tool.parameters.properties.arguments.additionalProperties, true);

  const operations = JSON.parse(await readFile(new URL("../operations.json", import.meta.url)));
  assert.deepEqual(tool.parameters.properties.operation.enum, operations);

  const result = await tool.execute("call-id", {
    operation: "list_leads",
    arguments: { sort: "priority" },
  });
  assert.deepEqual(result.details, {
    input: { operation: "list_leads", arguments: { sort: "priority" } },
    context: { workspaceDir: "/trusted/workspace" },
  });
  assert.deepEqual(result.content, [
    { type: "text", text: JSON.stringify(result.details) },
  ]);
});


test("manifest declares exact tool ownership and no configuration", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("openclaw.plugin.json", root), "utf8"),
  );
  assert.equal(manifest.id, "openhouse-crm");
  assert.deepEqual(manifest.contracts, { tools: ["openhouse_crm"] });
  assert.deepEqual(manifest.activation, { onStartup: true });
  assert.deepEqual(manifest.configSchema, {
    type: "object",
    additionalProperties: false,
  });
});


test("package ships built ESM with no install-time or runtime dependencies", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("package.json", root), "utf8"),
  );
  assert.equal(packageJson.type, "module");
  assert.deepEqual(packageJson.openclaw.extensions, ["./dist/index.js"]);
  assert.equal(packageJson.dependencies, undefined);
  assert.equal(packageJson.optionalDependencies, undefined);
  assert.equal(packageJson.scripts?.install, undefined);
  assert.match(packageJson.peerDependencies.openclaw, /^>=2026\./);
});


test("runtime entry uses the supported focused plugin SDK entrypoint", async () => {
  const source = await readFile(new URL("dist/index.js", root), "utf8");
  assert.match(source, /openclaw\/plugin-sdk\/plugin-entry/);
  assert.match(source, /definePluginEntry/);
  assert.doesNotMatch(source, /typebox|child_process|exec\s*\(/i);
});
