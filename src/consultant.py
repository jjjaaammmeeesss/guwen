"""
星灵顾问 —— 独立认知隔离分析引擎。

通过独立的 API/CLI 调用（不同模型/不同上下文），对代码和架构问题进行认知隔离式深度诊断。
认知隔离 = 分析模型看不到当前对话上下文，只看到顾问的系统提示 + 用户问题本身。

回退链：智创聚合 (Claude Opus 4.7) → codex CLI (GPT-5.5) → OpenAI API (GPT-4o) → xingluan (Claude Opus) → DeepSeek
"""

import json
import os
import sys
import subprocess
from pathlib import Path

# ── 默认顾问系统提示 ──────────────────────────────────────
DEFAULT_SYSTEM_PROMPT = (
    "你是一位资深软件架构师，严格遵循 Andrej Karpathy 的编程原则审查代码、诊断问题、做架构决策。\n\n"
    "四大原则：\n"
    "1. **想清楚再写代码** — 陈述假设，有多个解读时全列出来。有更简单方法就说。不确定时先问，不猜。\n"
    "2. **简洁优先** — 最少代码解决问题，不写推测性代码。不为单次使用建抽象。200行能写成50行就应重写。\n"
    "3. **外科手术式改动** — 只碰必须改的。不改相邻代码、格式。匹配现有风格。发现死代码提出来但不删。\n"
    "4. **目标驱动执行** — 把任务转化为可验证目标。多步骤先列计划，每步标注验证方式。\n\n"
    "分析维度：\n"
    "- **架构边界** — 是否跨层写业务逻辑？是否绕过已有 service/repository？\n"
    "- **简洁性** — 是否有更简单实现？是否过度抽象？命名是否一致？\n"
    "- **重复与孤儿** — 是否有重复函数/类型？是否有未使用的 import/变量？\n"
    "- **风险与修复** — 最小改动方案是什么？重构顺序和风险评估？\n\n"
    "响应规则：\n"
    "- 每项判断标注证据（文件:行号）和置信度\n"
    "- 区分三个等级：🔴确定有问题 / 🟡疑似有问题 / 🔵风格建议\n"
    "- 不确定的事诚实说「不确定」，不做猜测\n"
    "- 最先说最严重的问题，再列次要\n"
    "- 直接输出分析，不调用外部工具或搜索网络"
)


def _find_auth_from_claude_settings():
    """从 Claude Code 的 settings.json 读取 API 配置。"""
    settings_paths = [
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".claude" / "settings.local.json",
    ]
    for sp in settings_paths:
        if sp.exists():
            try:
                cfg = json.loads(sp.read_text(encoding="utf-8"))
                routes = cfg.get("apiRoutes", {})
                profiles = routes.get("profiles", {})
                active = routes.get("active", "")
                return profiles, active
            except (json.JSONDecodeError, KeyError):
                continue
    return {}, ""


