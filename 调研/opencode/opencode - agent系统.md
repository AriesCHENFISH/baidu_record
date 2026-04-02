# OpenCode Agent 系统

OpenCode 的 Agent 系统是一个分层架构，支持主 Agent 和子 Agent 两种类型。

---

## 一、核心代码指路

### 1.1 Agent 定义与配置

| 文件 | 说明 |
|------|------|
| `packages/opencode/src/agent/agent.ts` | Agent 核心定义，包含所有内置 Agent 的配置 |
| `packages/opencode/src/config/config.ts` | Agent 配置的 Zod schema 定义 (670-757 行) |
| `packages/opencode/src/agent/prompt/*.txt` | 各个 Agent 的系统提示词模板 |

**Agent 配置的 Zod Schema：**

```typescript
export const Agent = z
  .object({
    model: z.string().optional(),
    variant: z
      .string()
      .optional()
      .describe("Default model variant for this agent (applies only when using the agent's configured model)."),
    temperature: z.number().optional(),
    top_p: z.number().optional(),
    prompt: z.string().optional(),
    tools: z.record(z.string(), z.boolean()).optional().describe("@deprecated Use 'permission' field instead"),
    disable: z.boolean().optional(),
    description: z.string().optional().describe("Description of when to use the agent"),
    mode: z.enum(["subagent", "primary", "all"]).optional(),
    hidden: z
      .boolean()
      .optional()
      .describe("Hide this subagent from the @ autocomplete menu (default: false, only applies to mode: subagent)"),
    options: z.record(z.string(), z.any()).optional(),
    color: z
      .union([
        z.string().regex(/^#[0-9a-fA-F]{6}$/, "Invalid hex color format"),
        z.enum(["primary", "secondary", "accent", "success", "warning", "error", "info"]),
      ])
      .optional()
      .describe("Hex color code (e.g., #FF5733) or theme color (e.g., primary)"),
    steps: z
      .number()
      .int()
      .positive()
      .optional()
      .describe("Maximum number of agentic iterations before forcing text-only response"),
    maxSteps: z.number().int().positive().optional().describe("@deprecated Use 'steps' field instead."),
    permission: Permission.optional(),
  })
  .catchall(z.any())
  .transform((agent, ctx) => {
    const knownKeys = new Set([
      "name",
      "model",
      "variant",
      "prompt",
      "description",
      "temperature",
      "top_p",
      "mode",
      "hidden",
      "color",
      "steps",
      "maxSteps",
      "options",
      "permission",
      "disable",
      "tools",
    ])

    // Extract unknown properties into options
    const options: Record<string, unknown> = { ...agent.options }
    for (const [key, value] of Object.entries(agent)) {
      if (!knownKeys.has(key)) options[key] = value
    }

    // Convert legacy tools config to permissions
    const permission: Permission = {}
    for (const [tool, enabled] of Object.entries(agent.tools ?? {})) {
      const action = enabled ? "allow" : "deny"
      // write, edit, patch, multiedit all map to edit permission
      if (tool === "write" || tool === "edit" || tool === "patch" || tool === "multiedit") {
        permission.edit = action
      } else {
        permission[tool] = action
      }
    }
    Object.assign(permission, agent.permission)

    // Convert legacy maxSteps to steps
    const steps = agent.steps ?? agent.maxSteps

    return { ...agent, options, permission, steps } as typeof agent & {
      options?: Record<string, unknown>
      permission?: Permission
      steps?: number
    }
  })
  .meta({
    ref: "AgentConfig",
  })

export type Agent = z.infer<typeof Agent>
```

**Summary Agent Prompt 示例：**

```
Summarize what was done in this conversation. Write like a pull request description.

Rules:
- 2-3 sentences max
- Describe the changes made, not the process
- Do not mention running tests, builds, or other validation steps
- Do not explain what the user asked for
- Write in first person (I added..., I fixed...)
- Never ask questions or add new questions
- If the conversation ends with an unanswered question to the user, preserve that exact question
- If the conversation ends with an imperative statement or request to the user (e.g. "Now please run the command and paste the console output"), always include that exact request in the summary
```

---

### 1.2 Agent 调用与执行

