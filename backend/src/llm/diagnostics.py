"""模型响应诊断：仅保留有界、脱敏的正文片段。"""
import hashlib
import re


def response_summary(response, secrets=()):
    if not isinstance(response, str):
        return {'response_type': type(response).__name__}
    safe = response
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, '[REDACTED]')
    safe = re.sub(r'https?://[^\s<>"\']+|data:[^\s"\']+', '[REDACTED_URL]', safe)
    safe = re.sub(r'(?i)\bBearer\s+[^\s"\']+|\bsk-[\w-]+', '[REDACTED]', safe)
    safe = re.sub(
        r'''(?ix)(["']?(?:api[_-]?key|access[_-]?token|token|secret|password|authorization)["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''',
        r'\1"[REDACTED]"', safe,
    )
    return {
        'response_chars': len(response),
        'response_sha256': hashlib.sha256(response.encode('utf-8')).hexdigest(),
        'response_prefix': safe[:1000],
        'response_suffix': safe[-500:] if len(safe) > 1000 else '',
    }