def _resolve_configs(skip_codex: bool = False):
    """解析所有可用的 API 配置，按优先级排序。

    参数:
        skip_codex: 为 True 时不包含 codex CLI 通道

    返回: list[dict], 每个 dict 包含 method, api_key, base_url, model, type, label

    method: "codex_cli" | "openai" | "anthropic"

    优先级（非 codex 部分）：
    1. CONSULTANT_* 环境变量（显式覆盖）
    2. settings.json 中 openai profile（用户自己的 GPT API）
    3. settings.json 中 xingluan profile（Claude Opus，认知隔离）
    4. settings.json 中活跃 profile（兜底）
    5. ANTHROPIC_* / OPENAI_* 环境变量（最后兜底）
    """
    profiles, active = _find_auth_from_claude_settings()

    configs = []

    # ── 第 0 优先级：智创聚合 Claude API（主力通道，Opus 4.7） ──
    if "zhichuang" in profiles and "zhichuang" != active:
        p = profiles["zhichuang"]
        key = p.get("authToken", "")
        if key and "请替换" not in key:
            configs.append({
                "method": "anthropic",
                "api_key": key,
                "base_url": p.get("baseUrl", ""),
                "model": p.get("defaultModel", "claude-opus-4-7"),
                "type": "anthropic",
                "label": f"智创聚合 Claude ({p.get('defaultModel', 'claude-opus-4-7')})",
            })

    # ── 第 1 优先级：codex CLI（备用通道，天然认知隔离） ──
    if not skip_codex:
        codex_path = _find_codex()
        if codex_path:
            # 从 codex config.toml 读取当前模型
            codex_model = _read_codex_model()
            configs.append({
                "method": "codex_cli",
                "api_key": "",
                "base_url": "",
                "model": codex_model or "gpt-5.5",
                "type": "codex_cli",
                "label": f"codex CLI ({codex_model or 'gpt-5.5'})",
            })

    # ── 第 2 优先级：CONSULTANT_* 显式覆盖 ──
    consultant_key = os.environ.get("CONSULTANT_API_KEY", "")
    consultant_url = os.environ.get("CONSULTANT_BASE_URL", "")
    consultant_model = os.environ.get("CONSULTANT_MODEL", "")
    consultant_type = os.environ.get("CONSULTANT_API_TYPE", "")

    if consultant_key:
        method = "openai" if consultant_type == "openai" else "anthropic"
        configs.append({
            "method": method,
            "api_key": consultant_key,
            "base_url": consultant_url or "https://api.anthropic.com",
            "model": consultant_model or "claude-sonnet-4-6",
            "type": consultant_type or "anthropic",
            "label": "CONSULTANT_* 环境变量",
        })

    # ── 第 3 优先级：openai profile（用户自己的 GPT API） ──
    if "openai" in profiles and "openai" != active:
        p = profiles["openai"]
        key = p.get("authToken", "")
        if key and "请替换" not in key:
            configs.append({
                "method": "openai",
                "api_key": key,
                "base_url": p.get("baseUrl", ""),
                "model": p.get("defaultModel", "gpt-4o"),
                "type": "openai",
                "label": "OpenAI GPT (备用)",
            })

    # ── 第 4 优先级：OPENAI_* 环境变量（用户自己的 GPT API） ──
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        configs.append({
            "method": "openai",
            "api_key": openai_key,
            "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com"),
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o"),
            "type": "openai",
            "label": "OpenAI GPT (环境变量备用)",
        })

    # ── 第 5 优先级：xingluan profile（Claude Opus，认知隔离） ──
    if "xingluan" in profiles and "xingluan" != active:
        p = profiles["xingluan"]
        configs.append({
            "method": "anthropic",
            "api_key": p.get("authToken", ""),
            "base_url": p.get("baseUrl", ""),
            "model": p.get("defaultModel", "claude-opus-4-7"),
            "type": "anthropic",
            "label": "xingluan Claude Opus (认知隔离)",
        })

    # ── 第 6 优先级：活跃 profile（兜底） ──
    if active in profiles and active not in ["xingluan", "openai"]:
        p = profiles[active]
        configs.append({
            "method": "anthropic",
            "api_key": p.get("authToken", ""),
            "base_url": p.get("baseUrl", ""),
            "model": p.get("defaultModel", ""),
            "type": p.get("type", "anthropic"),
            "label": f"{active} (活跃兜底)",
        })

    # ── 第 7 优先级：ANTHROPIC_* 环境变量兜底 ──
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        configs.append({
            "method": "anthropic",
            "api_key": anthropic_key,
            "base_url": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            "type": "anthropic",
            "label": "ANTHROPIC_* 环境变量",
        })

    return configs


def _find_codex():
    """查找 codex CLI 路径。"""
    import shutil
    codex = shutil.which("codex")
    return codex


def _read_codex_model():
    """从 codex config.toml 读取当前使用的模型。"""
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return None
    try:
        # 简单解析 TOML 的 model 字段（不引入 toml 依赖）
        for line in config_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("model") and "=" in line:
                model = line.split("=", 1)[1].strip().strip('"').strip("'")
                return model
    except Exception:
        pass
    return None


