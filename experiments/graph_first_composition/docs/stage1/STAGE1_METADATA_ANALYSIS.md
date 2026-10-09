# 第一阶段（Stage 1）元数据结果分析

本文件记录对当前 ClawHub 合并库的第一阶段分析结果。该阶段的目标是先完成
skill 生态的基础画像（ecosystem characterization）与初步风险扫描，而不是进行
深度动态分析或攻击实验。

## 分析范围

- 数据来源：
  - `data/experiment1_split/merged.sqlite3`
- 数据形态：
  - `skills`
  - `publishers`
  - `skill_versions`
  - `skill_files`
  - `permission_secret_hints`
  - `skill_archive_statuses`
- 快照时间：
  - 2026-03-12（当前增量刷新仍在运行，因此本文件是“快照版”分析，不是最终冻结版）

## 1. 基础规模

| Metric | Value |
| --- | ---: |
| Total Skills | 18,442 |
| Total Publishers | 8,071 |
| Total Versions | 43,503 |
| Total Files | 323,257 |
| Total Text Files | 321,519 |
| Permission Hint Records | 16,820 |

初步判断：

- ClawHub 已经不是一个小规模 marketplace，而是一个中等规模的 skill 生态。
- 文件与文本文件数量很高，说明很多 skill 实际上暴露了较丰富的公开内容，可支持后续静态分析、版本分析和权限披露分析。

## 2. 作者生态

| Metric | Value |
| --- | ---: |
| Unique Authors | 8,048 |
| Avg Skills / Author | 2.29 |
| Median Skills / Author | 1 |
| Single-skill Authors | 5,477 |
| Single-skill Author Ratio | 68.05% |
| Top 5 Authors Skill Share | 8.02% |
| Top 10 Authors Skill Share | 10.77% |
| Top 5 Authors Download Share | 18.7% |

### Top Publishers by Skill Count

| Author | Skills | Downloads | Stars |
| --- | ---: | ---: | ---: |
| ivangdavila | 852 | 696,076 | 1,918 |
| 1kalin | 272 | 99,838 | 72 |
| byungkyu | 128 | 804,799 | 1,054 |
| wpank | 116 | 87,103 | 39 |
| alirezarezvani | 111 | 79,761 | 75 |

### Top Publishers by Downloads

| Author | Skills | Downloads | Stars |
| --- | ---: | ---: | ---: |
| steipete | 55 | 1,186,135 | 4,154 |
| byungkyu | 128 | 804,799 | 1,054 |
| ivangdavila | 852 | 696,076 | 1,918 |
| TheSethRose | 19 | 175,658 | 703 |
| pskoett | 2 | 168,607 | 1,765 |

初步判断：

- 生态具有明显的“长尾作者 + 少数头部作者”结构。
- 68% 的作者只发布 1 个 skill，说明大量开发者只是尝试性发布。
- 少数作者掌握明显更多的下载量和流量入口，这会直接影响后续对 trust bootstrapping、冒充、排名操纵和 selection poisoning 的分析。

## 3. 热度分布

| Metric | Value |
| --- | ---: |
| Downloads P50 | 301 |
| Downloads P90 | 1,857 |
| Downloads P99 | 8,585 |
| Stars P50 | 0 |
| Stars P90 | 2 |
| Stars P99 | 19 |
| Skills with >=10k Downloads | 152 |
| Skills with >=100 Stars | 38 |

### Top Skills by Downloads

| Skill | Author | Downloads | Stars |
| --- | --- | ---: | ---: |
| self-improving-agent | pskoett | 168,087 | 1,761 |
| Summarize | steipete | 128,085 | 507 |
| Tavily Web Search | arun-8687 | 126,444 | 644 |
| Find Skills | JimLiuxinghai | 121,879 | 538 |
| Agent Browser | TheSethRose | 106,452 | 490 |

### Top Skills by Stars

| Skill | Author | Stars | Downloads |
| --- | --- | ---: | ---: |
| self-improving-agent | pskoett | 1,761 | 168,087 |
| Gog | steipete | 677 | 92,666 |
| Tavily Web Search | arun-8687 | 644 | 126,444 |
| Find Skills | JimLiuxinghai | 538 | 121,879 |
| Summarize | steipete | 507 | 128,085 |

初步判断：

- 热度高度集中，呈现典型的 power-law / long-tail 分布。
- stars 中位数为 0，P90 只有 2，说明绝大多数 skill 几乎没有稳定的社会证明。
- 这为后续“selection poisoning”“metadata manipulation”和“trust drift”提供了很强的现实动机。

