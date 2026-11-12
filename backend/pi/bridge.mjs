/** Pi owns coding tools and sessions; Python owns business permissions, evidence and approval. */
import { createInterface } from 'node:readline';
import { dirname } from 'node:path';
import { mkdirSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { createAgentSession, DefaultResourceLoader, loadProjectContextFiles, loadSkills, ModelRuntime, SessionManager, SettingsManager,
  createReadToolDefinition, createEditToolDefinition, createWriteToolDefinition, createBashToolDefinition,
  createFindToolDefinition, createLsToolDefinition } from '@earendil-works/pi-coding-agent';
import { compactionMetric, estimateInput } from './context-metrics.mjs';

process.umask(0o077);
const send = (value) => process.stdout.write(JSON.stringify(value) + '\n');
const input = createInterface({ input: process.stdin });
const replies = new Map();
let session;
let submitted = false;
let turn = 0;
let idle = 0;
let error;
let starting = false;
let stopping = false;

function shutdown() {
  stopping = true;
  // SDK dispose aborts tool signals and kills detached bash process groups.
  session?.dispose();
  for (const reply of replies.values()) reply({ error: 'Cancelled' });
  replies.clear();
  input.close();
  process.stdin.destroy();
}
process.once('SIGTERM', shutdown);
process.once('SIGHUP', shutdown);

function request(value, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new Error('Cancelled'));
    const id = value.id || randomUUID();
    const cancel = () => { replies.delete(id); reject(new Error('Cancelled')); };
    signal?.addEventListener('abort', cancel, { once: true });
    replies.set(id, (result) => {
      signal?.removeEventListener('abort', cancel);
      if (result.error) reject(new Error(result.error));
      else resolve(result);
    });
    send({ ...value, id });
  });
}

const shellQuote = (value) => "'" + value.replaceAll("'", "'\\''") + "'";

function codingTools(config) {
  const factories = { read: createReadToolDefinition, edit: createEditToolDefinition, write: createWriteToolDefinition,
    bash: createBashToolDefinition, find: createFindToolDefinition, ls: createLsToolDefinition };
  return config.nativeTools.map((name) => {
    const options = name === 'bash' ? {
      exposeSessionEnvironment: false,
      spawnHook: ({ command, cwd }) => ({ cwd, env: config.commandEnv,
        command: `exec sandbox-exec -p ${shellQuote(config.commandSandbox)} /bin/sh -c ${shellQuote(command)}` }),
    } : undefined;
    const tool = factories[name](config.cwd, options);
    return { ...tool, executionMode: 'sequential', execute: async (id, args, signal, onUpdate, ctx) => {
      if (submitted) throw new Error('当前阶段已经提交');
      const permitted = await request({ type: 'native_start', callId: id, name, args }, signal);
      let result;
      try {
        result = await tool.execute(id, permitted.args, signal, onUpdate, ctx);
      } catch (err) {
        if (signal?.aborted) throw err;
        result = { content: [{ type: 'text', text: String(err.message) }], isError: true };
      }
      if (config.apiKey) result = JSON.parse(JSON.stringify(result).replaceAll(config.apiKey, '[REDACTED]'));
      const recorded = await request({ type: 'native_end', callId: id, result }, signal);
      // Pi controls formatting/truncation. Keep images, diffs and structured results intact.
      return { ...result, content: [...result.content, { type: 'text',
        text: `[历史证据 ${recorded.evidenceId}；可用 read_evidence 回查，历史结果不代表当前版本]` }] };
    } };
  });
}

function images(urls = []) {
  return urls.map((url) => {
    const match = /^data:([^;]+);base64,(.+)$/s.exec(url);
    if (!match) throw new Error('Invalid image payload');
    return { type: 'image', mimeType: match[1], data: match[2] };
  });
}

