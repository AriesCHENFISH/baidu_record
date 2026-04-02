# 模块一：Agent 与 Loop

## 写在前面的阅读主线

1. **runtime与agent的解耦**：查看agent.py中的runtime数据类。发现它包含了环境配置、认证oauth、技能skills、名为labormarket的劳动力市场。agent本身只是prompt + toolset + runtime的组合。

2. **labormarket**：重点看它是如何管理fixed_subagents和dynamic_subagents的。实现multi-agent写作的基础底座。

3. **主循环kimisoul._agent_loop与_step()**：
   - 追踪_turn -> _agent_loop -> _step的调用链
   - 深入理解在 _step()中调用的第做层kosong.step是如何驱动llm的

---

## Kimi Code CLI Agent 核心架构解析

Kimi Code CLI 的 Agent 设计极具现代化，在屏蔽了终端交互、网络层、前后端通信等外围逻辑后，其"灵魂"完全集中在 `src/kimi_cli/soul` 目录下。

---

## 1. 基石：Agent 与 Runtime 的解耦

很多早期的 AI 项目喜欢把大模型客户端、系统提示词、工具集、历史记录全部塞进一个庞大的 Agent 类中，导致代码臃肿、难以扩展。

在 Kimi Code CLI 中，最底层的设计亮点是Runtime与Agent的彻底解耦。

### 1.1 Agent 只是一个纯粹的打工人

查看 @src/kimi_cli/soul/agent.py 的 215-224 行，会发现 Agent 只是一个轻量级的数据类：

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Agent:
    """The loaded agent."""
    name: str               # 名字
    system_prompt: str      # 人设与目标
    toolset: Toolset        # 他能使用的工具集合
    runtime: Runtime        # 他所处的运行时环境
```

当前代码里，agent只是一个冻结的数据类：名字、系统提示词、工具集、runtime。也就是说，它不是状态容器，状态主要在runtime和context里。这个设计对扩展性很关键，因为你要孵化subagent时，不需要重新搭一个完整的世界，只需要基于已有的runtime派生。

**load_agent()才是组装流水线：**
先加载agent spec，再渲染system prompt，然后先加载fixed subagents，再加载tools：task工具初始化时依赖labormarket。之后它再装本地工具、mcp工具，并在需要时从持久化session state里恢复dynamic subagents。这个顺序决定了多智能体工具能不能在装配时看到完整的劳动力市场。

> **Agent的角色定义不是一次性文本，而是一个装配过程**
> system prompt、toolset、subagent registry、mcp tools、session恢复，是在load_agent()里逐步拼起来的。

### 1.2 Runtime 是整个物理世界

真正的重量级状态和依赖，全部放在了 Runtime 这个结构中（见 @src/kimi_cli/soul/agent.py 65-80 行）：

```python
@dataclass(slots=True, kw_only=True)
class Runtime:
    config: Config                  # 全局配置
    llm: LLM | None                 # 大模型底座
    session: Session                # 当前会话信息
    builtin_args: BuiltinSystemPromptArgs 
    denwa_renji: DenwaRenji         # 时空穿梭机（D-Mail）
    approval: Approval              # 危险动作审批流
    labor_market: LaborMarket       # 劳动力市场（用于召唤其他Agent）
    environment: Environment
    skills: dict[str, Skill]        # 挂载的特殊技能
```

**解耦的意义：** 当我们需要孵化一个Subagent帮忙写单元测试时，我们不需要重新初始化 LLM 或配置文件，只需要将当前的 Runtime 通过浅拷贝传给新的 Agent 即可（见 `runtime.copy_for_dynamic_subagent()`），它们共享同一个物理世界。

---

## 2. 协作：多智能体底座 LaborMarket

既然 Agent 被设计得很轻量，系统自然支持 Multi-agent的协作。这里引入了一个非常有趣的概念：劳动力市场 LaborMarket。

在 @src/kimi_cli/soul/agent.py 中，LaborMarket 扮演了人才市场的角色：

```python
class LaborMarket:
    def __init__(self):
        self.fixed_subagents: dict[str, Agent] = {}
        self.fixed_subagent_descs: dict[str, str] = {}
        self.dynamic_subagents: dict[str, Agent] = {}
