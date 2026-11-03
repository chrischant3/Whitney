"""High-signal static indicators for prompt-injection-like instructions."""

import re

RULES = (
    (
        "instruction_override",
        "high",
        re.compile(
            r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,100}"
            r"\b(?:previous|prior|above|system|developer|all|any)\b.{0,40}"
            r"\b(?:instructions?|prompts?|rules?|messages?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "secret_or_prompt_disclosure",
        "high",
        re.compile(
            r"\b(?:reveal|expose|print|dump|leak|exfiltrate|disclose|output|show|send)\b.{0,100}"
            r"\b(?:system\s+prompt|developer\s+(?:message|instructions?)|hidden\s+instructions?|"
            r"secrets?|credentials?|api[\s_-]?keys?|tokens?|"
            r"(?:stored|saved|existing|user|environment)[\s_-]+passwords?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "privileged_role_change",
        "high",
        re.compile(
            r"\b(?:you are now|pretend to be|role[- ]?play as|act as)\b.{0,70}"
            r"\b(?:unrestricted|unfiltered|system|developer|administrator|admin|root|dan)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "control_token_or_fake_role",
        "medium",
        re.compile(
            r"<\|(?:system|developer|assistant)\|>|\[(?:SYSTEM|DEVELOPER)\]|"
            r"BEGIN\s+(?:SYSTEM|DEVELOPER)\s+(?:PROMPT|MESSAGE)",
            re.IGNORECASE,
        ),
    ),
    (
        "chinese_instruction_override",
        "high",
        re.compile(r"(?:忽略|无视|忘记|绕过).{0,24}(?:之前|上文|系统|开发者).{0,12}(?:指令|提示|规则|限制)"),
    ),
    (
        "chinese_secret_disclosure",
        "high",
        re.compile(r"(?:泄露|透露|显示|输出|打印|提取).{0,24}(?:系统提示|开发者指令|密钥|令牌|凭证|环境变量)"),
    ),
)


def scan_text(text: str) -> list[dict[str, str]]:
    """Return suspicious pattern matches with context; never execute the text."""
    findings = []
    for category, severity, pattern in RULES:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 90)
            end = min(len(text), match.end() + 90)
            findings.append(
                {
                    "category": category,
                    "severity": severity,
                    "matched": " ".join(match.group(0).split())[:240],
                    "snippet": " ".join(text[start:end].split()),
                }
            )
    return findings
