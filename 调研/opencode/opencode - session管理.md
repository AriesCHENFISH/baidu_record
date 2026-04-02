# OpenCode Session 管理

Session是 OpenCode 系统的核心概念，用于管理用户与 AI 之间的对话上下文。Session 负责存储消息历史、管理工具调用状态、处理与 LLM 的交互，是连接 Agent、Tool、LLM 三者的核心枢纽。

---

## 一、核心代码文件

### 1.1 Session 核心定义

| 文件路径 | 作用 |
|----------|------|
| packages/opencode/src/session/index.ts | Session 主文件，包含 Session.Info 定义、create/fork/get/update 等核心方法 |
| packages/opencode/src/session/status.ts | Session 状态管理（idle/busy/retry） |
| packages/opencode/src/session/todo.ts | Session 待办事项管理 |

---

### 1.2 Message 相关

| 文件路径 | 作用 |
|----------|------|
| packages/opencode/src/session/message-v2.ts | 核心消息结构定义（User/Assistant 消息、Part 类型） |
| packages/opencode/src/session/message.ts | 旧版 Message 定义（已废弃） |

---

### 1.3 LLM 交互

| 文件路径 | 作用 |
|----------|------|
| packages/opencode/src/session/llm.ts | LLM 流式调用，包含工具解析、系统提示构建 |
| packages/opencode/src/session/system.ts | 系统提示管理，为不同 Provider 生成提示 |
| packages/opencode/src/session/processor.ts | 消息处理器，处理 LLM 流式输出和工具调用 |

---

### 1.4 消息处理

| 文件路径 | 作用 |
|----------|------|
| packages/opencode/src/session/prompt.ts | Session 提示管理，包含 prompt() 主入口和 loop() 循环 |
| packages/opencode/src/session/compaction.ts | 上下文压缩 |
| packages/opencode/src/session/summary.ts | Session 摘要生成 |
| packages/opencode/src/session/retry.ts | 重试机制 |
| packages/opencode/src/session/revert.ts | 回滚功能 |
| packages/opencode/src/session/instruction.ts | 指令管理 |

---

### 1.5 Server API

| 文件路径 | 作用 |
|----------|------|
| packages/opencode/src/server/routes/session.ts | Session CRUD API 路由 |

---

## 二、Session 数据结构

### 2.1 Session.Info 定义

```typescript
export const Info = z.object({
  id: Identifier.schema("session"), // Session 唯一 ID
  slug: z.string(), // URL 友好的 slug
  projectID: z.string(), // 所属项目 ID
  directory: z.string(), // 工作目录
  parentID: Identifier.schema("session").optional(), // 父 Session ID（用于子 Agent）
  summary: z
    .object({
      // Session 摘要
      additions: z.number(),
      deletions: z.number(),
      files: z.number(),
      diffs: Snapshot.FileDiff.array().optional(),
    })
    .optional(),
  share: z
    .object({
      // 分享信息
      url: z.string(),
    })
    .optional(),
  title: z.string(), // Session 标题
  version: z.string(), // OpenCode 版本
  time: z.object({
    // 时间戳
    created: z.number(),
    updated: z.number(),
    compacting: z.number().optional(), // 压缩时间
    archived: z.number().optional(), // 归档时间
  }),
  permission: PermissionNext.Ruleset.optional(), // 权限规则
  revert: z
    .object({
      // 回滚信息
      messageID: z.string(),
      partID: z.string().optional(),
      snapshot: z.string().optional(),
      diff: z.string().optional(),
    })
    .optional(),
})
```

---

### 2.2 Session 创建

```typescript
export async function createNext(input: {
  id?: string
  title?: string
  parentID?: string
  directory: string
  permission?: PermissionNext.Ruleset
}) {
  const result: Info = {
    id: Identifier.descending("session", input.id),
    slug: Slug.create(),
    version: Installation.VERSION,
    projectID: Instance.project.id,
    directory: input.directory,
    parentID: input.parentID, // 关键：关联父 Session
    title: input.title ?? createDefaultTitle(!!input.parentID),
    permission: input.permission,
    time: {
      created: Date.now(),
      updated: Date.now(),
    },
  }

  // 持久化存储
  await Storage.write(["session", Instance.project.id, result.id], result)

  // 发布事件
  Bus.publish(Event.Created, { info: result })
  Bus.publish(Event.Updated, { info: result })

  return result
}
```

---

### 2.3 Session Fork（分支）

