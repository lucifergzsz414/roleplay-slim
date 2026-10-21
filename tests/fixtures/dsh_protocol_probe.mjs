// Uses the caller's installed DSH packages; never loads a DSH profile or tools.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const [moduleRoot, baseURL] = process.argv.slice(2);
const require = createRequire(join(moduleRoot, '__probe__.cjs'));
const load = (name) => import(pathToFileURL(require.resolve(name)).href);
const { Context } = await load('@deepseek-ai/cordis');
const llm = await load('@deepseek-ai/dsh-llm');
const adapter = await load('@deepseek-ai/dsh-llm-pi-ai');
const ctx = new Context();
process.env.ROLEPLAY_SLIM_DSH_PROBE_KEY = 'local-fixture-not-a-secret';
await ctx.plugin(llm.default);
await ctx.plugin(adapter, {
  providers: {
    'slim-probe': {
      apiKeyEnv: 'ROLEPLAY_SLIM_DSH_PROBE_KEY',
      baseURL,
      api: 'openai-completions',
      compat: {
        supportsDeveloperRole: false,
        maxTokensField: 'max_tokens',
        thinkingFormat: 'deepseek',
      },
      models: [{ id: 'fixture-model', name: 'Fixture', contextWindow: 65536, maxTokens: 1024 }],
    },
  },
});

try {
  const source = { kind: 'plugin', plugin: 'slim-probe' };
  const messages = [];
  for (let i = 0; i < 8; i++) {
    messages.push(llm.createUserMessage({ source, content: [{ type: 'text', text: `Question ${i}. Middle detail. End.` }] }));
    messages.push(llm.createAssistantMessage({
      source: { provider: 'slim-probe', model: 'fixture-model' },
      content: [{ type: 'text', text: `Answer ${i}. Middle detail. End.` }],
    }));
  }
  messages.push(llm.createUserMessage({ source, content: [{ type: 'text', text: 'Current question: keep me intact.' }] }));
  const options = { provider: 'slim-probe', model: 'fixture-model', system: 'Persona: preserve exactly.\nNo real actions.', messages, maxTokens: 1024 };
  const run = async (request) => {
    const assembled = new llm.BlockAssembler();
    for await (const chunk of ctx.llm.stream(request)) assembled.push(chunk);
    return assembled.message({ kind: 'model', provider: request.provider, model: request.model });
  };
  const text = await run(options);
  assert(text.content.some((b) => b.type === 'text' && b.text === '测试回复'));
  const tool = await run({ ...options, tools: [{ name: 'fixture_lookup', description: 'Inert schema, never executed', parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] } }] });
  assert(tool.content.some((b) => b.type === 'tool-call'));
  // Feed a synthetic result back to the adapter. No tool implementation exists.
  const call = tool.content.find((b) => b.type === 'tool-call');
  assert.equal(call.name, 'fixture_lookup');
  assert.deepEqual(JSON.parse(call.arguments), { query: 'test' });
  const result = llm.createToolResultMessage({ callId: call.id, content: [{ type: 'text', text: 'fixture-result' }], isError: false });
  const final = await run({ ...options, messages: [...messages, tool, result] });
  assert(final.content.some((b) => b.type === 'text' && b.text === '测试回复'));
  console.log(JSON.stringify({ text: true, toolCall: true, toolResultReplay: true, toolExecuted: false }));
} finally {
  await ctx.fiber.dispose();
}
