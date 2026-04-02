# OpenCode LSP 集成系统

OpenCode 的 LSP（Language Server Protocol，语言服务器协议）集成系统是其智能编程辅助能力的核心组件。与传统 IDE 的 LSP 实现不同，OpenCode 将 LSP 能力直接暴露给 AI Agent，使 AI 能够动态调用语言服务器的强大功能，如代码跳转、引用查找、悬停提示等。这种深度集成是 OpenCode 区别于其他 AI 编程工具的关键创新点。

---

## 一、核心设计理念

### 1.1 传统方式 vs OpenCode 方式

**传统 AI 编程工具（如 GitHub Copilot）:**

- LSP 主要用于代码补全
- 用户在编辑器中被动接收提示
- AI 无法主动调用 LSP 功能

**OpenCode 的创新:**

- LSP 功能直接暴露给 AI Agent
- AI 可以主动调用 goToDefinition、findReferences 等
- 支持文件编辑后自动获取诊断信息
- 30+ 种语言服务器统一管理

---

## 二、核心代码文件

### 2.1 LSP 核心模块

| 文件路径 | 作用 |
|---------|------|
| `packages/opencode/src/lsp/index.ts` | LSP 主命名空间，包含客户端管理、LSP 功能 API |
| `packages/opencode/src/lsp/client.ts` | LSP 客户端实现，使用 vscode-jsonrpc 通信 |
| `packages/opencode/src/lsp/server.ts` | 30+ 种语言服务器定义和启动配置 |
| `packages/opencode/src/lsp/language.ts` | 文件扩展名到语言 ID 的映射 |

### 2.2 LSP 工具集成

| 文件路径 | 作用 |
|---------|------|
| `packages/opencode/src/tool/lsp.ts` | LSP 工具定义，暴露给 AI Agent |
| `packages/opencode/src/tool/lsp.txt` | LSP 工具描述 |

### 2.3 与其他系统集成

| 文件路径 | 作用 |
|---------|------|
| `packages/opencode/src/tool/read.ts` | 读取文件时预热 LSP |
| `packages/opencode/src/tool/write.ts` | 写入文件后获取诊断 |
| `packages/opencode/src/tool/edit.ts` | 编辑后获取诊断 |
| `packages/opencode/src/project/bootstrap.ts` | 项目启动时初始化 LSP |

---

## 三、LSP 客户端管理

### 3.1 状态管理

```typescript
// packages/opencode/src/lsp/index.ts:79-144

const state = Instance.state(async () => {
  const clients: LSPClient.Info[] = []
  const servers: Record<string, LSPServer.Info> = {}

  // 从配置或默认加载服务器
  for (const server of Object.values(LSPServer)) {
    servers[server.id] = server
  }

  // 应用用户配置
  for (const [name, item] of Object.entries(cfg.lsp ?? {})) {
    if (item.disabled) {
      delete servers[name]
      continue
    }
    servers[name] = {
      ...existing,
      id: name,
      spawn: async (root) => {
        return {
          process: spawn(item.command[0], item.command.slice(1), { cwd: root, env: { ...process.env, ...item.env } }),
          initialization: item.initialization,
        }
      },
    }
  }

  return {
    broken: new Set<string>(), // 损坏的服务器
    servers, // 可用服务器
    clients: [], // 已连接的客户端
    spawning: new Map(), // 正在启动的服务器
  }
})
```

### 3.2 客户端获取与启动

```typescript
// packages/opencode/src/lsp/index.ts:177-262

async function getClients(file: string) {
  const s = await state()
  const extension = path.parse(file).ext

  // 1. 查找匹配的文件扩展名的服务器
  for (const server of Object.values(s.servers)) {
    if (server.extensions.length && !server.extensions.includes(extension)) continue

    // 2. 查找项目根目录
    const root = await server.root(file)
    if (!root) continue
    if (s.broken.has(root + server.id)) continue

    // 3. 复用已有客户端
    const match = s.clients.find((x) => x.root === root && x.serverID === server.id)
    if (match) {
      result.push(match)
      continue
    }

    // 4. 启动新客户端
    const task = schedule(server, root, root + server.id)
    s.spawning.set(root + server.id, task)
    const client = await task
    if (!client) continue

    s.clients.push(client)
    result.push(client)
  }

  return result
}
```