```typescript
export const fork = fn(
  z.object({
    sessionID: Identifier.schema("session"),
    messageID: Identifier.schema("message").optional(),
  }),
  async (input) => {
    // 1. 获取原 Session
    const original = await get(input.sessionID)

    // 2. 创建新 Session
    const session = await createNext({
      directory: Instance.directory,
      title: getForkedTitle(original.title), // "原标题 (fork #1)"
    })

    // 3. 复制消息历史（到指定 messageID 为止）
    const msgs = await messages({ sessionID: input.sessionID })
    const idMap = new Map<string, string>()

    for (const msg of msgs) {
      if (input.messageID && msg.info.id >= input.messageID) break

      // 克隆消息和 Parts
      const newID = Identifier.ascending("message")
      idMap.set(msg.info.id, newID)

      const parentID = msg.info.role === "assistant" && msg.info.parentID ? idMap.get(msg.info.parentID) : undefined

      await updateMessage({
        ...msg.info,
        sessionID: session.id,
        id: newID,
        ...(parentID && { parentID }),
      })

      for (const part of msg.parts) {
        await updatePart({
          ...part,
          id: Identifier.ascending("part"),
          messageID: cloned.id,
          sessionID: session.id,
        })
      }
    }

    return session
  },
)
```

---

## 三、Message 数据结构

### 3.1 MessageV2 核心类型

```typescript
// User 消息
export type User = Info & {
  role: "user"
  agent: string // 使用的 Agent
  model: {
    // 使用的模型
    providerID: string
    modelID: string
  }
  system?: string // 自定义系统提示
  tools?: Record<string, boolean> // 工具配置（已废弃）
  variant?: string // 模型变体
}

// Assistant 消息
export type Assistant = Info & {
  role: "assistant"
  parentID: string // 父消息 ID
  agent: string // 使用的 Agent
  mode: string // Agent 模式
  finish?: string // 结束原因 (stop/tool-calls/error)
  modelID: string
  providerID: string
  cost: number // 花费
  tokens: {
    input: number
    output: number
    reasoning: number
    cache: { read: number; write: number }
  }
  path: {
    cwd: string
    root: string
  }
}
```

---

### 3.2 Part 类型系统

位置: packages/opencode/src/session/message-v2.ts:39-299

```typescript
// 1. TextPart - 文本内容
export const TextPart = PartBase.extend({
  type: z.literal("text"),
  text: z.string(),
  synthetic: z.boolean().optional(), // 是否合成（系统生成）
  ignored: z.boolean().optional(), // 是否忽略
})

// 2. ReasoningPart - 推理过程
export const ReasoningPart = PartBase.extend({
  type: z.literal("reasoning"),
  text: z.string(),
  metadata: z.record(z.string(), z.any()).optional(),
  time: z.object({ start: z.number(), end: z.number().optional() }),
})

// 3. FilePart - 文件内容
export const FilePart = PartBase.extend({
  type: z.literal("file"),
  mime: z.string(),
  filename: z.string().optional(),
  url: z.string(),
  source: FilePartSource.optional(),
})

// 4. AgentPart - Agent 调用
export const AgentPart = PartBase.extend({
  type: z.literal("agent"),
  name: z.string(),
})

// 5. SubtaskPart - 子任务
export const SubtaskPart = PartBase.extend({
  type: z.literal("subtask"),
  prompt: z.string(),
  description: z.string(),
  agent: z.string(),
  model: z.object({ providerID: z.string(), modelID: z.string() }).optional(),
  command: z.string().optional(),
})

// 6. ToolPart - 工具调用（核心）
export const ToolPart = PartBase.extend({
  type: z.literal("tool"),
  callID: z.string(), // 调用 ID
  tool: z.string(), // 工具名称
  state: ToolState, // 工具状态
})

// 工具状态
export const ToolState = z.discriminatedUnion("status", [
  ToolStatePending, // pending - 等待执行
  ToolStateRunning, // running - 执行中
  ToolStateCompleted, // completed - 执行完成
  ToolStateError, // error - 执行错误
])
```

---

## 四、Session 与 Agent 的交互

### 4.1 Session 选择 Agent

```typescript
async function createUserMessage(input: PromptInput) {
  // 从配置或默认获取 Agent
  const agent = await Agent.get(input.agent ?? (await Agent.defaultAgent()))

  const info: MessageV2.Info = {
    id: input.messageID ?? Identifier.ascending("message"),
    role: "user",
    sessionID: input.sessionID,
    agent: agent.name, // 设置使用的 Agent
    model: input.model ?? agent.model ?? (await lastModel(input.sessionID)),
    // ...
  }
}
```