def consult(
    prompt: str,
    system_prompt: str = "",
    model: str = "",
    max_tokens: int = 4096,
    skip_codex: bool = False,
) -> str:
    """
    调用独立 API/CLI 进行深度分析，返回分析结果。
    按优先级尝试：智创聚合 → codex CLI → OpenAI API → xingluan Claude → DeepSeek

    参数:
        prompt: 用户问题 / 需要分析的内容
        system_prompt: 顾问的系统提示（不传则用默认的 Karpathy 编程原则框架）
        model: 指定模型（不传则自动选择）
        max_tokens: 最大输出 token 数（仅 API 模式使用）
    """
    if not system_prompt:
        system_prompt = DEFAULT_SYSTEM_PROMPT

    configs = _resolve_configs(skip_codex=skip_codex)

    if not configs:
        return (
            "❌ 顾问引擎未配置：找不到可用的 API 或 codex CLI。\n\n"
            "请确保以下至少一项可用：\n"
            "1. codex CLI 已安装且已登录（运行 codex doctor 检查）\n"
            "2. 在 ~/.claude/settings.json 中配置了 openai 或 xingluan profile\n"
            "3. 设置了环境变量 CONSULTANT_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY"
        )

    if model:
        for c in configs:
            c["model"] = model

    # ── 依次尝试每个通道 ──────────────────────────────
    errors = []
    for i, cfg in enumerate(configs):
        method = cfg["method"]
        label = cfg["label"]
        cfg_model = cfg["model"]

        try:
            if method == "codex_cli":
                result = _consult_via_codex(prompt, system_prompt)
            elif method == "openai":
                result = _consult_via_openai(
                    prompt, system_prompt,
                    cfg["api_key"], cfg["base_url"], cfg_model, max_tokens,
                )
            else:
                result = _consult_via_anthropic(
                    prompt, system_prompt,
                    cfg["api_key"], cfg["base_url"], cfg_model, max_tokens,
                )

            # 标注通道来源
            prefix = ""
            if i > 0:
                # 说明之前有通道失败，当前是回退通道
                prefix = f"🔄 **注意**：优先通道不可用，当前使用回退通道（{label}，模型 {cfg_model}）\n\n---\n\n"
            elif method == "codex_cli":
                prefix = f"🧠 *星灵顾问 · 独立分析 (via codex CLI, {cfg_model})*\n\n"
            else:
                prefix = f"🧠 *星灵顾问 · 独立分析 (via {label}, {cfg_model})*\n\n"

            return prefix + result

        except Exception as e:
            error_msg = f"[{label}] {type(e).__name__}: {e}"
            errors.append(error_msg)
            continue

    # ── 所有通道都失败 ──────────────────────────────────
    return (
        "❌ 所有顾问通道均调用失败：\n\n"
        + "\n".join(f"  {i+1}. {e}" for i, e in enumerate(errors))
        + "\n\n请检查网络连接、API 配置，或运行 codex doctor 检查 codex 状态。"
    )


# ═══════════════════════════════════════════════════════════
# codex CLI 通道
# ═══════════════════════════════════════════════════════════

def _consult_via_codex(prompt: str, system_prompt: str) -> str:
    """通过 codex CLI 进行独立分析（主通道）。

    codex exec 在独立进程中运行，天然实现认知隔离。
    整个系统提示嵌入到 prompt 中，作为 agent 的初始指令。
    """
    import shutil
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError("codex CLI 未安装或不在 PATH 中")

    # 构建完整提示：强制要求直接输出分析，禁止开场白
    full_prompt = (
        f"{system_prompt}\n\n"
        f"---\n\n"
        f"请对以下代码/架构问题进行深度诊断。\n\n"
        f"**重要规则**：\n"
        f"1. 直接开始输出分析内容，不要说「我会...」「让我...」「明白」等开场白\n"
        f"2. 从架构边界、简洁性、重复与孤儿、风险与修复四个维度展开\n"
        f"3. 每项判断标注证据（文件:行号）和置信度\n"
        f"4. 区分三个等级：🔴确定有问题 / 🟡疑似有问题 / 🔵风格建议\n"
        f"5. 不要调用外部工具或搜索网络\n\n"
        f"【用户问题】\n{prompt}"
    )

    try:
        result = subprocess.run(
            [codex_path, "exec",
             full_prompt],
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise RuntimeError(f"codex 返回非零退出码 {result.returncode}: {stderr}")

        output = result.stdout.strip()
        if not output:
            raise RuntimeError("codex 返回了空内容")

        # 质量检查：太短的响应视为无效（codex 有时只返回开场白）
        MIN_ANALYSIS_LENGTH = 200
        if len(output) < MIN_ANALYSIS_LENGTH:
            raise RuntimeError(
                f"codex 返回内容过短（{len(output)} 字符），"
                f"疑似仅返回开场白而非实质分析"
            )

        # 检测纯开场白模式（只有"我会/让我/明白"而没有实质分析）
        opening_only_patterns = [
            "我会以", "让我来", "我理解", "明白了", "收到",
            "I'll", "Let me", "I understand",
        ]
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        if lines and len(lines) <= 3:
            for pattern in opening_only_patterns:
                if lines[0].startswith(pattern):
                    raise RuntimeError(
                        f"codex 疑似仅返回开场白（{len(lines)} 行，"
                        f"首行以 '{pattern}' 开头），跳过此通道"
                    )

        return output

    except subprocess.TimeoutExpired:
        raise RuntimeError("codex CLI 超时（300s）")
    except FileNotFoundError:
        raise RuntimeError("codex CLI 未找到")


# ═══════════════════════════════════════════════════════════
# Anthropic API 通道
# ═══════════════════════════════════════════════════════════

def _consult_via_anthropic(
    prompt: str,
    system_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
) -> str:
    """通过 Anthropic SDK 或 HTTP 调用 Anthropic-compatible API。"""
    try:
        import anthropic

        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
        )

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )

        text_blocks = []
        for block in response.content:
            if hasattr(block, "text"):
                text_blocks.append(block.text)

        return "\n\n".join(text_blocks)

    except ImportError:
        return _consult_via_anthropic_http(
            prompt, system_prompt, api_key, base_url, model, max_tokens
        )