---

## 四、LSP 功能 API

OpenCode 暴露了完整的 LSP 功能给 AI Agent：

### 4.1 核心功能实现

```typescript
// packages/opencode/src/lsp/index.ts:303-418

// 1. 悬停提示
export async function hover(input: { file: string; line: number; character: number }) {
  return run(input.file, (client) => {
    return client.connection.sendRequest("textDocument/hover", {
      textDocument: { uri: pathToFileURL(input.file).href },
      position: { line: input.line, character: input.character },
    })
  })
}

// 2. 跳转定义
export async function definition(input: { file: string; line: number; character: number }) {
  return run(input.file, (client) => {
    return client.connection.sendRequest("textDocument/definition", {
      textDocument: { uri: pathToFileURL(input.file).href },
      position: { line: input.line, character: input.character },
    })
  })
}

// 3. 查找引用
export async function references(input: { file: string; line: number; character: number }) {
  return run(input.file, (client) => {
    return client.connection.sendRequest("textDocument/references", {
      textDocument: { uri: pathToFileURL(input.file).href },
      position: { line: input.line, character: input.character },
      context: { includeDeclaration: true },
    })
  })
}

// 4. 查找实现
export async function implementation(input: { file: string; line: number; character: number }) {
  return run(input.file, (client) => {
    return client.connection.sendRequest("textDocument/implementation", {
      textDocument: { uri: pathToFileURL(input.file).href },
      position: { line: input.line, character: input.character },
    })
  })
}

// 5. 工作区符号搜索
export async function workspaceSymbol(query: string) {
  return runAll((client) =>
    client.connection
      .sendRequest("workspace/symbol", { query })
      .then((result) => result.filter((x) => kinds.includes(x.kind)))
      .then((result) => result.slice(0, 10)),
  )
}

// 6. 文档符号
export async function documentSymbol(uri: string) {
  return run(uri, (client) =>
    client.connection.sendRequest("textDocument/documentSymbol", {
      textDocument: { uri },
    }),
  )
}
```

### 4.2 诊断信息收集

```typescript
// packages/opencode/src/lsp/index.ts:291-301

export async function diagnostics() {
  const results: Record<string, LSPClient.Diagnostic[]> = {}

  // 并行收集所有客户端的诊断
  for (const result of await runAll(async (client) => client.diagnostics)) {
    for (const [path, diagnostics] of result.entries()) {
      const arr = results[path] || []
      arr.push(...diagnostics)
      results[path] = arr
    }
  }

  return results
}
```

---

## 五、LSP 工具（Agent 专用）

### 5.1 工具定义

```typescript
// packages/opencode/src/tool/lsp.ts:22-96

export const LspTool = Tool.define("lsp", {
  description: DESCRIPTION,
  parameters: z.object({
    operation: z
      .enum([
        "goToDefinition", // 跳转定义
        "findReferences", // 查找引用
        "hover", // 悬停提示
        "documentSymbol", // 文档符号
        "workspaceSymbol", // 工作区符号
        "goToImplementation", // 跳转实现
        "prepareCallHierarchy", // 准备调用层次
        "incomingCalls", // 传入调用
        "outgoingCalls", // 传出调用
      ])
      .describe("The LSP operation to perform"),
    filePath: z.string(),
    line: z.number().int().min(1),
    character: z.number().int().min(1),
  }),

  execute: async (args, ctx) => {
    // 1. 权限检查
    await ctx.ask({
      permission: "lsp",
      patterns: ["*"],
      always: ["*"],
    })

    // 2. 文件存在性检查
    const exists = await Bun.file(file).exists()
    if (!exists) {
      throw new Error(`File not found: ${file}`)
    }

    // 3. LSP 可用性检查
    const available = await LSP.hasClients(file)
    if (!available) {
      throw new Error("No LSP server available for this file type.")
    }

    // 4. 预热 LSP
    await LSP.touchFile(file, true)

    // 5. 执行 LSP 操作
    const result = await (async () => {
      switch (args.operation) {
        case "goToDefinition":
          return LSP.definition(position)
        case "findReferences":
          return LSP.references(position)
        case "hover":
          return LSP.hover(position)
        case "documentSymbol":
          return LSP.documentSymbol(uri)
        case "workspaceSymbol":
          return LSP.workspaceSymbol("")
        // ...
      }
    })()

    return {
      title: `${args.operation} ${relPath}:${args.line}:${args.character}`,
      metadata: { result },
      output: JSON.stringify(result, null, 2),
    }
  },
})
```