| 文件 | 说明 |
|------|------|
| `packages/opencode/src/tool/task.ts` | Task Tool 实现，核心的子 Agent 调用机制 |
| `packages/opencode/src/session/prompt.ts` | Session 提示处理，包含 Agent 选择和执行循环 |
| `packages/opencode/src/session/processor.ts` | LLM 消息处理和工具调用管理 |
| `packages/opencode/src/session/llm.ts` | LLM 流式调用实现 |

---

### 1.3 权限

| 文件 | 说明 |
|------|------|
| `packages/opencode/src/permission/next.ts` | 权限系统核心，管理 Agent 权限规则 |

---

## 二、Agent 类型与配置

### 2.1 内置 Agent 列表

在 agent.ts 中定义了以下内置 Agent：

| Agent | Mode | 说明 |
|-------|------|------|
| **build** | primary | Build 是启用了所有工具的默认主代理。这是用于需要完全访问文件操作和系统命令的开发工作的标准代理。 |
| **plan** | primary | 一个专为规划和分析设计的受限代理。我们使用权限系统来为您提供更多控制权，并防止意外更改。 file edit : ask（所有写入、补丁和编辑）bash: ask（所有bash命令） |
| **general** | subagent | 通用多任务代理（并行执行）一个用于研究复杂问题和执行多步骤任务的通用代理。拥有完整的工具访问权限（todo 除外），因此可以在需要时修改文件。可用于并行运行多个工作单元。为什么todo除外：todo通常是全局级别的系统工具，存储的是用户/主代理的整体待办清单，而不是某个子任务的临时任务。 |
| **explore** | subagent | 代码库探索专用一个用于探索代码库的快速只读代理。无法修改文件。当需要按模式快速查找文件、搜索代码中的关键字或回答有关代码库的问题时，使用此代理。 |
| **compaction** | primary/hidden | 对话历史压缩（内部使用）隐藏的系统代理，将长上下文压缩为较小的摘要。它会在需要时自动运行，且无法在 UI 中选择。 |
| **title** | primary/hidden | 生成会话标题（内部） |
| **summary** | primary/hidden | 生成会话概要 |

**Agent 定义代码：**

```typescript
const result: Record<string, Info> = {
  build: {        // 主 Agent，默认执行工具
    name: "build",
    mode: "primary",
    permission: PermissionNext.merge(defaults, /* build特定权限 */),
  },
  plan: {         // 主 Agent，计划模式，禁止编辑工具
    name: "plan",
    mode: "primary",
    permission: /* 只读权限 */,
  },
  general: {      // 子 Agent，通用的多步骤任务
    name: "general",
    mode: "subagent",
  },
  explore: {      // 子 Agent，快速探索代码库
    name: "explore",
    mode: "subagent",
    prompt: PROMPT_EXPLORE,
  },
  compaction: {   // 隐藏 Agent，上下文压缩
    name: "compaction",
    mode: "primary",
    hidden: true,
  },
  title: {        // 隐藏 Agent，生成标题
    name: "title",
    mode: "primary",
    hidden: true,
  },
  summary: {      // 隐藏 Agent，生成摘要
    name: "summary",
    mode: "primary",
    hidden: true,
  },
}
```

---

### 2.2 Action 含义

| Action | 含义 |
|--------|------|
| allow | 直接允许，无需询问用户 |
| deny | 直接拒绝，抛出异常 |
| ask | 询问用户，等待授权 |

---

### 2.3 Agent Info Schema

```typescript
export const Info = z.object({
  name: z.string(), // Agent 名称
  description: z.string().optional(), // 描述
  mode: z.enum(["subagent", "primary", "all"]), // 模式
  native: z.boolean().optional(), // 是否内置
  hidden: z.boolean().optional(), // 是否隐藏
  topP: z.number().optional(), // 模型 topP
  temperature: z.number().optional(), // 模型 temperature
  color: z.string().optional(), // UI 颜色
  permission: PermissionNext.Ruleset, // 权限规则
  model: z
    .object({
      // 模型配置
      modelID: z.string(),
      providerID: z.string(),
    })
    .optional(),
  variant: z.string().optional(), // 模型变体
  prompt: z.string().optional(), // 系统提示词
  options: z.record(z.string(), z.any()), // 自定义选项
  steps: z.number().int().positive().optional(), // 最大步数
})
```

