<!-- local reference notes only; not part of the upstream repo's published docs -->
# CogAlpha vs QuantaAlpha 深入对比（源码级，2026-07-10 核对）

来源：直接对照两个本地仓库的源码与配置文件 —— `/home/sunanying/CogAlpha`（本仓库）与
`/home/sunanying/QuantaAlpha`（本机另一个独立项目，前身叫 AlphaAgent）。QuantaAlpha 自己也有一份
详尽的 `CLAUDE.md`（约 38K，2026-07-08 更新），本文档的大部分事实基于那份文档 + 对关键源码文件
（`configs/backtest.yaml`、`quantaalpha/factors/coder/factor.py`、`quantaalpha/factors/coder/function_lib.py`、
`quantaalpha/backtest/runner.py` 等）和 git 历史的直接核对，不是逐字转录。

**这个文件只记录"两个项目在源码层面有什么不同"，不代表两者可以互相替代或合并比较** ——
`cogalpha/benchmark/presets.py` 里两套 preset 的"永不混用"不变量见 `/home/sunanying/CogAlpha/CLAUDE.md`
"Relationship to QuantaAlpha" 一节，这里只做架构/机制层面的对照。QuantaAlpha 是独立演进的项目，这份
对比会随时间过期——如果发现跟两边代码对不上了，以代码为准。

## 1. 定位本质不同，不是同一套东西的两种实现

- **CogAlpha**：论文复现项目，**固定代数**的多智能体循环，21 个预定义领域 agent 按 7 层层级划分，
  每代都全量跑一遍。目标是验证论文这套设计能不能在真实市场数据上复现出声称的效果。
- **QuantaAlpha**（前身 AlphaAgent，构建在微软 RD-Agent + Qlib 之上）：**演化式**开放搜索，
  `ORIGINAL → MUTATION → CROSSOVER → MUTATION → ...` 轨迹式推进，mutation/crossover 是在
  **假设文本层面**做正交扩展/多父本融合，不直接产出因子，而是生成下一轮 prompt 的引导后缀。目标是
  持续挖掘、越滚越大的因子库，不是复现某篇论文的固定实验。

一个是"验证一个固定假设"的实验装置，一个是"永动的挖掘生产线"。

## 2. 因子表示与执行沙箱：安全模型差一个数量级

| | CogAlpha | QuantaAlpha |
|---|---|---|
| 因子表示 | 原始 Python 函数体，LLM 直接写代码 | **自定义 DSL 表达式字符串**（如 `RANK(TS_MEAN($close,20)-$open)`），Jinja2 模板渲染成代码再执行 |
| 执行隔离 | `multiprocessing.get_context("spawn")` 独立子进程 + `proc.kill()`（SIGKILL）超时强杀，共享内存传面板数据，三个执行点（fitness/quality-5/leakage sentinel）**强制**走同一个池，无池则 fail-closed | `subprocess.check_output(..., shell=True, ...)` 直接 shell 出去跑一个 python 文件（`quantaalpha/factors/coder/factor.py:178`），**无 Docker、无沙箱**，只有 timeout |
| 命名空间限制 | 显式白名单导入（`math/numpy/pandas/scipy/scipy.stats/talib`）+ 极简安全 builtins（无 `eval/exec/open/__import__`） | 没有独立白名单机制——因子是 DSL 表达式而非任意 Python，攻击面天然被 DSL 语法收窄（~50 个 `function_lib.py` 里的函数）；但仓库里还留了 `FactorMultiProcessEvolvingStrategy`（legacy 模式，"LLM writes raw Python directly"）这条路径，完全没有对应的执行隔离 |

`shell=True` 本身是个值得注意的模式（这里插值的是本地路径而非 LLM 生成内容，实际注入风险不高，但比
CogAlpha "三个执行点必须走同一个受控池，否则 fail-closed" 的设计原则明显更松）。

## 3. 时序泄露（look-ahead）防护：CogAlpha 有专门一层，QuantaAlpha 基本没有

全仓库 grep "leakage"/"look-ahead" 在 QuantaAlpha 里只有两处：
- `quantaalpha/factors/coder/function_lib.py:189`：`DELAY` 函数里一行 `assert p >= 0`，防止负数周期。
- `quantaalpha/backtest/runner.py:522`：一句注释，无实际检查逻辑。

**没有 AST 静态扫描，没有执行时 sentinel 测试**。而 CogAlpha 的 `cogalpha/stages/leakage.py` 是两层
独立机制：AST 扫描禁止模式（反向 `shift(-k)`、居中滚动窗口、绝对 `.loc` 索引等）+ 构造合成面板、
扰动 cut 日期之后的数据、校验因子在 cut 前的输出不变——这是能抓住"反转→diff→反转"这类能绕过静态
扫描的构造的**执行级**测试，是 CogAlpha 六步质检里**唯一一个"硬拒绝、从不降级为 warning"**的步骤。

QuantaAlpha 之所以敢只留一个 assert，某种程度上是因为 DSL 表达式的函数词汇表本身就比"任意 Python"
收窄了泄露的构造空间——但这不是等价的防护，`FactorMultiProcessEvolvingStrategy` 那条 raw-Python
路径完全没有对应保障。

## 4. 因子准入机制：两种完全不同的哲学

- **CogAlpha**：同代**相对分位数**（65%/80%）**取 max**（绝对 minima），**五个指标同时达标**
  （ic/rank_ic/icir/rank_icir/mi）才能进 qualified/elite。是一次性、按代际隔离的门槛。
