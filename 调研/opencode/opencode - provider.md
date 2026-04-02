# OpenCode Provider 系统

OpenCode 的 Provider 系统是一个高度抽象化、智能化的 AI 模型提供者管理层。它不仅仅是一个简单的 API 封装，而是在多模型支持、差异适配、智能配置等方面做了大量创新，与传统 AI 开发框架（如 LangChain、LlamaIndex、OpenAI SDK）有着显著的不同。

---

## 一、核心设计理念对比

### 1.1 传统方式的局限性

**LangChain/LlamaIndex 方式:**

```javascript
// LangChain 示例
const llm = new ChatOpenAI({
  model: "gpt-4",
  temperature: 0.7,
})

// 需要单独配置 Anthropic
const claude = new ChatAnthropic({
  model: "claude-3-opus-20240229",
})

// 每个模型需要独立配置
// 无法统一管理多 provider
```

**OpenAI SDK 方式:**

```javascript
// 仅支持 OpenAI 官方 API
const openai = new OpenAI({
  apiKey: process.env.OPENAI_API_KEY,
})
```

### 1.2 OpenCode 的创新设计

OpenCode 的 Provider 系统追求:

1. **统一抽象** - 单一接口管理 20+ 个 AI Provider
2. **智能适配** - 自动处理各 Provider 的 API 差异
3. **动态加载** - 运行时加载 SDK，按需初始化
4. **零配置** - 从 ModelsDev 数据库自动获取模型信息

---

## 二、核心代码文件

### 2.1 Provider 核心

| 文件路径 | 作用 |
|---------|------|
| `packages/opencode/src/provider/provider.ts` | Provider 核心命名空间，包含 Provider/Model 定义、加载逻辑、SDK 管理 |
| `packages/opencode/src/provider/models.ts` | ModelsDev 数据源，远程模型列表 |
| `packages/opencode/src/provider/transform.ts` | Provider 差异适配层 |
| `packages/opencode/src/provider/auth.ts` | 认证管理（OAuth/API Key） |

### 2.2 交互相关

| 文件路径 | 作用 |
|---------|------|
| `packages/opencode/src/session/llm.ts` | LLM 调用，使用 Provider 获取 LanguageModel |
| `packages/opencode/src/session/system.ts` | 系统提示，根据 model 选择模板 |
| `packages/opencode/src/agent/agent.ts` | Agent 生成，使用 Provider 获取默认模型 |
| `packages/opencode/src/config/config.ts` | 配置管理，Provider 配置 Schema |

---

## 三、OpenCode Provider 核心特性

### 3.1 20+ Provider 统一支持

```typescript
// packages/opencode/src/provider/provider.ts:58-81

const BUNDLED_PROVIDERS: Record<string, (options: any) => SDK> = {
  "@ai-sdk/amazon-bedrock": createAmazonBedrock,
  "@ai-sdk/anthropic": createAnthropic,
  "@ai-sdk/azure": createAzure,
  "@ai-sdk/google": createGoogleGenerativeAI,
  "@ai-sdk/google-vertex": createVertex,
  "@ai-sdk/openai": createOpenAI,
  "@ai-sdk/openai-compatible": createOpenAICompatible,
  "@openrouter/ai-sdk-provider": createOpenRouter,
  "@ai-sdk/xai": createXai,
  "@ai-sdk/mistral": createMistral,
  "@ai-sdk/groq": createGroq,
  "@ai-sdk/deepinfra": createDeepInfra,
  "@ai-sdk/cerebras": createCerebras,
  "@ai-sdk/cohere": createCohere,
  "@ai-sdk/gateway": createGateway,
  "@ai-sdk/togetherai": createTogetherAI,
  "@ai-sdk/perplexity": createPerplexity,
  "@ai-sdk/vercel": createVercel,
  "@gitlab/gitlab-ai-provider": createGitLab,
  "@ai-sdk/github-copilot": createGitHubCopilotOpenAICompatible,
}
```

**优势对比:**

