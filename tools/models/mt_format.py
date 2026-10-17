# SPDX-License-Identifier: Apache-2.0
"""Prompt of the translate stage (zh_en_v1). The runtime builds the same text in engine.cpp.

    任务：中译英\n[上文：<context>\n]中文：<Chinese>\n英文：<English><eos>

The key sequence is deliberately absent: the Chinese is already chosen, and a model that sees the keys
learns to guess English from them instead of translating.
"""

PROMPT_VERSION = 'zh_en_v1'
CONTEXT_CHARS = 64


def prompt(chinese: str, context: str = '') -> str:
    context = context.strip()[-CONTEXT_CHARS:]
    head = '任务：中译英\n' + (f'上文：{context}\n' if context else '')
    return f'{head}中文：{chinese}\n英文：'