- **QuantaAlpha**：`quantaalpha/factors/pool/factor_pool.py::FactorPoolManager` 是**跨运行持久化**
  的全局池，贪心算法——按单因子 RankIC 降序排序，只要跟已入池因子的相关性不超阈值就收。**没有绝对
  下限**，纯粹相对排序 + 去重。而且这个机制目前有两个仓库自己都承认的真实未修复问题（QuantaAlpha
  CLAUDE.md"还没做完"第1、2条，2026-07-08 记录）：
  - "组篮子" `select_basket` 根本没实现——池子只进不出，冗余问题会在下游用池子时重新出现；
  - 子代因子跟父代天然高相关，池子没有对应豁免/去重机制，且准入是"先到先得"而非"优胜劣汰"（时间
    顺序决定谁留下，不是质量）。

CogAlpha 的准入完全是同代内评估，没有"先来后到"这个问题，因为它每代都重新走一遍完整的 fitness
gate，不存在跨代持久化的准入顺序依赖。

## 5. 回测配置：发现一处真实的"快照过期"

CogAlpha 的 `cogalpha/benchmark/presets.py::QUANTAALPHA_CSI300_OHLCV_V1` 是 **2026-06-06** 抓取的
QuantaAlpha `configs/backtest.yaml` 快照（`observed_on=date(2026, 6, 6)`），写死 `topk=50, n_drop=5`。
直接对比 QuantaAlpha 仓库当前的 `configs/backtest.yaml` 和它的 git 历史：

```
git log -p -- configs/backtest.yaml
...
-      topk: 50
-      n_drop: 5
+      topk: 10
+      n_drop: 1
```

这个改动在提交 `671fe4d`（**2026-07-08 21:25:11 +0800**，"Add global factor pool, multi-market fixes,
auto-mining-loop, and CLAUDE.md maintenance"），**晚于 CogAlpha 快照整整一个月**。也就是说：**CogAlpha
代码里嵌的 QuantaAlpha 对比基准现在是过期的**——如果想用 `quantaalpha_csi300_ohlcv_v1` preset 去逼近
QuantaAlpha 现在的真实回测行为，topk/n_drop 会跟真实配置对不上（50/5 vs 实际的 10/1）。

已核对、**目前仍一致、没有漂移**的部分：数据窗口 `2016-01-01~2025-12-26`、label 表达式
`Ref($close, -2) / Ref($close, -1) - 1`、cost model（`open_cost=0.0005/close_cost=0.0015/min_cost=5`）。

标签定义两边从来不是一回事，且从未打算可比：CogAlpha 是 10 日**开盘**远期收益
（`entry_delay_days=1`），QuantaAlpha 是 T+2 **收盘**收益，horizon=1。这个不可比性 CogAlpha 代码注释
里已经明确写了"两套设置永远不混用"——本节只是指出**快照数值本身**（topk/n_drop）已经过期，不是说
两套 benchmark 应该被合并比较。

**待办**：如果要重新对齐，需要在 `cogalpha/benchmark/presets.py::QUANTAALPHA_CSI300_OHLCV_V1` 里把
`portfolio_rule.topk`/`portfolio_rule.n_drop` 从 `_config(50)`/`_config(5)` 更新成 `_config(10)`/
`_config(1)`，并把 `sources[0].observed_on` 更新为重新核对的日期。截至本文档记录时（2026-07-10）
尚未执行这个更新。

## 6. 工程成熟度：QuantaAlpha 有更多"活的"已知 bug

QuantaAlpha 自己 CLAUDE.md 记录了好几个**未修复、会实际咬人**的 bug：
- 并行挖掘 worker 共享同一个因子库文件，**没有锁、没有原子写**（`quantaalpha/factors/library.py`），
  `run_evolution_loop` 甚至显式关掉了 file lock"避免死锁"，last-writer-wins 会静默丢因子；
- `quantaalpha/llm/client.py` 的 JSON 提取逻辑，遇到回复里一个 `{`/`}` 都没有时会静默把整个正响应
  文本变成空字符串（已经在换 Claude 系代理模型时真实触发过，2026-07-08 记录）；
- 缓存层①（按代码文本 md5 做 key，`FactorFBWorkspace.execute`）**不感知底层数据变化**，重新生成
  `daily_pv.h5` 不会使旧缓存失效；
- `function_lib.py` 里 `MAX`/`MIN` 被定义了两次，后一个静默覆盖前一个。

CogAlpha 目前记录的"已知问题"（见 `/home/sunanying/CogAlpha/CLAUDE.md` "论文复现现状" 一节）性质不
同——更多是"论文的某个机制还没实现"（多市场 preset、plateau 早停、消融开关）或"还没跑出论文声称
的效果"，而不是运行时会咬人的并发/缓存 bug。这个差异部分也反映了两者的成熟度阶段：QuantaAlpha 有
网页前端、跑过真实的自动循环挖掘批次（62个因子），是个在真实使用中持续演进的系统；CogAlpha 明确是
"benchmark-first"、还在验证阶段，目前唯一一次真实 LLM+真实数据运行 `elite_pool=0`，还没触发过
`finalize()`（组合训练+回测）。

## 后续可能的动作

1. 重新抓一次 QuantaAlpha `configs/backtest.yaml` 快照，把 CogAlpha 里过期的 topk/n_drop 更新掉（见
   第5节"待办"）。
2. 深入某一个具体机制的对比（比如两边 quality gate 的详细逐步对照、或 QuantaAlpha 的
   mutation/crossover prompt 设计）。