| 特性 | LangChain | OpenCode |
|------|----------|---------|
| Provider 数量 | ~10 个官方 | 20+ 个官方 |
| 自定义 Provider | 需要开发 Plugin | 内置 Custom Loader |
| 模型列表 | 手动维护 | 自动从 ModelsDev 同步 |
| 模型能力 | 基本支持 | 完整能力建模 |

### 3.2 Model 能力建模

```typescript
// packages/opencode/src/provider/provider.ts:533-602

export const Model = z.object({
  id: z.string(),
  providerID: z.string(),
  api: z.object({
    id: z.string(),
    url: z.string(),
    npm: z.string(), // SDK 包名
  }),
  name: z.string(),
  family: z.string().optional(),
  capabilities: z.object({
    temperature: z.boolean(), // 是否支持温度
    reasoning: z.boolean(), // 是否支持推理
    attachment: z.boolean(), // 是否支持附件
    toolcall: z.boolean(), // 是否支持工具调用
    input: z.object({
      // 输入模态
      text: z.boolean(),
      audio: z.boolean(),
      image: z.boolean(),
      video: z.boolean(),
      pdf: z.boolean(),
    }),
    output: z.object({
      // 输出模态
      text: z.boolean(),
      audio: z.boolean(),
      image: z.boolean(),
      video: z.boolean(),
      pdf: z.boolean(),
    }),
    interleaved: z.union([
      // 交错推理
      z.boolean(),
      z.object({ field: z.enum(["reasoning_content", "reasoning_details"]) }),
    ]),
  }),
  cost: z.object({
    // 价格建模
    input: z.number(),
    output: z.number(),
    cache: z.object({ read: z.number(), write: z.number() }),
  }),
  limit: z.object({
    // 限制建模
    context: z.number(),
    input: z.number().optional(),
    output: z.number(),
  }),
  variants: z.record(z.string(), z.record(z.string(), z.any())).optional(), // 推理变体
})
```

这与其他工具有何不同?

- **LangChain**: 运行时才能知道模型能力，需要开发者自己处理
- **OpenCode**: 编译时就有完整的模型能力定义，可以静态验证

### 3.3 自定义 Loader 机制

```typescript
// packages/opencode/src/provider/provider.ts:90-531

const CUSTOM_LOADERS: Record<string, CustomLoader> = {
  // Anthropic: 自动添加 Beta 头
  async anthropic() {
    return {
      autoload: false,
      options: {
        headers: {
          "anthropic-beta": "claude-code-20250219,interleaved-thinking-2025-05-14",
        },
      },
    }
  },

  // OpenAI: 优先使用 Responses API
  async openai() {
    return {
      autoload: false,
      async getModel(sdk, modelID) {
        return sdk.responses(modelID) // 使用最新 API
      },
    }
  },

  // GitHub Copilot: 根据模型选择 API
  async "github-copilot"() {
    return {
      autoload: false,
      async getModel(sdk, modelID) {
        if (shouldUseCopilotResponsesApi(modelID)) {
          return sdk.responses(modelID)
        }
        return sdk.chat(modelID)
      },
    }
  },

  // Cloudflare AI Gateway: 动态加载
  async "cloudflare-ai-gateway"() {
    return {
      autoload: true,
      async getModel(_sdk, modelID) {
        const { createAiGateway } = await import("ai-gateway-provider")
        const { createUnified } = await import("ai-gateway-provider/providers/unified")
        const aigateway = createAiGateway({ accountId, gateway, apiKey })
        const unified = createUnified()
        return aigateway(unified(modelID))
      },
    }
  },
}
```

---

## 四、Provider 差异适配（Transform）

这是 OpenCode 最核心的创新之一。其他框架通常让开发者自己处理这些差异，而 OpenCode 通过 ProviderTransform 自动处理。

### 4.1 消息格式标准化