---

## 三、父子 Agent 调用关系

### 3.1 Task Tool 核心逻辑

Task Tool 是实现父子 Agent 调用的核心机制，定义在 `packages/opencode/src/tool/task.ts`：

```typescript
export const TaskTool = Tool.define("task", async (ctx) => {
  // 获取可用的子 Agent 列表
  const agents = await Agent.list().then((x) => x.filter((a) => a.mode !== "primary"))

  // 根据父 Agent 权限过滤可调用的子 Agent
  const caller = ctx?.agent
  const accessibleAgents = caller
    ? agents.filter((a) => PermissionNext.evaluate("task", a.name, caller.permission).action !== "deny")
    : agents

  // 参数定义
  const parameters = z.object({
    description: z.string(), // 任务描述
    prompt: z.string(), // 任务提示词
    subagent_type: z.string(), // 子 Agent 类型
    task_id: z.string().optional(), // 可选：恢复之前的任务（用于返回之前的子任务）
    command: z.string().optional(),
  })

  // 执行逻辑
  async function execute(params, ctx) {
    // 4.1 权限检查
    await ctx.ask({
      permission: "task",
      patterns: [params.subagent_type],
      always: ["*"],
    })

    //  获取目标 Agent 配置
    const agent = await Agent.get(params.subagent_type)

    //  创建子 Session*******
    const session = await Session.create({
      parentID: ctx.sessionID, // 关联父 Session
      title: params.description + ` (@${agent.name} subagent)`,
      permission: [
        // 默认禁止
        { permission: "todowrite", pattern: "*", action: "deny" },
        { permission: "todoread", pattern: "*", action: "deny" },
        // 如果子 Agent 没有 task 权限，禁止其调用其他子 Agent
        ...(hasTaskPermission ? [] : [{ permission: "task", pattern: "*", action: "deny" }]),
      ],
    })

    // 调用 SessionPrompt.prompt 执行子 Agent
    const result = await SessionPrompt.prompt({
      messageID,
      sessionID: session.id,
      model: { modelID, providerID },
      agent: agent.name,
      tools: {
      
      },
      parts: promptParts,
    })

    //  返回结果
    return {
      title: params.description,
      metadata: { summary, sessionId: session.id, model },
      output: text, // 子 Agent 的输出文本
    }
  }
})
```

---

### 3.2 子 Session 创建流程

```
父 Agent (Session A)
    │
    │ 调用 Task Tool
    ▼
┌─────────────────────────────────────────┐
│  TaskTool.execute()                     │
│                                         │
│  1. 权限检查 (ctx.ask)                  │
│  2. 获取子 Agent 配置                   │
│  3. 创建子 Session (parentID = A)       │
│  4. 调用 SessionPrompt.prompt()        │
│  5. 返回结果                           │
└─────────────────────────────────────────┘
    │
    ▼
子 Agent (Session B, parentID = A)
    │
    │ 执行任务...
    │
    ▼
返回结果给父 Agent
```

---

## 四、Agent 执行流程

### 4.1 整体执行流程

```
用户输入
    │
    ▼
SessionPrompt.prompt()  ─────────────────────┐
    │                                          │
    │ 创建 User Message                        │
    │                                          │
    ▼                                          │
选择 Agent (Agent.get() 或 @ 提及)             │
    │                                          │
    ▼                                          │
权限检查 (PermissionNext.evaluate)             │
    │                                          │
    ▼                                          │
LLM.stream() ───────────────────────────────┤
    │                                          │
    │ 流式输出                                  │
    ▼                                          │
Tool 调用循环                                  │
    │                                          │
    ├── Tool 是 Task Tool? ──► 递归调用子 Agent │
    │                                          │
    ├── Tool 需要权限? ──► PermissionNext.ask() │
    │                                          │
    └── 执行 Tool                              │
    │                                          │
    ▼                                          │
返回结果 ◄────────────────────────────────────┘
```

---

### 4.2 SessionPrompt.loop 核心逻辑

