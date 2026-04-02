# 模块二：Tool 与 MCP

在长程 Agent 任务中，如果说主循环是引擎，那么"工具"就是 Agent 改造物理世界的手脚。为了让 Agent 能够安全、高效、可扩展地使用工具，Kimi Code CLI 采用了一套极为优雅的架构，将依赖注入、外部标准化协议（MCP）以及安全审批沙盒深度结合。

本文档将按照"地基 → 脚手架 → 贴瓷砖"的逻辑，带你拆解 Agent 工具调用与安全审批的核心流程。

---

## 1. 地基：工具的依赖注入 (Dependency Injection)

传统的 Agent 工具在定义时，往往需要硬编码全局配置或环境信息，导致工具难以独立测试和复用。Kimi Code CLI 在工具加载层面引入了依赖注入机制，这是整个工具体系能够灵活挂载的"地基"。

### 1.1 KimiToolset 与 反射解析

在工具集管理器 @src/kimi_cli/soul/toolset.py 中，KimiToolset 负责加载和维护所有可用的工具。最精彩的部分在于其内部的 `_load_tool` 方法，它通过 Python 的反射机制（inspect）自动完成了依赖的注入：

```python
# @src/kimi_cli/soul/toolset.py 192-214
@staticmethod
def _load_tool(tool_path: str, dependencies: dict[type[Any], Any]) -> ToolType | None:
    # 1. 动态导入工具类
    module_name, class_name = tool_path.rsplit(":", 1)
    module = importlib.import_module(module_name)
    tool_cls = getattr(module, class_name, None)
    
    args: list[Any] = []
    # 2. 如果工具类重写了 __init__ 方法，则通过反射解析其参数
    if "__init__" in tool_cls.__dict__:
        for param in inspect.signature(tool_cls).parameters.values():
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                # 遇到 keyword-only 参数即停止依赖注入
                break
            # 3. 关键：根据类型注解 (Annotation) 自动去依赖池中寻找实例并注入
            if param.annotation not in dependencies:
                raise ValueError(f"Tool dependency not found: {param.annotation}")
            args.append(dependencies[param.annotation])
            
    # 4. 实例化工具
    return tool_cls(*args)
```

**为什么这很关键？**

当开发者编写一个新的原生工具时，无需关心如何获取 Runtime 或 Config。只需在 `__init__` 中声明类型（如 `def __init__(self, runtime: Runtime):`），系统加载该工具时，就会自动把环境中的 Runtime 实例塞进去。这种设计让工具代码极度纯粹。

---

## 2. 脚手架：MCP (Model Context Protocol) 的无缝接入

随着 AI 的发展，出现了 MCP（Model Context Protocol）标准，允许 Agent 调用分布在本地或远程服务器上的标准化工具。Kimi Code CLI 是如何接入这一庞大生态的呢？

### 2.1 外部工具向原生接口的对齐

Kimi Code CLI 并没有为 MCP 工具单独写一套调用逻辑，而是把 MCP 工具包装成了原生工具的形状。

在 @src/kimi_cli/soul/toolset.py 中，提供了 MCPTool 包装类：

```python
# @src/kimi_cli/soul/toolset.py 373-397
class MCPTool[T: ClientTransport](CallableTool):
    def __init__(
        self,
        server_name: str,
        mcp_tool: mcp.Tool,
        client: fastmcp.Client[T],
        *,
        runtime: Runtime,
        **kwargs: Any,
    ):
        # 1. 继承原生的 CallableTool 接口
        super().__init__(
            name=mcp_tool.name,
            description=...,
            parameters=mcp_tool.inputSchema,
            **kwargs,
        )
        self._mcp_tool = mcp_tool
        self._client = client
        self._runtime = runtime
        self._action_name = f"mcp:{mcp_tool.name}"
```

所有的 MCP 工具在后台被 `load_mcp_tools` 函数异步加载，然后转化为 MCPTool 实例并 add 到 KimiToolset 中。对底层大模型而言，调用本地原生 Shell 工具与调用远端 MCP 数据库查询工具，在体验上毫无差别。

---

## 3. 贴瓷砖：安全审批沙盒 (Approval Sandbox)

当 Agent 开始自由调用工具后，最致命的问题就是安全（比如误删库、泄露密钥）。为了防止 Agent "失控暴走"，Kimi Code CLI 建立了一个强大的异步审批流拦截器。

### 3.1 强制拦截与沙盒机制

当 LLM 决定调用某个工具时，实际上触发的是工具类的 `__call__` 方法。我们来看 MCPTool 是如何在这最后一道防线卡住执行的：

```python
# @src/kimi_cli/soul/toolset.py 398-402
async def __call__(self, *args: Any, **kwargs: Any) -> ToolReturnValue:
    description = f"Call MCP tool `{self._mcp_tool.name}`."
    
    # 【高能预警】工具执行前，强制向沙盒请求审批
    if not await self._runtime.approval.request(self.name, self._action_name, description):
        return ToolRejectedError() # 如果被拒绝，直接返回给大模型拦截信息
        
    # 如果通过审批，则向真正的 MCP Server 发起调用...
    try:
        async with self._client as client:
            ...
```

### 3.2 Approval 的运行原理

进入 @src/kimi_cli/soul/approval.py，你会看到这个审批沙盒的精妙运作：

#### (1) YOLO 模式与白名单

在 Approval.request 内部，首先会检查是否开启了 YOLO 模式（You Only Live Once，意味着全局放行），或者该动作是否被用户之前选择过"本次会话总是允许"（auto_approve_actions）。如果是，立刻放行。

```python
# @src/kimi_cli/soul/approval.py 96-100
if self._state.yolo:
    return True
if action in self._state.auto_approve_actions:
    return True
```

#### (2) 异步队列阻塞

如果需要审批，它会创建一个包含随机 ID 的 Request 对象，把它塞入一个队列 `_request_queue` 中，并利用 `asyncio.Future` 把当前的协程挂起（阻塞），等待未来的信号：

```python
# @src/kimi_cli/soul/approval.py 101-112
request = Request(...)
approved_future = asyncio.Future[bool]()
self._request_queue.put_nowait(request)
self._requests[request.id] = (request, approved_future)

return await approved_future # 挂起！等待用户在UI点击"Approve"或"Reject"
```

#### (3) 跨越线程的 Wire 传递

那这个阻塞什么时候会被唤醒？

在上一阶段讲到的核心主循环 _agent_loop 中，后台一直跑着一个专门管审批的守护任务（_pipe_approval_to_wire）。它会从 `_request_queue` 中取走刚才的 Request，通过 wire_send 扔给前端 UI。前端显示弹窗让用户点击，用户点完后，UI 将结果传回给这个守护任务，最后通过 `self._approval.resolve_request` 设置 `future.set_result(True/False)`，从而瞬间唤醒那个被挂起的工具调用逻辑！

### 3.3 为什么这是一个"值得参考的亮点"？

许多 Agent 项目在处理权限时，要不就是硬编码在每一个工具的逻辑里，要不就是在主循环外部强行中断。Kimi Code CLI 这种做法的亮点在于：

1. **零侵入感**：工具的执行逻辑（如 `__call__`）和权限审批流（Approval.request）是完全异步解耦的，写业务工具的人只需调一句 request，不需要处理多线程或 UI 交互逻辑。

2. **底层阻塞引擎**：利用 asyncio.Future 实现"发请求 -> 原地挂起 -> UI操作完 -> 继续执行"，将复杂的异步 IO 变成了线性的、极其易读的代码。这是一种在 Python Agent 开发中处理"Human-in-the-loop (HITL)"极其高级的范式。

---

*(End of file)*