---

## 六、30+ 语言服务器支持

### 6.1 支持的语言服务器

OpenCode 内置支持 30+ 种语言服务器：

| 语言 | 服务器 ID | 配置文件 |
|------|----------|---------|
| TypeScript/JavaScript | deno | Deno |
| TypeScript/JavaScript | typescript | tsserver |
| Vue | vue | vue-language-server |
| Python | pyright | pyright-langserver |
| Python (实验) | ty | _ty |
| Go | gopls | gopls |
| Rust | rust-analyzer | rust-analyzer |
| Ruby | ruby-lsp | ruby-lsp |
| C/C++ | clangd | clangd |
| Java | jdtls | jdtls |
| C# | csharp-ls | csharp-ls |
| PHP | php | intelephense |
| Dart | dart | dart |
| Lua | lua-ls | lua-language-server |
| Elixir | elixir | elixir-ls |
| Swift | swift | sourcekit-lsp |
| Zig | zls | zls |
| OCaml | ocaml | ocaml-lsp |
| YAML | yaml | yaml-language-server |
| Svelte | svelte | svelte-language-server |
| Astro | astro | astro-language-server |
| Prisma | prisma | prisma-language-server |
| ESLint | eslint | eslint |
| Biome | biome | @biomejs/biome |
| Terraform | terraform | terraform-ls |

### 6.2 服务器定义示例

```typescript
// packages/opencode/src/lsp/server.ts:61-116

// Deno
export const Deno: Info = {
  id: "deno",
  root: async (file) => {
    const files = Filesystem.up({
      targets: ["deno.json", "deno.jsonc"],
      start: path.dirname(file),
      stop: Instance.directory,
    })
    const first = await files.next()
    if (!first.value) return undefined
    return path.dirname(first.value)
  },
  extensions: [".ts", ".tsx", ".js", ".jsx", ".mjs"],
  async spawn(root) {
    const deno = Bun.which("deno")
    if (!deno) return
    return {
      process: spawn(deno, ["lsp"], { cwd: root }),
    }
  },
}

// TypeScript
export const Typescript: Info = {
  id: "typescript",
  root: NearestRoot(
    ["package-lock.json", "bun.lockb", "bun.lock", "pnpm-lock.yaml", "yarn.lock"],
    ["deno.json", "deno.jsonc"], // 排除 Deno 项目
  ),
  extensions: [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"],
  async spawn(root) {
    const tsserver = await Bun.resolve("typescript/lib/tsserver.js", Instance.directory)
    const proc = spawn(BunProc.which(), ["x", "typescript-language-server", "--stdio"], {
      cwd: root,
    })
    return {
      process: proc,
      initialization: { tsserver: { path: tsserver } },
    }
  },
}
```

---

## 七、与工具的深度集成

### 7.1 读取文件时预热 LSP

```typescript
// packages/opencode/src/tool/read.ts:136-138

// 读取文件后，预热 LSP 客户端
LSP.touchFile(filepath, false)
FileTime.read(ctx.sessionID, filepath)
```

### 7.2 写入文件后获取诊断

```typescript
// packages/opencode/src/tool/write.ts

// 写入文件后，自动获取诊断信息
const diagnostics = await LSP.diagnostics()
if (diagnostics[filepath]?.length) {
  // 将诊断信息添加到输出
}
```

### 7.3 编辑后获取诊断

```typescript
// packages/opencode/src/tool/edit.ts

// 编辑后，通知 LSP 文件已变更
await LSP.touchFile(filepath, true)

// 获取最新诊断
const diagnostics = await LSP.diagnostics()
```