位置: `packages/opencode/src/session/prompt.ts:256-667`

```typescript
export const loop = fn(Identifier.schema("session"), async (sessionID) => {
  while (true) {
    // 1. 获取消息历史
    let msgs = await MessageV2.filterCompacted(MessageV2.stream(sessionID))

    // 2. 检查是否有待处理的子任务
    const task = tasks.pop()
    if (task?.type === "subtask") {
      // 执行子 Agent 任务
      const taskTool = await TaskTool.init()
      const result = await taskTool.execute(taskArgs, taskCtx)
      continue
    }

    // 3. 检查是否需要上下文压缩
    if (lastFinished && await SessionCompaction.isOverflow({ tokens })) {
      await SessionCompaction.create({ sessionID, agent, model, auto: true })
      continue
    }

    // 4. 正常处理：创建 Assistant Message 并处理
    const processor = SessionProcessor.create({ /* ... */ })

    // 5. 解析可用工具（考虑权限）
    const tools = await resolveTools({ agent, session, model, /* ... */ })

    // 6. 调用 LLM 处理
    const result = await processor.process({
      user: lastUser,
      agent,
      messages: /* ... */,
      tools,
      model,
    })

    if (result === "stop") break
  }
})
```

---

## 五、权限系统

### 5.1 权限规则定义

位置: `packages/opencode/src/permission/next.ts:29-43`

```typescript
export const Rule = z.object({
  permission: z.string(), // 权限名称 (如 "task", "edit", "read")
  pattern: z.string(), // 匹配模式 (如 "*", "src/**/*.ts")
  action: z.enum(["allow", "deny", "ask"]), // 动作
})

export type Ruleset = Rule[]
```

---

### 5.2 默认权限配置

位置: `packages/opencode/src/agent/agent.ts:51-73`

```typescript
const defaults = PermissionNext.fromConfig({
  "*": "allow", // 默认允许所有
  doom_loop: "ask", // 检测到死循环时询问
  external_directory: {
    // 外部目录访问
    "*": "ask",
    [Truncate.GLOB]: "allow",
  },
  question: "deny", // 默认禁止提问
  plan_enter: "deny", // 默认禁止进入计划模式
  plan_exit: "deny", // 默认禁止退出计划模式
  read: {
    // 读取权限
    "*": "allow",
    "*.env": "ask", // 环境文件需要询问
    "*.env.*": "ask",
    "*.env.example": "allow",
  },
})
```

---

### 5.3 权限评估流程

位置: `packages/opencode/src/permission/next.ts:127-157`

```typescript
export const ask = fn(
  Request.partial({ id: true }).extend({
    ruleset: Ruleset,
  }),
  async (input) => {
    for (const pattern of request.patterns) {
      const rule = evaluate(request.permission, pattern, ruleset)

      if (rule.action === "deny") {
        throw new DeniedError(/* ... */) // 直接拒绝
      }

      if (rule.action === "ask") {
        // 发布事件等待用户授权
        Bus.publish(Event.Asked, info)
        return new Promise((resolve, reject) => {
          // 等待用户响应
        })
      }

      if (rule.action === "allow") continue // 允许
    }
  },
)
```

---

## 六、具体任务示例：父 Agent 调用子 Agent 探索代码库

### 6.1 场景描述

用户要求主 Agent (build) 探索项目中某个功能的实现。主 Agent 决定调用 explore 子 Agent 来完成这个任务。

### 6.2 完整调用流程

**1. 用户输入**

```
"探索一下项目中的用户认证是怎么实现的?"
```

**2. 创建 User Message**

```typescript
const agent = await Agent.get(input.agent ?? (await Agent.defaultAgent()))
// agent = { name: "build", mode: "primary", ... }

const info: MessageV2.Info = {
  id: "msg_xxx",
  role: "user",
  sessionID: "session_xxx",
  agent: "build", // 使用 build 作为主 Agent
  model: 
  },
}
```

**3. Agent 执行循环**

主 Agent build 收到用户请求后，分析(具体的在后面)需要探索代码库，决定调用 explore 子 Agent。

该步骤后的结果是一个tool call

子agent的创建是通过task tool去触发的