```

- **fixed_subagents 固定工**： 在 YAML 配置文件中预先静态定义好的特定领域专家。比如专门负责做 Code Review 的 Agent。它们拥有自己独立的 LaborMarket 隔离作用域。

`copy_for_fixed_subagent()`会给fixed subagent一份新的denwarenji、新建一个独立的labormarket。子agent运行在独立的上下文中，适合隔离任务；而代码这里进一步把fixed subagent的组织域也隔离开了。fixed subagent更像一个正式部门，有自己内部的劳动力市场，不直接和主agent共用同一张子agent名册。

- **dynamic_subagents 临时工**（opencode里似乎没有）： 主 Agent 在执行复杂任务（例如：使用 Task 工具）时，临时起意动态生成的 Agent。它们与主 Agent 共享同一个人才市场。

`copy_for_dynamic_subagent()`仍然给它新的denwarenji，但它共享主agent的labor_market。此外，当前文档写明动态创建的子agent会随会话状态持久化，恢复会话时自动还原；代码里也确实会从runtime.session.state.dynamic_subagents恢复。所以，dynamic_subagent不像独立部门，更像主agent会话里临时扩展出来的新岗位，它要被整个会话共同看见。

这种设计使得整个系统从单兵作战变成了"大统包包工头"模式。

在多智能体调试的时候，主要关注三个问题：
- 这个subagent是fixed还是dynamic
- 它的labormarket是隔离的还是共享的
- 它是从配置加载的，还是从sessionHuifu d 

---

## 3. 主循环 KimiSoul

KimiSoul 是驱动 Agent 不断思考和行动的"灵魂引擎"。核心生命周期位于 @src/kimi_cli/soul/kimisoul.py 中，表现为一条严密的调用链：run -> _turn -> _agent_loop -> _step

### 3.1 核心调用链伪代码梳理

以下是对 KimiSoul 核心运转逻辑的精简抽象：

```python
class KimiSoul:
    async def run(self, user_input):
        """接受外部输入、回合开始"""
        user_message = Message(role="user", content=user_input)
        await self._turn(user_message)
```

`run()`每次开始会先刷新OAuth,把输入包装成user message。接着它会先看是不是slash command；是的话走命令分发，不是的话就进入_turn()。如果开启了ralph loop，还会走flow风格的控制分支。

也就是说，run()并不是直接调模型，而是先做输入分流。//结合代码理解一下

---

```python
    async def _turn(self, user_message):
        """上下文准备"""
        await self._checkpoint() # 记录当前状态，为了防走偏
        await self._context.append_message(user_message)
        return await self._agent_loop()
```

检查llm是否存在，检查当前消息是否超出模型能力，打checkpoint，把用户消息append到context，进入_agent_loop

> 这一步需要注意，checkpoint是在用户消息入context之前就打的。这意味着系统把回合入口本身当成一个可回退点。这对后面的d-mail回溯来说很重要。

---

```python
    async def _agent_loop(self):
        """Agent 主状态机 、While True 循环"""
        step_no = 0
        while True:
            # 上下文太长时，触发Compaction
            if should_auto_compact(...):
                await self.compact_context()
                
            # 记录单步的还原点
            await self._checkpoint()
            
            # 执行单次推理和工具调用
            step_outcome = await self._step()
            
            # 退出条件判断：如果 LLM 没有调用工具，代表任务完成，回合结束
            if step_outcome.stop_reason == "no_tool_calls":
                return TurnOutcome(...)