## 4. 元数据覆盖率

| Metadata Field | Coverage |
| --- | ---: |
| Description | 18,442 |
| Updated At | 18,442 |
| Current Version | 18,442 |
| Category | 0 |
| Install Command | 0 |

### 描述长度

| Metric | Value |
| --- | ---: |
| Avg Description Length | 156.55 |
| Median Description Length | 159 |

初步判断：

- API 返回的 description / updated_at / current_version 覆盖率很高，足够支持基于文本与版本的第一轮离线分析。
- 但 API 层基本不提供稳定 category，也不提供 install command。
- 这意味着 Stage 1 的 category 分析必须依赖启发式分类，而 HTML-only 信号（如 install command、首页标签、UI badge）需要单独补采。

## 5. 技能功能类别（启发式分类）

由于当前 API 数据中 `category` 基本为空，本阶段使用 `name + description + tags + metadata_systems`
进行启发式分类，结果如下：

| Category | Count |
| --- | ---: |
| Other | 5,815 |
| Search/Web | 3,307 |
| Automation/Agent Ops | 3,120 |
| Media/Document | 1,709 |
| Developer Tools | 1,384 |
| Communication | 1,155 |
| Productivity | 1,149 |
| Finance | 803 |

初步判断：

- ClawHub 更像一个混合型 agent capability registry，而非单一办公/插件市场。
- `Search/Web` 与 `Automation/Agent Ops` 是最值得优先关注的功能簇，因为它们最接近“不可信输入 + 可执行动作”的高风险组合。

## 6. 能力与初步风险信号

下表来自 `permission_secret_hints` 和 `metadata_systems`：

| Capability / Signal | Count |
| --- | ---: |
| Shell/system execution hint | 13,915 |
| Credentialed external service | 9,784 |
| Local/document access | 9,179 |
| GitHub integration | 6,443 |
| Email integration | 4,346 |
| Browser/Web automation | 3,983 |
| Calendar integration | 1,342 |
| Slack integration | 1,054 |
| Notion integration | 460 |

解释边界：

- `Shell/system execution hint` 不等于“已证实可执行恶意 shell”，而是公开文本/安装说明/文件中存在较强 shell 或命令痕迹。
- 该信号更适合作为 Stage 1 的风险曝光指标，而不是最终风险定性。

初步判断：

- 高比例 skill 涉及凭证、外部 SaaS、浏览器或文档访问。
- 这支持后续关于 toxic composition、selection poisoning、semantic rug pull 与 memory laundering 的研究问题。

## 7. 当前阶段可写出的主要观察

1. ClawHub 已达到 18k+ skill 规模，且呈现明显长尾结构。
2. 供应侧由大量单-skill 作者和少数头部发布者共同构成，生态集中度不可忽视。
3. 热度和信任信号高度集中，stars/downloads 可被视为后续 trust manipulation 研究的重要对象。
4. API 元数据不提供稳定 taxonomy，限制了官方语义分类，但文本描述本身足够丰富，可支持启发式分类与权限/能力抽取。
5. `Search/Web`、`Automation/Agent Ops`、`Developer Tools` 等功能簇占比较高，说明生态中存在大量会接触不可信输入或高权限动作的 skill。
6. 公开可见的 credential、browser、GitHub、Email、Calendar 等信号表明，ClawHub 的核心风险不只是“单个 skill 是否恶意”，而是它们如何进入 agent 的决策闭环。

## 8. 当前阶段的局限

- 本文件基于 API 主归档快照，不包含完整 HTML-only 信号。
- 因此 `highlighted/popular/install command/comment UI badge` 等信息尚未纳入此版分析。
- 当前增量刷新仍在运行，因此部分计数后续可能小幅变化。
- 启发式类别划分仍然粗糙，后续可替换为更细的 taxonomy 或 embedding-based clustering。

## 9. 建议的下一步

在本阶段结果基础上，建议按以下顺序继续：

1. 完成当前增量刷新并冻结最终 Stage 1 数据快照。
2. 对失败项和增量项做 targeted backfill。
3. 补采 HTML-only 字段，完善 install command 与首页推荐信号。
4. 进入 Stage 2：`Selection Poisoning` 与 `Toxic Composition`。
5. 再进入 Stage 3：`Semantic Drift` 与 `Memory Laundering`。