**4. 主 Agent 调用 Task Tool**

```json
{
  "tool": "task",
  "input": {
    "description": "探索用户认证实现",
    "prompt": "请探索项目中用户认证的实现:\n1. 查找登录相关的代码\n2. 查找密码验证逻辑\n3. 查找 session/token 管理\n\n请返回关键文件路径和实现概要。",
    "subagent_type": "explore",  // 指定子 Agent 类型
  }
}
```

**5. Task Tool 执行**

```typescript
async function execute(params, ctx) {
  // 权限检查：build 是否有权限调用 explore?
  await ctx.ask({
    permission: "task",
    patterns: ["explore"],
    always: ["*"],
  })

  // 获取 explore Agent 配置
  const agent = await Agent.get("explore")
  // agent = {
  //   name: "explore",
  //   mode: "subagent",
  //   prompt: PROMPT_EXPLORE,
  //   permission: [
  //     { permission: "*", action: "deny" },
  //     { permission: "grep", action: "allow" },
  //     { permission: "glob", action: "allow" },
  //     { permission: "read", action: "allow" },
  //     // ...
  //   ]
  // }

  // 创建子 Session
  const session = await Session.create({
    parentID: ctx.sessionID, // 父
    title: "探索用户认证实现 (@explore subagent)",
    permission: [
      { permission: "todowrite", pattern: "*", action: "deny" },
      { permission: "todoread", pattern: "*", action: "deny" },
      // explore 没有 task 权限，禁止其调用其他子 Agent
      { permission: "task", pattern: "*", action: "deny" },
    ],
  })
  // session.id = 子

  // 调用子 Agent 执行
  const result = await SessionPrompt.prompt({
    messageID: "message_yyy",
    sessionID: "session_yyy",
    model: {
      /* 使用 explore 的模型或继承父模型 */
    },
    agent: "explore",
    tools: {
      todowrite: false,
      todoread: false,
      task: false, // explore 不能调用其他子 Agent
    },
    parts: [{ type: "text", text: params.prompt }],
  })
}
```

**6. 子 Agent 执行**

```typescript
// 子 Agent 的 loop 流程
// 实则就是每一个agent 和llm的交互
while (true) {
  // 加载 explore Agent 的系统提示词
  // prompt = PROMPT_EXPLORE

  // 解析工具（仅限 explore 的权限范围内）
  const tools = await resolveTools({
    agent: await Agent.get("explore"),  // 只有 grep/glob/read/bash 权限
    session: session_yyy,
    model,
  })
  // tools = { grep, glob, list, bash, webfetch, read, ... }

  // LLM 执行，调用llm让agent做决策
  const result = await processor.process({
    user: lastUser, //当前的用户命令
    agent: "explore", //agent的系统提示词和默认的设置
    messages: /* ... */, //上下文，具体如何调取看后面的storage
    tools, //可用工具列表
    model,
  })
  
  // packages/opencode/src/session/llm.ts
  // packages/opencode/src/session/processor.ts
  // 组织素材的代码

  // explore Agent 执行搜索和读取操作
  // - 使用 grep 搜索 "login", "auth", "password"
  // - 使用 glob 查找相关文件
  // - 使用 read 读取关键文件

  break
}
```

**7. 返回结果给父 Agent**

```typescript
const text = result.parts.findLast((x) => x.type === "text")?.text ?? ""

return {
  title: params.description,
  metadata: {
    summary: [
      // 工具调用摘要 
    ],
    sessionId: "session_yyy",
    model: {
      //模型信息 
    },
  },
  output: [
    `task_id: session_yyy`,
    "",
    "<task_result>",
    text, // explore Agent 的输出
    "</task_result>",
  ].join("\n"),
}
```

**8. 父 Agent 处理结果**

主 Agent build 收到 Task Tool 的输出后，向用户展示探索结果。

---

### 6.3 调用关系图