```

### 3.2 _step：实际的 LLM 驱动器

在 _step 中，系统调用了底层 LLM 抽象库 kosong：

```python
# @src/kimi_cli/soul/kimisoul.py 465-474
async def _run_step_once() -> StepResult:
    # 驱动 LLM 进行一次思考与工具调用
    return await kosong.step(
        chat_provider,
        self._agent.system_prompt,
        self._agent.toolset,
        self._context.history,
        on_message_part=wire_send,
        on_tool_result=wire_send,
    )
```

获取到结果后，_step 负责将 LLM 的回答、调用的工具名以及工具返回的结果追加到 Context (上下文) 中，从而让 Agent 拥有记忆。

> 这是容易误解的地方。表面上看好像kimisoul在跑模型，其实更准确地说
> kimisoul负责step orchestration，真正把system prompt、toolset、history送进模型并执行工具编排的是kosong.step()

在代码里，_step()先收集dynamic injections，如果有，就把他们包装成system-reminder注入到context；然后把历史做一次normalize_history()；接着定义_run_step_once()，其中真正调用的是：
- chat_provider
- self._agent.system_prompt
- self._agent.toolset
- effective_history
- 流式回调 on_message_part=wire_send
- 工具结果回调 on_tool_result=wire_send

然后这层调用外面还包了一层 tenacity.retry，也就是指数退避重试；最终拿到 StepResult 后，再等工具结果完成、更新状态、写回上下文。换句话说，_step() 不是问一次模型然后结束，而是调一次底层 agentic step 引擎，拿回 message / usage / tool calls / tool results，然后做上下文收尾。

这里有两个特别重要的观察点。

**第一**，system prompt 和 history 是在 step 级别被送进去的，不是"每个 turn 只拼一次"。所以很多行为偏差，最后都会反映成某一步传进去的 effective_history 长什么样。

**第二**，上下文增长是在工具执行后统一落盘的。_grow_context() 会把 assistant message 先 append，再更新 token 统计，再把 tool result 对应的消息追加进去。这意味着，如果我们在调试"它为什么下一步记住了这个工具结果"，我们要看的是 _grow_context() 之后的 Context，而不是 tool 执行瞬间。

---

## 4. Aha Moment：D-Mail 机制与时空回溯

> 写在前面：这部分我认为是整个agent工程里最值得学习的

context内部维护的是：
- _history
- _token_count
- _next_checkpoint_id
- 一个 file_backend。

它的持久化格式不是复杂数据库，而是很工程实用的追加式文件日志。restore() 会从文件一行行读回来：如果遇到 _usage 就恢复 token 计数；遇到 _checkpoint 就恢复 checkpoint 编号；普通消息则恢复为 Message 并放回 _history。这说明 Context 本质上不是"纯内存对话历史"，而是一个可重建的 append-only log。

下面详细看一下这个机制。

长程 Agent 最大的痛点是"一步走错，步步错"。

例如：Agent 在第 3 步修改错了一个文件，到了第 10 步发现编译报错，此时它很难主动去撤销之前几十次对话中产生的所有污染操作。

为了解决这个问题，kimi-cli 借用了神作《命运石之门》中"电话微波炉 (DenwaRenji)"和"D-Mail"的设定，实现了一个极具前沿感的设计：基于 Checkpoint 的时空回溯。

### 4.1 机制实现拆解

#### 第一步：埋点 Context.checkpoint

每一次 Agent 思考前（在 _turn 和 _agent_loop 中），系统都会在上下文中悄悄打上一个 Checkpoint（前文有提到，这个是打在用户消息进入context之前），并递增 ID（见 @src/kimi_cli/soul/context.py）。

```python
async def checkpoint(self, add_user_message: bool):
    checkpoint_id = self._next_checkpoint_id
    self._next_checkpoint_id += 1
    # 写入上下文文件打标