---

### 4.2 Agent 权限与 Session 权限合并

```typescript
async function resolveTools(input) {
  // 权限合并：Agent 权限 + Session 权限
  async ask(req) {
    await PermissionNext.ask({
      ...req,
      sessionID: input.session.id,
      tool: { messageID, callID: options.toolCallId },
      ruleset: PermissionNext.merge(
        input.agent.permission,           // Agent 权限
        input.session.permission ?? []    // Session 权限
      ),
    })
  }
}
```

---

### 4.3 Session 创建时指定 Agent

在 Task Tool 创建子 Session 时：

位置: packages/opencode/src/tool/task.ts:73-102

```typescript
const session = await Session.create({
  parentID: ctx.sessionID, // 关联父 Session
  title: params.description + ` (@${agent.name} subagent)`,
  permission: [
    // 子 Session 继承父 Session 权限，但可以添加额外限制
    { permission: "todowrite", pattern: "*", action: "deny" },
    { permission: "todoread", pattern: "*", action: "deny" },
  ],
})
```

---

## 五、Session 与 Tool 的交互

### 5.1 工具调用状态管理

Session 通过 Message 存储工具调用状态：

```
User Message
    │
    ├── TextPart: "请读取 package.json"
    │
Assistant Message
    │
    ├── ToolPart (status: pending)
    │     tool: "read"
    │     callID: "call_xxx"
    │     input: { filePath: "package.json" }
    │
    ├── ToolPart (status: running)
    │     tool: "read"
    │     callID: "call_xxx"
    │     input: { filePath: "package.json" }
    │     time: { start: 1700000000000 }
    │
    └── ToolPart (status: completed)
          tool: "read"
          callID: "call_xxx"
          output: "<file>...package.json content...</file>"
          time: { start: 1700000000000, end: 1700000000100 }
```

---

### 5.2 工具调用生命周期

位置: packages/opencode/src/session/processor.ts:103-221

```typescript
// 1. 工具开始调用
case "tool-input-start":
  const part = await Session.updatePart({
    type: "tool",
    tool: value.toolName,
    callID: value.id,
    state: { status: "pending", input: {} },
  })

// 2. 工具实际调用
case "tool-call":
  const part = await Session.updatePart({
    ...match,
    state: {
      status: "running",
      input: value.input,
      time: { start: Date.now() },
    },
  })

// 3. 工具执行完成
case "tool-result":
  await Session.updatePart({
    ...match,
    state: {
      status: "completed",
      output: value.output.output,
      metadata: value.output.metadata,
      title: value.output.title,
      time: { start: match.state.time.start, end: Date.now() },
    },
  })

// 4. 工具执行错误
case "tool-error":
  await Session.updatePart({
    ...match,
    state: {
      status: "error",
      error: value.error.toString(),
    },
  })
```

---

### 5.3 Tool Context 中的 Session 信息

```typescript
export type Context<M extends Metadata = Metadata> = {
  sessionID: string // Session ID（关键！）
  messageID: string // 当前 Message ID
  agent: string // 当前 Agent
  abort: AbortSignal // 中止信号
  callID?: string // Tool 调用 ID
  extra?: { [key: string]: any }
  messages: MessageV2.WithParts[] // 消息历史
  metadata(input): void // 更新元数据
  ask(input): Promise<void> // 请求权限
}
```

---

## 六、Session 与 LLM 的交互

### 6.1 LLM.stream() 核心逻辑

```typescript
export async function stream(input: StreamInput) {
  // 1. 获取 Provider 和配置
  const [language, cfg, provider, auth] = await Promise.all([
    Provider.getLanguage(input.model),
    Config.get(),
    Provider.getProvider(input.model.providerID),
    Auth.get(input.model.providerID),
  ])

  // 2. 构建系统提示
  const system = [
    // Agent 自定义提示或 Provider 提示
    ...(input.agent.prompt ? [input.agent.prompt] : []),
    // 自定义提示
    ...input.system,
    // 用户消息中的系统提示
    ...(input.user.system ? [input.user.system] : []),
  ].join("\n")

  // 3. 解析工具
  const tools = await resolveTools(input)

  // 4. 调用 AI SDK
  return streamText({
    model: language,
    messages: input.messages,
    tools,
    abortSignal: input.abort,
    // ... 其他参数
  })
}
```

