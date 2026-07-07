# 代码顾问 v2.0 (guwen)

> 基于 Karpathy 编程原则的资深代码审查专家。
> **核心设计：认知隔离** —— 通过独立 API 调用外部高级模型（Claude Opus 4.7），分析在全新上下文中进行，不受当前对话污染。

## v2.0 更新

从 v1.x 的心理顾问（星灵内核专用）**完全重写**为通用代码顾问：
- 适用场景从心理分析扩展到所有编程项目
- 四通道回退链（智创聚合 → codex CLI → xingluan → DeepSeek）
- 认知隔离：独立 API 调用，不受主对话上下文影响
- 严格遵循 Karpathy 四大编程原则

## 触发方式

- 自动：代码遇到复杂决策、连续 3 次修复失败、架构改动影响多模块
- 手动：`/guwen` 或说"帮我看看""帮我分析""顾问"
- 触发词：顾问、咨询、疑难问题、专家建议、怎么办、帮我分析、帮我决策、帮我看看

## 分析维度

| 维度 | 问题 |
|------|------|
| 架构边界 | 是否把业务逻辑写进了不该写的层？ |
| 简洁性 | 是否有更简单实现？是否过度抽象？ |
| 重复与孤儿 | 是否有重复函数/类型？死代码？ |
| 风险与修复 | 最小改动方案、重构顺序、风险评估 |

## 安装

```bash
# 全局安装
git clone https://github.com/jjjaaammmeeesss/guwen.git ~/.claude/skills/guwen

# 或使用 42plugin
42plugin install jjjaaammmeeesss/guwen
```

## 多通道回退

| 优先级 | 通道 | 模型 |
|--------|------|------|
| 主通道 | 智创聚合 | Claude Opus 4.7 |
| 备用 1 | codex CLI | GPT-5.5 |
| 备用 2 | xingluan | Claude Opus 4.7 |
| 兜底 | DeepSeek | DeepSeek V4 |

## 仓库内容

- `SKILL.md` — 顾问身份卡、四大原则、工作流
- `src/consultant.py` — 多通道调用脚本（认知隔离核心实现）

## License

[MIT](LICENSE)