---

## 八、与传统方案的对比优势

### 8.1 与 Copilot 对比

| 特性 | GitHub Copilot | OpenCode |
|------|---------------|---------|
| 代码补全 | ✓ | ✓ |
| LSP 功能暴露 | 部分 | 完整 |
| AI 主动调用 | ✗ | ✓ |
| 诊断信息 | ✗ | ✓ |
| 多服务器并发 | ✗ | ✓ |
| 语言服务器数量 | ~5 | 30+ |

### 8.2 与 LangChain/LlamaIndex 对比

| 特性 | LangChain/LlamaIndex | OpenCode |
|------|---------------------|---------|
| LSP 集成 | 无 | 完整 |
| 代码分析能力 | 基础 | 深度 |
| 工具调用 | 无 | 9 种 LSP 操作 |
| 诊断收集 | 无 | 自动 |

### 8.3 独特创新

1. **LSP 工具化**: 将 LSP 功能封装为 Agent 可调用的工具
2. **自动诊断收集**: 文件编辑后自动获取并展示 LSP 错误
3. **智能服务器选择**: 根据文件扩展名和项目根目录自动选择合适的 LSP 服务器
4. **多服务器并发**: 支持同时运行多个 LSP 服务器
5. **零配置**: 自动检测并启动合适的语言服务器

---

## 九、具体使用示例

### 9.1 场景：AI 分析代码结构

用户要求 AI 分析项目中的某个函数实现。

**传统方式:**

- AI 只能通过搜索文本或正则匹配来理解代码
- 无法准确知道函数的定义位置
- 无法列出函数的调用方

**OpenCode 方式:**

用户: "分析这个项目中的用户认证是怎么实现的?"

AI Agent:

1. 调用 LSP 工具 - `workspaceSymbol("auth")`
   → 返回项目中所有包含 "auth" 的符号

2. 调用 LSP 工具 - `goToDefinition("login 函数")`
   → 跳转到函数定义位置

3. 调用 LSP 工具 - `findReferences("login 函数")`
   → 列出所有调用 login 的位置

4. 调用 LSP 工具 - `incomingCalls("auth 中间件")`
   → 列出所有调用该中间件的地方

### 9.2 场景：代码错误自动检测

用户修改了代码，AI 自动检测错误。

用户: "帮我把这个函数改成异步的"

AI Agent:

1. 使用 Edit Tool 修改代码
2. LSP.touchFile() 通知语言服务器
3. LSP.diagnostics() 获取诊断
4. 返回: "检测到以下错误:
   - line 42: 类型不匹配
   - line 45: 缺少 await"

---

## 十、关键代码位置索引

| 功能 | 文件 | 行号 |
|------|------|-----|
| LSP 命名空间 | lsp/index.ts | 14-485 |
| 客户端管理 | lsp/index.ts | 177-262 |
| 悬停提示 | lsp/index.ts | 303-317 |
| 跳转定义 | lsp/index.ts | 386-395 |
| 查找引用 | lsp/index.ts | 397-407 |
| 工作区符号 | lsp/index.ts | 359-369 |
| 诊断收集 | lsp/index.ts | 291-301 |
| LSP 工具定义 | tool/lsp.ts | 22-96 |
| 语言服务器定义 | lsp/server.ts | 1-2046 |

---

## 十一、总结

OpenCode 的 LSP 集成系统是区别于其他 AI 编程工具的核心创新：

1. **深度集成**: LSP 功能直接暴露给 AI Agent，而不仅仅用于代码补全

2. **完整功能**: 支持 9 种 LSP 操作（跳转、引用、符号、诊断等）

3. **30+ 语言支持**: 内置支持 30+ 种语言服务器，零配置自动启动

4. **自动诊断**: 文件编辑后自动获取并展示 LSP 错误

5. **智能选择**: 根据文件类型和项目结构自动选择合适的语言服务器

6. **工具化设计**: 将复杂的 LSP 协议封装为简单的工具调用

这种设计使 AI Agent 具备了真正的代码理解能力，而不仅仅是文本匹配，从而能够提供更准确、更智能的编程辅助。