---

### 6.2 消息格式转换

位置: packages/opencode/src/session/message-v2.ts

// MessageV2 转换为 AI SDK ModelMessage
// 这是一个复杂的转换过程，涉及：
// 1. 文本内容 -> string
// 2. 文件内容 -> base64
// 3. 工具调用 -> toolCall
// 4. 工具结果 -> toolResult

// 位置在 toModelMessages() 函数中

---

### 6.3 Session Loop 核心流程

位置: packages/opencode/src/session/prompt.ts:256-667

```typescript
export const loop = fn(Identifier.schema("session"), async (sessionID) => {
  while (true) {
    // 1. 设置状态为 busy
    SessionStatus.set(sessionID, { type: "busy" })

    // 2. 获取消息历史（过滤已压缩的）
    let msgs = await MessageV2.filterCompacted(MessageV2.stream(sessionID))

    // 3. 查找最后的用户消息和助手消息
    let lastUser, lastAssistant, lastFinished
    for (let i = msgs.length - 1; i >= 0; i--) {
      const msg = msgs[i]
      if (!lastUser && msg.info.role === "user") lastUser = msg.info
      if (!lastAssistant && msg.info.role === "assistant") lastAssistant = msg.info
      if (!lastFinished && msg.info.role === "assistant" && msg.info.finish) {
        lastFinished = msg.info
      }
      if (lastUser && lastFinished) break
    }

    // 4. 检查是否结束
    if (lastAssistant?.finish && !["tool-calls", "unknown"].includes(lastAssistant.finish)) {
      break  // 结束循环
    }

    // 5. 获取 Agent 配置
    const agent = await Agent.get(lastUser.agent)

    // 6. 创建处理器
    const processor = SessionProcessor.create({
      assistantMessage: await Session.updateMessage({ /* ... */ }),
      sessionID,
      model,
      abort,
    })

    // 7. 解析可用工具
    const tools = await resolveTools({
      agent,
      session,
      model,
      bypassAgentCheck,
      messages: msgs,
    })

    // 8. 调用 LLM 处理
    const result = await processor.process({
      user: lastUser,
      agent,
      messages: /* ... */,
      tools,
      model,
    })

    // 9. 检查结果
    if (result === "stop") break
    if (result === "compact") {
      // 需要压缩上下文
      await SessionCompaction.create({ sessionID, agent, model, auto: true })
    }
  }
})
```

---

## 七、Session 状态管理

### 7.1 SessionStatus

```typescript
export namespace SessionStatus {
  export const Info = z.union([
    z.object({ type: z.literal("idle") }), // 空闲
    z.object({ type: z.literal("retry"), attempt: z.number(), message: z.string(), next: z.number() }), // 重试中
    z.object({ type: z.literal("busy") }), // 忙碌
  ])

  // 内存状态存储
  const state = Instance.state(() => {
    const data: Record<string, Info> = {}
    return data
  })

  export function set(sessionID: string, status: Info) {
    Bus.publish(Event.Status, { sessionID, status })
    if (status.type === "idle") {
      delete state()[sessionID]
      return
    }
    state()[sessionID] = status
  }
}
```

---

### 7.2 状态转换

```
┌─────────┐     用户输入      ┌─────────┐
│  idle   │ ───────────────► │  busy   │
│ (空闲)   │                  │ (忙碌)   │
└─────────┘                  └─────────┘
    ▲                              │
    │   LLM 处理完成               │   调用 LLM
    │                              │
    │   ┌─────────────┐            │
    └───│   retry    │◄───────────┘
        │  (重试中)   │
        └─────────────┘
```

---

## 八、具体任务示例：完整对话流程

### 8.1 场景描述

用户在 OpenCode 中输入："请读取项目根目录下的 package.json 文件"

---

### 8.2 完整执行流程

**步骤 1: 用户输入 → Session 创建**

用户: "请读取项目根目录下的 package.json 文件"

如果是新会话：

```typescript
// 创建 Session
const session = await Session.create({
  title: "New session - 2024-01-01T00:00:00.000Z",
  permission: [], // 继承 Agent 权限
})
// session.id = "session_xxx"
```

**步骤 2: 创建 User Message**

