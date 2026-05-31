# guwen

> 星灵顾问 —— 疑难杂症专家，专治"拿不准"。

一个 Claude Code skill。当主流程的引擎（感知 / 洞察 / 行动）对当前情况置信度不足时，把问题升到顾问层做深度心理分析。

## 核心方法

顾问的定位不是"再跑一遍模型"，而是**换一种透镜**：

- 不急着给答案，先看清问题本质
- 调用意愿流派心理学框架（新心学）作为分析视角
- 让深度推理模型（Claude Opus）处理复杂、多义、模糊的情境
- 输出再翻译成用户能理解的语言，保留"看见自己"的核心理念

适合放在 agent 链路里作为兜底层 —— 主引擎跑不动了就喊顾问。

## 何时调用

- 用户问题有多种可能的解释，主引擎选不出
- 当前引擎置信度低于阈值
- 涉及多种心理动机交织、需要专家视角的疑难情境
- 用户明确要求"帮我分析一下""这种情况怎么办""听听专家建议"

触发词：顾问、咨询、疑难问题、专家建议、怎么办、帮我分析、这种情况。

## 安装

```bash
# 全局可用
git clone https://github.com/jjjaaammmeeesss/guwen.git ~/.claude/skills/guwen

# 或只在某个项目里用
git clone https://github.com/jjjaaammmeeesss/guwen.git <project>/.claude/skills/guwen
```

注意：skill 中引用了 `src/consultant.py` 中的 `consult()` 函数 —— 这是星灵内核项目的内部模块；如果你不在该项目里用，把对应步骤替换成你自己的深度推理调用即可。

## 仓库内容

- `SKILL.md` —— 顾问身份卡、工作流、调用模板

## License

[MIT](LICENSE)
