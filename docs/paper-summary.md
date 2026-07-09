<!-- local reference notes only; not part of the upstream repo's published docs -->
# 论文摘要留档：Cognitive Alpha Mining via LLM-Driven Code-Based Evolution

来源：arXiv:2511.18850（HTML版：`https://arxiv.org/html/2511.18850`）。本文是这篇论文关键事实的结构化摘要，
供本地查阅，不是逐字转录全文。所有数字均已在 2026-07-09 的会话中与 `/home/sunanying/CogAlpha` 的源码逐项核对过一次
（Ridge/LightGBM 是否融合这一条还做了第二次定向核实）。**这个文件只记录"论文说了什么"，不代表当前代码已经复现出论文的
实验结论** —— 代码与论文的对照结果、以及论文提到但代码尚未实施的部分，见 `/home/sunanying/CogAlpha/CLAUDE.md`
"论文复现现状"一节。

## 一句话概括

把LLM当作"具备认知能力的agent"，用代码形式表示alpha因子，通过多agent生成 + 多agent质检 + 演化式变异/交叉搜索，
在固定benchmark规则下筛选出真正有预测力的因子，再组合、回测。

## 七级21个领域agent层级

| Level | 层名 | agent数 | agent | Focus |
|---|---|---:|---|---|
| I | Market Structure & Cycle | 2 | AgentMarketCycle, AgentVolatilityRegime | 长期趋势、周期性阶段转换 |
| II | Extreme Risk & Fragility | 2 | AgentTailRisk, AgentCrashPredictor | 尾部风险、崩盘前兆、脆弱性累积 |
| III | Price–Volume Dynamics | 4 | AgentLiquidity, AgentOrderImbalance, AgentPriceVolumeCoherence, AgentVolumeStructure | 流动性、订单失衡、量价一致性 |
| IV | Price–Volatility Behavior | 5 | AgentDailyTrend, AgentReversal, AgentRangeVol, AgentLagResponse, AgentVolAsymmetry | 动量、反转、波动率聚集 |
| V | Multi-Scale Complexity | 2 | AgentDrawdown, AgentFractal | 回撤几何、分形粗糙度 |
| VI | Stability & Regime-Gating | 2 | AgentRegimeGating, AgentStability | 自适应门控、时间稳定性 |
| VII | Geometric & Fusion | 4 | AgentBarShape, AgentCreative, AgentComposite, AgentHerding | K线形态、多因子融合 |

合计21个agent，7个层级。

## 算法主流程（§2–§4）

1. **生成**（§3.1–3.2）：每个domain agent按 Diversified Guidance 的5种改写模式之一（light / moderate / creative /
   divergent / concrete）生成因子代码候选。
2. **质检**（§3.2，App A.3 顺序）：
   1. Code Quality Agent —— 检测语法错误、未定义变量、格式问题、非法库调用
   2. Code Repair Agent —— 自动修复（import、表达式、类型不匹配）
   3. Judge Agent —— 语义级校验：逻辑一致性、技术正确性、经济含义合理性
   4. Logic Improvement Agent —— 修正薄弱逻辑、重构公式、调整窗口参数
   5. Execution & Numerical Stability Check —— 沙箱执行；检测NaN、上溢/下溢、非法log
   6. Temporal Leakage Unit Test —— 确保没有前视shift、没有窗口对齐错误
   7. 通过→候选池；失败→丢弃或打回修复agent
   - NaN拒绝阈值：>30%的NaN值即丢弃。
3. **Fitness评估**（§3.3）：五个预测力指标——IC、ICIR、RankIC、RankICIR、MI（互信息）。
4. **Qualified/Elite筛选**（§3.4）：
   - 分位数门槛：Qualified要求五指标都超过同代65分位数；Elite要求都超过80分位数。
   - 绝对下限（Qualified）：IC/RankIC ≥ 0.005；ICIR/RankICIR ≥ 0.05；MI ≥ 0.02。
   - 绝对下限（Elite）：IC/RankIC ≥ 0.01；ICIR/RankICIR ≥ 0.1；MI ≥ 0.02。
   - Qualified进入下一代parent pool；Elite进入最终候选池；上一代前2名elite始终carry forward。
5. **演化算子**（§3.6）：Mutation Agent（微调已有因子代码）+ Crossover Agent（组合两个已有因子生成新因子）；
   三种演化类型：仅mutation / 仅crossover / crossover后接mutation。演化产物重新过一遍质检流程。
6. **自适应反馈**（§3.5）：每一代都采样——选2个表现最好的有效因子 + 2个表现最差的无效因子，分析并总结原因，
   把fitness结果和分析摘要注入下一代的生成prompt。
7. **注入**（§4.1）：每2代，把domain agent新生成、过滤后的因子注入parent pool。
8. **早停（本仓库未实现，见CLAUDE.md）**：论文描述了基于plateau的早停——若elite池的提升幅度 δ≤0.001，
   持续 plateau_win 代仍未突破，则提前终止演化。

## 关键超参数

| 参数 | 值 | 备注 |
|---|---:|---|
| LLM模型（论文默认） | gpt-oss-120b | 本仓库改用可配置的OpenAI兼容后端，默认 deepseek-v4-flash |
| Max token长度 | 4096 | 每次响应 |
| Task agent温度 | 0.7–1.2 | 本仓库未按角色区分温度，全局固定0.8 |
| QA agent温度 | 0.8 | |
| Initial pool | 80 | 来自task-specific agent的候选下限 |
| Parent pool | 32 | 过滤后进入下一代 |
| Children pool | 96 | = 3×parent pool，来自演化agent |
| 演化代数 | 24 | 每个domain agent周期 |
| Sub-cycle | 3 | 每个sub-cycle含8代 |
| Domain agent总数 | 21 | 跨7层 |
| Elite carry forward | 2 | 上一代前2名始终进入下一代 |
| Qualified分位数 | 65 | |
| Elite分位数 | 80 | |
| NaN拒绝阈值 | >30% | |
| 注入频率 | 每2代 | |