```typescript
const agent = await Agent.get("build") // 默认使用 build Agent

const userMessage: MessageV2.User = {
  id: "message_001",
  sessionID: "session_xxx",
  role: "user",
  agent: "build",
  model: { providerID: "anthropic", modelID: "claude-3-5-sonnet" },
  time: { created: Date.now() },
}

await Session.updateMessage(userMessage)

// 添加 TextPart
await Session.updatePart({
  id: "part_001",
  messageID: "message_001",
  sessionID: "session_xxx",
  type: "text",
  text: "请读取项目根目录下的 package.json 文件",
  synthetic: false,
})
```

**步骤 3: Session Loop 开始处理**

位置: packages/opencode/src/session/prompt.ts:270

```typescript
SessionStatus.set(sessionID, { type: "busy" })

// 获取消息历史
const msgs = await MessageV2.filterCompacted(MessageV2.stream(sessionID))
// msgs = [
//   { info: userMessage, parts: [TextPart] }
// ]
```

**步骤 4: 获取 Agent 配置**

```typescript
const agent = await Agent.get("build")
// agent = {
//   name: "build",
//   mode: "primary",
//   permission: [ /* 权限规则 */ ],
//   // ...
// }
```

**步骤 5: 创建 Assistant Message**

```typescript
const assistantMessage: MessageV2.Assistant = {
  id: "message_002",
  sessionID: "session_xxx",
  parentID: "message_001",
  role: "assistant",
  agent: "build",
  mode: "build",
  modelID: "claude-3-5-sonnet-20241022",
  providerID: "anthropic",
  cost: 0,
  tokens: { input: 0, output: 0, reasoning: 0, cache: { read: 0, write: 0 } },
  path: { cwd: "/Users/chenxi/project", root: "/Users/chenxi/project" },
  time: { created: Date.now() },
}

await Session.updateMessage(assistantMessage)
```

**步骤 6: 解析可用工具**

位置: packages/opencode/src/session/prompt.ts:574-582

```typescript
const tools = await resolveTools({
  agent,
  session,
  model,
  bypassAgentCheck: false,
  messages: msgs,
})
// tools = { read: Tool, bash: Tool, edit: Tool, ... }
```

**步骤 7: 调用 LLM**

位置: packages/opencode/src/session/llm.ts

```typescript
const result = await LLM.stream({
  user: userMessage,
  sessionID: "session_xxx",
  model,
  agent,
  system: [
    /* 系统提示 */
  ],
  messages: [
    /* 转换后的消息 */
  ],
  tools,
  abort,
})
```

LLM 分析后决定调用 read 工具：

**步骤 8: LLM 返回 tool-call**

```json
// AI SDK 生成 tool-call 事件
{
  type: "tool-call",
  toolCallId: "call_001",
  toolName: "read",
  input: { filePath: "package.json" }
}
```

**步骤 9: SessionProcessor 处理 tool-call**

位置: packages/opencode/src/session/processor.ts:126-141

```typescript
case "tool-call": {
  // 创建 ToolPart (running)
  await Session.updatePart({
    id: "part_002",
    messageID: "message_002",
    sessionID: "session_xxx",
    type: "tool",
    callID: "call_001",
    tool: "read",
    state: {
      status: "running",
      input: { filePath: "package.json" },
      time: { start: Date.now() },
    },
  })
  break
}
```

**步骤 10: 执行 Read Tool**

位置: packages/opencode/src/tool/read.ts

```typescript
const result = await ReadTool.execute(
  { filePath: "package.json" },
  {
    sessionID: "session_xxx",
    messageID: "message_002",
    agent: "build",
    abort,
    // ...
  },
)
// result = {
//   title: "package.json",
//   output: "<file>\n00001| {\n00002|   \"name\": \"opencode\",\n...</file>",
//   metadata: { truncated: false }
// }
```

**步骤 11: 处理 tool-result**

位置: packages/opencode/src/session/processor.ts:172-193

```typescript
case "tool-result": {
  await Session.updatePart({
    ...match,
    state: {
      status: "completed",
      input: { filePath: "package.json" },
      output: "<file>\n00001| {\n00002|   \"name\": \"opencode\",\n...</file>",
      title: "package.json",
      time: { start: 1700000000000, end: 1700000000100 },
    },
  })
  break
}
```

**步骤 12: LLM 接收工具结果继续生成**

LLM 收到 read 工具的输出后，生成最终回复：