```typescript
// packages/opencode/src/provider/transform.ts:44-131

export function normalizeMessages(msgs: ModelMessage[], model: Provider.Model) {
  // 1. Anthropic: 过滤空消息
  if (model.api.npm === "@ai-sdk/anthropic") {
    msgs = msgs
      .map((msg) => {
        if (typeof msg.content === "string") {
          if (msg.content === "") return undefined
          return msg
        }
        // 过滤空文本/推理
        const filtered = msg.content.filter((part) => {
          if (part.type === "text" || part.type === "reasoning") {
            return part.text !== ""
          }
          return true
        })
        return filtered.length === 0 ? undefined : { ...msg, content: filtered }
      })
      .filter((msg) => msg !== undefined)
  }

  // 2. Claude: toolCallId 规范化
  if (model.api.id.includes("claude")) {
    return msgs.map((msg) => {
      if ((msg.role === "assistant" || msg.role === "tool") && Array.isArray(msg.content)) {
        msg.content = msg.content.map((part) => {
          if (part.type === "tool-call" && "toolCallId" in part) {
            return { ...part, toolCallId: part.toolCallId.replace(/[^a-zA-Z0-9_-]/g, "_") }
          }
          return part
        })
      }
      return msg
    })
  }

  // 3. Mistral: toolCallId 必须是 9 位字母数字
  if (model.providerID === "mistral") {
    return msgs.map((msg) => {
      if ((msg.role === "assistant" || msg.role === "tool") && Array.isArray(msg.content)) {
        msg.content = msg.content.map((part) => {
          if (part.type === "tool-call" && "toolCallId" in part) {
            const normalizedId = part.toolCallId
              .replace(/[^a-zA-Z0-9]/g, "")
              .substring(0, 9)
              .padEnd(9, "0")
            return { ...part, toolCallId: normalizedId }
          }
          return part
        })
      }
      return msg
    })
  }
}
```

### 4.2 参数智能配置

```typescript
// packages/opencode/src/provider/transform.ts

// 温度参数默认值 - 每个模型都不同
export function temperature(model: Provider.Model) {
  const id = model.id.toLowerCase()

  // Claude 3.5 推荐 0.5
  if (id.includes("claude-3-5")) return 0.5

  // Codex 推荐 0.2
  if (id.includes("codex")) return 0.2

  // Gemini 2.0 推荐 0.7
  if (id.includes("gemini-2")) return 0.7

  // 其他模型默认 0.6
  return 0.6
}

// Top-P 智能配置
export function topP(model: Provider.Model) {
  const id = model.id.toLowerCase()

  // Qwen 需要 1.0
  if (id.includes("qwen")) return 1

  // MiniMax/Gemini 需要 0.95
  if (id.includes("minimax") || id.includes("gemini")) return 0.95

  return undefined
}

// Top-K 智能配置
export function topK(model: Provider.Model) {
  const id = model.id.toLowerCase()

  // MiniMax M2: 20-40
  if (id.includes("minimax-m2")) {
    if (id.includes("m2.1")) return 40
    return 20
  }

  // Gemini: 64
  if (id.includes("gemini")) return 64

  return undefined
}
```

**对比其他框架:**

| 参数 | LangChain | OpenCode |
|------|----------|---------|
| temperature | 开发者自己设置 | 根据模型智能推荐 |
| topP | 开发者自己设置 | 根据模型自动配置 |
| topK | 不支持 | MiniMax/Gemini 自动配置 |
| maxTokens | 开发者自己计算 | 考虑 reasoning token 自动计算 |

### 4.3 推理变体（Variants）统一