def _consult_via_anthropic_http(
    prompt: str,
    system_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
) -> str:
    """通过 HTTP 直接调用 Anthropic-compatible API（不需要 anthropic SDK）。"""
    import urllib.request
    import urllib.error

    url = base_url.rstrip("/") + "/v1/messages"

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data.get("content", [])
        if isinstance(content, list):
            return "\n\n".join(b.get("text", "") for b in content if b.get("text"))
        elif isinstance(content, str):
            return content
        else:
            return json.dumps(data, ensure_ascii=False, indent=2)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise RuntimeError(f"HTTP {e.code}: {error_body}")
    except Exception as e:
        raise RuntimeError(f"HTTP 请求失败: {e}")


# ═══════════════════════════════════════════════════════════
# OpenAI API 通道
# ═══════════════════════════════════════════════════════════

def _consult_via_openai(
    prompt: str,
    system_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
) -> str:
    """通过 OpenAI SDK 或 HTTP 调用 OpenAI-compatible API。"""
    try:
        import openai

        client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/") + "/v1",
        )

        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )

        return response.choices[0].message.content or ""

    except ImportError:
        return _consult_via_openai_http(
            prompt, system_prompt, api_key, base_url, model, max_tokens
        )


def _consult_via_openai_http(
    prompt: str,
    system_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
) -> str:
    """通过 HTTP 直接调用 OpenAI-compatible API（不需要 openai SDK）。"""
    import urllib.request
    import urllib.error

    url = base_url.rstrip("/") + "/v1/chat/completions"

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            return msg.get("content", "") or json.dumps(data, ensure_ascii=False, indent=2)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise RuntimeError(f"HTTP {e.code}: {error_body}")
    except Exception as e:
        raise RuntimeError(f"HTTP 请求失败: {e}")


# ═══════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="星灵顾问 — 独立认知隔离分析（智创聚合 → codex CLI → OpenAI → Claude Opus → DeepSeek 多通道回退）"
    )
    parser.add_argument("prompt", nargs="*", help="要分析的问题（也可通过 stdin）")
    parser.add_argument("--system", "-s", default="", help="自定义系统提示")
    parser.add_argument("--model", "-m", default="", help="指定模型")
    parser.add_argument("--max-tokens", "-t", type=int, default=4096)
    parser.add_argument("--skip-codex", action="store_true", help="跳过 codex CLI，直接使用 API")
    args = parser.parse_args()

    prompt_text = " ".join(args.prompt) if args.prompt else sys.stdin.read().strip()

    if not prompt_text:
        print("用法: python consultant.py <问题文本>")
        print("       echo <问题文本> | python consultant.py")
        sys.exit(1)

    result = consult(
        prompt=prompt_text,
        system_prompt=args.system,
        model=args.model,
        max_tokens=args.max_tokens,
        skip_codex=args.skip_codex,
    )
    print(result)