```json
// 最终的 Assistant Message
{
  role: "assistant",
  finish: "stop",
  parts: [
    { type: "text", text: "我已读取了 package.json 文件，内容如下：" },
    { type: "tool", tool: "read", state: { status: "completed", ... } },
    { type: "text", text: "```json\n{\n  \"name\": \"opencode\",\n  \"version\": \"1.0.0\",\n  ...\n}\n```" }
  ]
}
```

**步骤 13: Session Loop 结束**

```typescript
// 检查是否结束
if (lastAssistant?.finish && !["tool-calls", "unknown"].includes(lastAssistant.finish)) {
  break // 结束循环
}

SessionStatus.set(sessionID, { type: "idle" })
```

---

### 8.3 完整时序图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户输入                                     │
│           "请读取项目根目录下的 package.json 文件"                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Session.create()                                                  │
│  - 创建 Session (id: session_xxx)                                   │
│  - 设置初始权限                                                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SessionPrompt.prompt()                                            │
│  - 创建 User Message (message_001)                                  │
│  - 设置 agent: "build"                                             │
│  - 添加 TextPart                                                   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SessionPrompt.loop()                                              │
│                                                                 │
│  1. SessionStatus.set("busy")                                    │
│  2. MessageV2.stream() 获取历史                                   │
│  3. Agent.get("build") 获取配置                                   │
│  4. SessionProcessor.create() 创建处理器                          │
│  5. resolveTools() 解析可用工具                                   │
│  6. LLM.stream() 调用 AI                                          │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LLM 处理                                                          │
│                                                                 │
│  1. 构建系统提示                                                   │
│  2. 转换消息格式                                                   │
│  3. 调用 AI 模型                                                  │
│  4. AI 返回 tool-call: read({ filePath: "package.json" })       │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SessionProcessor.process()                                        │
│                                                                 │
│  1. tool-call 事件:                                               │
│     - Session.updatePart({ status: "running" })                   │
│                                                                 │
│  2. 执行 ReadTool:                                                │
│     - Bun.file().text()                                          │
│     - 返回文件内容                                                │
│                                                                 │
│  3. tool-result 事件:                                             │
│     - Session.updatePart({ status: "completed", output: "..." })  │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LLM 接收工具结果                                                  │
│  AI 生成最终回复                                                   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  SessionLoop 结束                                                  │
│  SessionStatus.set("idle")                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 九、关键代码位置索引

| 功能 | 文件 | 行号 |
|------|------|------|
| Session 定义 | session/index.ts | 52-93 |
| Session 创建 | session/index.ts | 206-247 |
| Session Fork | session/index.ts | 158-198 |
| Session 事件 | session/index.ts | 105-138 |
| Message 定义 | session/message-v2.ts | 39-299 |
| Part 类型 | session/message-v2.ts | 39-299 |
| ToolPart 状态 | session/message-v2.ts | 223-290 |
| Session Loop | session/prompt.ts | 256-667 |
| LLM.stream | session/llm.ts | 46-200 |
| 工具解析 | session/prompt.ts | 676-854 |
| 工具状态处理 | session/processor.ts | 103-221 |
| Session 状态 | session/status.ts | 6-75 |

---

## 十、总结

OpenCode 的 Session 系统是整个架构的核心枢纽：

1. **数据结构**: Session 包含 ID、权限、时间戳等元数据，通过 Message 存储对话历史

2. **Message-Part 架构**:
   - Message 分为 User 和 Assistant 两种角色
   - Part 包含 Text、Reasoning、Tool、File 等多种类型
   - Tool 通过 ToolPart 存储，包含完整的状态生命周期

3. **与 Agent 的交互**:
   - 每个 Session 关联一个 Agent
   - Agent 权限与 Session 权限通过 PermissionNext.merge() 合并
   - 子 Agent 通过 parentID 关联父 Session

4. **与 Tool 的交互**:
   - Tool 作为 Part 存储在 Message 中
   - 状态经历 pending → running → completed/error
   - Tool Context 包含 sessionID 用于追踪

5. **与 LLM 的交互**:
   - 通过 LLM.stream() 调用 AI 模型
   - MessageV2.toModelMessages() 转换内部格式为 AI SDK 格式
   - Loop 循环处理多轮对话和工具调用

6. **状态管理**:
   - SessionStatus 管理内存状态（idle/busy/retry）
   - Storage 持久化存储 Session 和 Message

这种设计确保了完整的对话历史追踪、灵活的工具调用管理、以及可扩展的 Agent 系统支持。