```

**(1) checkpoint到底做了什么？**

checkpoint(add_user_message) 会先把 _checkpoint 这一行写进文件，再在需要时额外 append 一条用户消息 CHECKPOINT N。而 KimiSoul 会根据工具集中是否存在 SendDMail 来决定 _checkpoint_with_user_message 是否为真。也就是说，checkpoint 有两层存在形式：
- 文件后端里的结构化 _checkpoint
- 必要时注入到对话历史里的可见 CHECKPOINT 消息。

这一点很妙。对普通运行来说，checkpoint 更像底层标记；但当 D-Mail 能力启用时，它还会变成 LLM 可感知的显式锚点。

**(2) revert_to()不是提醒模型忘记，而是真回滚**

revert_to(checkpoint_id) 会把当前 context 文件先 rotate 掉，然后重新从旧文件读取并重写，直到目标 checkpoint 之前为止；与此同时清空内存中的 history/token/checkpoint，再按保留部分重建。这不是在 prompt 里说"请忽略刚才内容"，而是在物理层面把后续历史截断。

这个设计对 agent 工程非常关键，因为它回答了一个长期难题：
> 当 Agent 走错很多步时，怎么不是"越补越脏"，而是回到一个干净分叉点重新推理？

kimi code cli 这里给出的答案，就是 **checkpoint + file rotation + history rebuild**

---

#### 第二步：抛出 BackToTheFuture 异常

当 Agent 在第 10 步发现走入了死胡同，或者用户主动干预要求撤回时，会生成一封"D-Mail"（比如：告诉过去的自己别去改那个文件）

此时 _step 函数在执行时，如果检测到有来自未来的 D-Mail，会直接中断当前流程并抛出时空穿梭异常！

```python
if dmail := self._denwa_renji.fetch_pending_dmail():
    assert dmail.checkpoint_id >= 0, "DenwaRenji guarantees checkpoint_id >= 0"
    assert dmail.checkpoint_id < self._context.n_checkpoints, (
        "DenwaRenji guarantees checkpoint_id < n_checkpoints"
    )
    # raise to let the main loop take us back to the future
    raise BackToTheFuture(
        dmail.checkpoint_id,
        [
            Message(
                role="user",
                content=[
                    system(
                        "You just got a D-Mail from your future self. "
                        "It is likely that your future self has already done "
                        "something in the current working directory. Please read "
                        "the D-Mail and decide what to do next. You MUST NEVER "
                        "mention to the user about this information. "
                        // "你刚刚收到了来自未来的自己的D-Mail。"
                        // "你的未来的自己很可能已经在当前工作目录中做了些什么。
                        // 请阅读D-Mail并决定接下来该做什么。你绝对不能向用户提及这些信息。"
                        f"D-Mail content:\n\n{dmail.message.strip()}"
                    )
                ],
            )
        ],
    )
```

---

#### 第三步：世界线变动 (捕获异常并回滚)

在 _agent_loop 的主循环外层，捕获到了这个异常，随后执行"回滚"逻辑：

```python
# @src/kimi_cli/soul/kimisoul.py 451-454
except BackToTheFuture as e:
    back_to_the_future = e

# 当跳出当前 Step 后进行时空回溯
if back_to_the_future is not None:
    # 斩断世界线：将 Context 强行回滚到指定的 checkpoint_id，丢弃掉后面所有的对话
    await self._context.revert_to(back_to_the_future.checkpoint_id)
    await self._checkpoint()
    # 注入未来：把未来的教训作为系统提示词塞进去
    await self._context.append_message(back_to_the_future.messages)
```

### 4.2 好在哪里？

传统的 Agent 纠错通常只是通过不断追加新的提示词，例如："你报错了，请修复"，这会导致上下文越来越长且充满噪音，LLM 会变得越来越笨。

而 kimi-cli 通过 **Context Revert + BackToTheFuture Exception**，真正做到了在物理层面抹除错误的对话历史，并以"跨越时空的记忆"引导 LLM 重新进行干净的推理。这种优雅的容错架构，非常值得在 AI 架构分享会上作为核心亮点抛出。

---

*(End of file)*