```
┌──────────────────────────────────────────────────────────────────┐
│                        用户输入                                   │
│              "探索一下项目中的用户认证是怎么实现的?"                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    主 Agent (build)                              │
│  sessionID: session_xxx                                          │
│  mode: primary                                                   │
│  permission: { "*": "allow", "task": "allow", ... }             │
└──────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  分析任务         │
                    │  调用 Task Tool   │
                    └─────────┬─────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Task Tool                                     │
│                                                                 │
│  1. ctx.ask({ permission: "task", patterns: ["explore"] })    │
│  2. Agent.get("explore")                                        │
│  3. Session.create({                                            │
│       parentID: "session_xxx",                                   │
│       permission: [                                             │
│         { permission: "todowrite", action: "deny" },            │
│         { permission: "todoread", action: "deny" },             │
│         { permission: "task", action: "deny" },  // explore    │
│       ]                                                          │
│     })                                                           │
│  4. SessionPrompt.prompt({ sessionID: "session_yyy", ... })    │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    子 Agent (explore)                            │
│  sessionID: session_yyy (parentID: session_xxx)                 │
│  mode: subagent                                                  │
│  permission: {                                                  │
│    "*": "deny",                                                 │
│    "grep": "allow",                                             │
│    "glob": "allow",                                             │
│    "read": "allow",                                             │
│    "bash": "allow",                                             │
│    ...                                                           │
│  }                                                               │
└──────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │  执行搜索任务     │
                    │  - grep 搜索      │
                    │  - glob 查找      │
                    │  - read 读取      │
                    └─────────┬─────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    返回结果                                       │
│  output: "找到以下认证相关文件: ..."                               │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    主 Agent (build)                              │
│  向用户展示:                                                      │
│  "我通过 explore 子 Agent 探索了项目，发现用户认证实现如下: ..."    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 七、关键代码位置索引

| 功能 | 文件 | 行号 |
|------|------|------|
| Agent 定义 | agent/agent.ts | 76-202 |
| Agent Schema | agent/agent.ts | 24-49 |
| Task Tool 定义 | tool/task.ts | 28-203 |
| 子 Session 创建 | tool/task.ts | 67-103 |
| Session Prompt loop | session/prompt.ts | 256-667 |
| 权限评估 | permission/next.ts | 127-157 |
| 工具解析 | session/prompt.ts | 676-854 |
| Agent 选择 | session/prompt.ts | 857 |
| 默认权限 | agent/agent.ts | 54-73 |

---

## 八、总结

OpenCode 的 Agent 系统采用了以下核心设计，其实和c++里的面向对象编程类似：

1. **分层架构**: 主 Agent (primary) 与子 Agent (subagent) 分离，通过 Task Tool 实现调用
2. **权限隔离**: 每个 Agent 有独立的权限规则，子 Agent 继承但限制父 Agent 的权限
3. **Session 关联**: 子 Agent 通过 parentID 关联父 Session，形成调用链
4. **工具过滤**: 根据 Agent 权限动态过滤可用工具，确保安全隔离
5. **递归执行**: Task Tool 在子 Agent 的 Session 中递归调用 SessionPrompt.prompt()

---

假设你让 Primary 父 Agent 做「基于 data.csv 写分析脚本 + 执行测试 + 生成测试报告」，整个递归过程是这样的：

**父 Agent 调用 Task Tool，启动第一层子 Agent（写文件）**

Primary 父 Agent（主 Agent）在「父会话 session_123」里，调用 Task Tool，指定子 Agent 为file_writer，创建「子会话 session_456」；在 session_456 里，执行SessionPrompt.prompt()—— 让file_writer子 Agent 的 LLM 思考，完成「写 analysis.py」的任务。

**第一层子 Agent 递归调用 Task Tool，启动第二层子 Agent（生成报告）**

file_writer子 Agent 写完脚本后，发现还需要「生成代码说明文档」，但它自己不擅长写文档，于是它也调用 Task Tool（如果有权限的话）；Task Tool 在file_writer的「子会话 session_456」里，创建「孙会话 session_789」（parentID=session_456），指定子 Agent 为doc_writer；在 session_789 里，再次执行SessionPrompt.prompt()—— 让doc_writer孙 Agent 的 LLM 思考，完成「生成 analysis.py 说明文档」的任务。

边界就是task权限是否被禁用。

这种设计使得系统既支持复杂的多步骤任务分解，又保持了良好的安全边界