```typescript
// packages/opencode/src/provider/transform.ts:328-450

export function variants(model: Provider.Model): Record<string, Record<string, any>> {
  if (!model.capabilities.reasoning) return {}

  const id = model.id.toLowerCase()

  // 不同 Provider 使用不同的参数名称！
  switch (model.api.npm) {
    case "@ai-sdk/openai":
      // OpenAI: reasoningEffort
      return {
        low: { reasoningEffort: "low" },
        high: { reasoningEffort: "high", reasoningSummary: "auto" },
        xhigh: { reasoningEffort: "xhigh", reasoningSummary: "auto" },
      }

    case "@ai-sdk/anthropic":
      // Anthropic: thinking.budgetTokens
      return {
        high: { thinking: { type: "enabled", budgetTokens: 16000 } },
        max: { thinking: { type: "enabled", budgetTokens: 31999 } },
      }

    case "@ai-sdk/google":
      // Google: thinkingConfig
      return {
        low: { thinkingConfig: { thinkingBudget: 1024 } },
        high: { thinkingConfig: { thinkingBudget: 24576 } },
      }

    case "@openrouter/ai-sdk-provider":
      // OpenRouter: 统一格式
      return {
        low: { reasoning: { effort: "low" } },
        high: { reasoning: { effort: "high" } },
      }

    case "@ai-sdk/github-copilot":
      // Copilot: 不同模型不同格式
      if (model.id.includes("claude")) {
        return { thinking: { thinking_budget: 4000 } }
      }
      return { reasoningEffort: "high", reasoningSummary: "auto" }
  }
}
```

---

## 五、配置与管理

### 5.1 多层配置优先级

```
┌─────────────────────────────────────────────────────────┐
│                    配置优先级                             │
├─────────────────────────────────────────────────────────┤
│  1. ModelsDev 数据库 (远程模型列表)                       │
│        ↓                                                │
│  2. Config 文件 (opencode.json)                         │
│        ↓                                                │
│  3. 环境变量 (ANTHROPIC_API_KEY 等)                     │
│        ↓                                                │
│  4. Auth 存储 (OAuth/API Key 持久化)                    │
│        ↓                                                │
│  5. Plugin 扩展 (第三方认证)                             │
└─────────────────────────────────────────────────────────┘
```

### 5.2 零配置使用

用户无需配置任何内容即可使用：

```json
// opencode.json - 最小配置
{
  "default_model": "claude-sonnet-4-20250514"
}
```

OpenCode 自动从 ModelsDev 获取：

- 模型列表
- 模型能力
- 价格信息
- API 端点
- 默认参数

### 5.3 高级配置示例

```json
// opencode.json - 高级配置
{
  "provider": {
    "anthropic": {
      "options": {
        "apiKey": "sk-...",
        "timeout": 60000
      },
      "whitelist": ["claude-sonnet-4-20250514", "claude-opus-4-20240229"],
      "models": {
        "sonnet": {
          "id": "claude-sonnet-4-20250514",
          "variants": {
            "think": {
              "thinking": {
                "type": "enabled",
                "budgetTokens": 16000
              }
            }
          }
        }
      }
    },
    "openai": {
      "options": {
        "baseURL": "https://api.openai.com/v1"
      }
    }
  },
  "disabled_providers": ["google"],
  "enabled_providers": ["anthropic", "openai", "openrouter"]
}
```

---

## 六、Provider 与其他系统交互

### 6.1 Provider → LLM

```typescript
// packages/opencode/src/session/llm.ts:46-165

export async function stream(input: StreamInput) {
  // 1. 获取 Provider 和配置
  const [language, cfg, provider, auth] = await Promise.all([
    Provider.getLanguage(input.model), // 核心：获取 LanguageModel
    Config.get(),
    Provider.getProvider(input.model.providerID),
    Auth.get(input.model.providerID),
  ])

  // 2. 构建系统提示
  const system = [
    ...(input.agent.prompt ? [input.agent.prompt] : []),
    ...input.system,
    ...(input.user.system ? [input.user.system] : []),
  ].join("\n")

  // 3. 解析工具
  const tools = await resolveTools(input)

  // 4. 调用 AI SDK
  return streamText({
    model: language, // 来自 Provider.getLanguage()
    messages: input.messages,
    tools,
    // ... 其他参数
  })
}
```

### 6.2 Provider → Agent

```typescript
// packages/opencode/src/agent/agent.ts:282-285

export async function generate(input: { description: string }) {
  const defaultModel = input.model ?? (await Provider.defaultModel())
  const model = await Provider.getModel(defaultModel.providerID, defaultModel.modelID)
  const language = await Provider.getLanguage(model)
  // ...
}
```

### 6.3 Provider → Session