async function start(config) {
  mkdirSync(dirname(config.session), { recursive: true, mode: 0o700 });
  const runtime = await ModelRuntime.create({ authPath: config.session + '.auth', modelsPath: config.session + '.models' });
  runtime.registerProvider('yunxiao', {
    baseUrl: config.baseUrl,
    api: 'openai-completions',
    apiKey: config.apiKey,
    models: [{ id: config.model, name: config.model, reasoning: false, input: ['text', 'image'],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: config.contextWindow, maxTokens: config.maxTokens,
      compat: { supportsStore: false, supportsDeveloperRole: false, supportsReasoningEffort: false, maxTokensField: 'max_tokens' } }],
  });
  const settings = SettingsManager.inMemory({ retry: { enabled: true, maxRetries: 3 }, compaction: {
    enabled: true, reserveTokens: config.reserveTokens, keepRecentTokens: config.keepRecentTokens,
  } });
  const discovered = loadSkills({ cwd: config.cwd, agentDir: dirname(config.session),
    skillPaths: config.skillPaths, includeDefaults: false });
  const contextFiles = loadProjectContextFiles({ cwd: config.cwd, agentDir: dirname(config.session) });
  const approved = await request({ type: 'resources', paths: [
    ...discovered.skills.map((skill) => skill.filePath), ...contextFiles.map((file) => file.path),
  ] });
  const resources = new DefaultResourceLoader({
    cwd: config.cwd, agentDir: dirname(config.session), settingsManager: settings,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    systemPrompt: config.system,
    agentsFilesOverride: () => ({ agentsFiles: contextFiles.filter((file) => approved.paths.includes(file.path)) }),
    skillsOverride: () => ({ ...discovered, skills: discovered.skills.filter((skill) => approved.paths.includes(skill.filePath)) }),
  });
  await resources.reload();
  const customTools = config.tools.map(({ function: tool }) => ({
    name: tool.name, label: tool.name, description: tool.description, parameters: tool.parameters,
    executionMode: 'sequential',
    execute: async (id, args, signal) => {
      if (submitted) return { content: [{ type: 'text', text: '阶段已提交，本次调用未执行' }], details: {} };
      const value = await request({ type: 'tool', id, name: tool.name, args }, signal);
      if (value.terminal) submitted = true;
      return { content: [{ type: 'text', text: value.text || '已执行' }, ...images(value.images)], details: {} };
    },
  }));
  const allTools = [...codingTools(config), ...customTools];
  ({ session } = await createAgentSession({
    cwd: config.cwd, agentDir: dirname(config.session), modelRuntime: runtime,
    model: runtime.getModel('yunxiao', config.model), thinkingLevel: 'off',
    resourceLoader: resources, tools: allTools.map((tool) => tool.name), customTools: allTools,
    sessionManager: SessionManager.open(config.session, dirname(config.session), config.cwd),
    settingsManager: settings,
  }));
  const streamModel = session.agent.streamFunction;
  let compactionStarted;
  let requestId = 0;
  session.agent.streamFunction = async (model, context, options) => {
    if (compactionStarted) context = { ...context, systemPrompt: (context.systemPrompt || '')
      + '\n额外要求：保留用户纠正、当前阶段、未完成事项、失败原因及历史证据 ID。区分假设与已证实事实、历史测试和当前版本。不得从聊天推断审批。历史工具输出可用 read_evidence 回查，无需重执行有副作用的操作。' };
    const estimated = estimateInput({ system: context.systemPrompt, tools: context.tools, messages: context.messages });
    const id = ++requestId;
    const started = Date.now();
    send({ type: 'context_metric', event: 'request_start', turn, request_id: id,
      request_kind: compactionStarted ? 'summary' : 'agent', estimated_input_tokens: estimated, input_limit: config.inputLimit });
    // Pi handles overflow with one compact-and-retry. Reject locally before sending an oversized request.
    if (estimated > config.inputLimit) {
      send({ type: 'context_metric', event: 'budget_blocked', turn, estimated_input_tokens: estimated, input_limit: config.inputLimit });
      throw new Error('Context length exceeds configured input budget');
    }
    const output = await streamModel(model, context, {
      ...options, maxTokens: config.maxTokens, ...(config.temperature == null ? {} : { temperature: config.temperature }),
    });
    void output.result().then((message) => send({ type: 'context_metric', event: 'request_end', turn, request_id: id,
      duration_ms: Date.now() - started, status: message.stopReason === 'error' ? 'failed' : message.stopReason === 'aborted' ? 'cancelled' : 'completed',
      usage: message.usage ? {
        input_tokens: message.usage.input + message.usage.cacheRead + message.usage.cacheWrite,
        output_tokens: message.usage.output, cached_tokens: message.usage.cacheRead, total_tokens: message.usage.totalTokens,
      } : null }), () => send({ type: 'context_metric', event: 'request_end', turn, request_id: id,
        duration_ms: Date.now() - started, status: 'failed' }));
    return output;
  };
  // Also serialize host callbacks; terminal validators must precede any later writes.
  session.agent.toolExecution = 'sequential';
  session.subscribe((event) => {
    if (event.type === 'turn_start') {
      turn += 1;
      send({ type: 'turn', turn });
      if (turn > config.maxTurns) { error = 'Pi 已达到请求轮数上限'; session.agent.abort(); }
    }
    if (event.type === 'message_update') {
      const delta = event.assistantMessageEvent;
      if (delta.type === 'text_delta') send({ type: 'text', text: delta.delta });
      if (delta.type === 'thinking_delta') send({ type: 'thinking', text: delta.delta });
    }
    if (event.type === 'message_end' && event.message.role === 'user') {
      const text = typeof event.message.content === 'string' ? event.message.content : event.message.content.filter((part) => part.type === 'text').map((part) => part.text).join('');
      const match = /^\[会话消息 #(\d+)\]/.exec(text);
      if (match) send({ type: 'delivered', ids: [Number(match[1])] });
    }
    if (event.type === 'message_end' && event.message.role === 'assistant') {
      const message = event.message;
      const text = message.content.filter((part) => part.type === 'text').map((part) => part.text).join('');
      if (text) send({ type: 'assistant', text });
      if (message.stopReason === 'error') error = message.errorMessage || 'Pi 模型请求失败';
      else error = undefined;
    }
    // All tool result messages are persisted by this point, so the session can resume safely.
    if (event.type === 'turn_end' && submitted) session.agent.abort();
    if (event.type === 'compaction_start') {
      compactionStarted = Date.now();
      send({ type: 'context_metric', event: 'compaction_start', reason: event.reason });
    }
    if (event.type === 'compaction_end') {
      send(compactionMetric(event, compactionStarted));
      compactionStarted = undefined;
    }
  });
  send({ type: 'ready' });
  await session.prompt(config.prompt);
  while (!stopping && !submitted && !error && turn < config.maxTurns && idle++ < 2) {
    await session.prompt('请继续调用工具完成当前阶段，完成后必须调用对应的 submit 工具。需要用户澄清时调用 ask_user。');
  }
  send({ type: 'done', submitted, error: submitted ? undefined : error || 'Pi 未提交阶段结果' });
  session.dispose();
  input.close();
  process.stdin.destroy();
}

input.on('line', (line) => {
  try {
    const value = JSON.parse(line);
    if (value.type === 'start' && !starting) {
      starting = true;
      void start(value).catch((err) => {
        // Credentials never appear in command-line args or protocol diagnostics.
        send({ type: 'fatal', error: String(err.message).replaceAll(value.apiKey || '\0', '[redacted]') });
        session?.dispose();
        input.close();
        process.stdin.destroy();
        process.exitCode = 1;
      });
    } else if (value.type === 'result') {
      replies.get(value.id)?.(value);
      replies.delete(value.id);
    } else if (value.type === 'steer') {
      void session.steer(value.text, images(value.images)).then(() => send({ type: 'queued', ids: value.ids })).catch(() => send({ type: 'note', text: '补充消息将在下一次运行时处理' }));
    }
  } catch { send({ type: 'fatal', error: 'Invalid host protocol message' }); }
});
input.on('close', () => { stopping = true; session?.dispose(); });