## Benchmark与数据集

### 主数据集：CSI300

- 300只中国大盘A股
- Train: 2011-01-01 – 2019-12-31；Valid: 2020-01-01 – 2020-12-31；Test: 2021-01-01 – 2024-12-01
- Label：10日forward return（主要），30日也测试过；价格列用open（进出场）
- 组合模型rolling训练步长：126天

### 论文实验覆盖、但本仓库未实现的多市场/多horizon部分

| 数据集 | 说明 |
|---|---|
| CSI500 | 500只中国中盘股，日期范围与CSI300相同 |
| S&P500 | 500只美股大盘股，Train 2007–2014, Valid 2015, Test 2016–2020 |
| HSI | 89只港股大盘股，Train 2011–2019, Valid 2020, Test 2021–2025 |
| HSCI | 约509只港股，日期同HSI |
| 30日horizon | 论文§4.6在10日和30日两种horizon下都做了实验 |

论文§4.6原文（Generalization to Different Settings）："we conduct experiments on five different
datasets (CSI300, CSI500, S&P500, HSI, HSCI) from three different stock markets (China, U.S., and HK),
using two training methods (LightGBM and Ridge) and two prediction horizons"。

### 组合训练方法（App B.4）

- **LightGBM（主要，默认）**：learning_rate=0.0001, num_leaves=32, max_depth=12, reg_alpha=1.0, reg_lambda=1.0,
  n_estimators=1000, subsample(bagging_fraction)=0.8, colsample_bytree(feature_fraction)=0.8。
- **Ridge（次要）**：alpha=10。
- **二者是两个独立、互不融合的方法**，各自成一行结果（"CogAlpha Ridge" / "CogAlpha LightGBM"），
  从不做平均/加权/stacking（此条已二次定向核实论文原文）。默认评估用LightGBM。

### 组合信号→回测（Top-50/Drop-5）

- 每天选预测分数最高的50只股票；调仓时最多替换5个持仓（drop-5）；等权持仓；下一期open价成交。
- 交易成本：买入(open) 0.05%，卖出(close) 0.15%，单笔最低5元。

### 回测指标公式

```
AER（年化超额收益）：
  daily excess: r_t = r_port_t − r_bench_t − cost_t
  μ = mean(r_t)
  AER = μ × 252

IR（信息比率）：
  σ = std(r_t)
  IR = (μ / σ) × √252
```

### Fitness指标公式

```
IC_t  = corr(factor_i,t, return_i,t+1)  （截面Pearson相关）， IC = mean_t(IC_t)
ICIR  = mean(IC_t) / std(IC_t)  （不年化）
RankIC_t 同IC_t但用秩相关（Spearman风格）， RankIC = mean_t(RankIC_t)
RankICIR = mean(RankIC_t) / std(RankIC_t)
MI(F,R) = ∬ p(f,r) log[p(f,r)/(p(f)p(r))] df dr  （互信息，非线性依赖）
```

## 时序泄漏与代码执行安全（论文原始描述）

- **Temporal Leakage Unit Test**：检测前视shift（如`shift(-1)`）、识别未对齐的rolling窗口、拒绝任何隐式时序违规，
  只有零泄漏的因子才被接受。（本仓库代码实际做了两层防护，比这段描述更严格——见CLAUDE.md）
- **沙箱执行**：代码在受限沙箱中执行；检测运行时错误（NaN、上溢、下溢、非法log）；接受前做数值稳定性检查；
  失败的代码打回修复agent或丢弃。（本仓库代码实际用子进程池+SIGKILL超时+受限import白名单三层隔离，
  比这段描述更重——见CLAUDE.md）

## 论文报告的实验结果（仅作留档，代码尚未复现）

### Table 1：CogAlpha vs. 21个baseline（CSI300, 10日horizon）

| 指标 | CogAlpha | 最佳baseline | 提升 |
|---|---:|---|---|
| IC | 0.0591 | 0.0358 (Alpha158) | +65% |
| RankIC | 0.0814 | 0.0412 (LightGBM) | +98% |
| ICIR | 0.3410 | 0.2932 (RandomForest) | +16% |
| RankICIR | 0.4350 | 0.4385 (RandomForest) | −0.8%（接近持平） |
| AER | 0.1639 | 0.1198 (Alpha360) | +37% |
| IR | 1.8999 | 1.3166 (XGBoost) | +44% |

### Table 3：消融实验

| 配置 | IC | RankIC |
|---|---:|---:|
| Baseline（仅Agent） | 0.0300 | 0.0318 |
| +Evolution (E) | 0.0219 | 0.0420 |
| +Adaptive (EA) | 0.0315 | 0.0491 |
| +Guidance (EAG) | 0.0414 | 0.0501 |
| +Hierarchy (EAGH，即完整CogAlpha) | 0.0591 | 0.0814 |

> 提醒：这些是论文自己跑出来的数字。`docs/system-walkthrough.md` 记录的本仓库唯一一次真实LLM+真实数据validation-scale
> 运行（216个候选）最终 `elite_pool=0`，没有训练组合信号、没有跑回测——上面这张表的结果**目前还没有被这份代码复现出来过**。