```typescript
// packages/opencode/src/session/system.ts

export function provider(model: Provider.Model) {
  // 根据不同 Provider 返回不同的系统提示
  if (model.api.id.includes("gpt-5")) return [PROMPT_CODEX]
  if (model.api.id.includes("claude")) return [PROMPT_ANTHROPIC]
  if (model.api.id.includes("gemini")) return [PROMPT_GEMINI]
  return []
}
```

---

## 七、OpenCode Provider 的独特优势

### 7.1 与 LangChain 对比

| 特性 | LangChain | OpenCode |
|------|----------|---------|
| Provider 数量 | ~10 | 20+ |
| 模型能力建模 | 无 | 完整能力建模 |
| 推理变体 | 无 | 多 Provider 统一 |
| 零配置 | 需要手动配置 | 自动从 ModelsDev 同步 |
| 工具调用差异 | 开发者处理 | 自动适配 |
| 价格计算 | 无 | 内置成本追踪 |

### 7.2 与 OpenAI SDK 对比

| 特性 | OpenAI SDK | OpenCode |
|------|-----------|---------|
| 多 Provider | 不支持 | 20+ Provider |
| 模型列表 | 手动维护 | 自动同步 |
| Provider 差异 | 不适用 | 自动适配 |
| 工具调用 | 仅 OpenAI | 统一接口 |

### 7.3 独特创新点

1. **ModelsDev 数据源**
   - 远程模型数据库
   - 实时同步模型能力
   - 自动更新价格信息

2. **Custom Loader 机制**
   - 运行时动态加载 SDK
   - 按需初始化
   - 支持第三方扩展

3. **Transform 适配层**
   - 消息格式标准化
   - 参数智能配置
   - 推理变体统一

4. **成本追踪**
   - 内置价格计算
   - Token 使用统计
   - 预算控制

---

## 八、具体示例：多 Provider 无缝切换

### 8.1 场景

用户想用不同的模型完成同一个任务。

### 8.2 代码实现

```typescript
// 用户无需关心 Provider 差异
const models = [
  { providerID: "anthropic", modelID: "claude-sonnet-4-20250514" },
  { providerID: "openai", modelID: "gpt-4o" },
  { providerID: "google", modelID: "gemini-2-0-flash" },
]

for (const m of models) {
  const model = await Provider.getModel(m.providerID, m.modelID)

  // OpenCode 自动处理差异：
  // - Anthropic: reasoningEffort 参数
  // - OpenAI: thinking.budgetTokens
  // - Google: thinkingConfig

  const language = await Provider.getLanguage(model)
  // 使用统一接口调用
}
```

---

## 九、关键代码位置索引

| 功能 | 文件 | 行号 |
|------|------|-----|
| Provider 定义 | provider/provider.ts | 604-617 |
| Model 定义 | provider/provider.ts | 533-602 |
| 内置 Provider | provider/provider.ts | 58-81 |
| Custom Loader | provider/provider.ts | 90-531 |
| 消息标准化 | provider/transform.ts | 44-131 |
| 温度配置 | provider/transform.ts | 280-304 |
| Top-P 配置 | provider/transform.ts | 306-313 |
| Top-K 配置 | provider/transform.ts | 315-323 |
| 推理变体 | provider/transform.ts | 328-450 |
| Schema 转换 | provider/transform.ts | 500-600 |

---

## 十、总结

OpenCode 的 Provider 系统相比其他框架有显著优势：

1. **统一抽象**: 一个接口管理 20+ Provider，开发者无需关心底层差异

2. **智能适配**: ProviderTransform 自动处理：
   - 消息格式差异
   - 参数命名差异
   - 推理配置差异

3. **零配置**: 从 ModelsDev 自动获取模型信息，无需手动维护

4. **完整建模**: 模型能力、价格、限制全部建模，支持静态验证

5. **动态加载**: 运行时按需加载 SDK，支持插件扩展

6. **成本追踪**: 内置 Token 计数和价格计算

这种设计让开发者可以专注于业务逻辑，而不是 API 细节，同时保证了对最新模型和 Provider 的快速支持。