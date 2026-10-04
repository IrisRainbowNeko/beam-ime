#!/usr/bin/env python3
"""Render the README diagrams (docs/images/*.svg) in Chinese and English.

    python3 tools/render_diagrams.py

The candidate lists are measured, not invented: Beam rows come from the 0.1.0-beta.2 release
model (beam-0.6b-q8_0.gguf, beam_ms 1000), Rime Ice rows from a fresh rime_ice schema with an
empty user dictionary. Re-measure before changing them.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"

FONT = "'Noto Sans CJK SC','Source Han Sans SC','PingFang SC','Microsoft YaHei',sans-serif"
MONO = "'JetBrains Mono','Cascadia Mono','DejaVu Sans Mono',monospace"

STYLE = f"""
  .bg{{fill:#ffffff}} .card{{fill:#f6f8fa;stroke:#d0d7de}} .t{{fill:#1f2328}} .m{{fill:#59636e}}
  .acc{{fill:#eef4ff;stroke:#4f6bed}} .accb{{fill:#4f6bed}} .acct{{fill:#3b55d9}} .on{{fill:#ffffff}}
  .rime{{fill:#f3f7f1;stroke:#6f9a5d}} .rimet{{fill:#4e7a3c}} .cloud{{fill:#fff6ec;stroke:#d98a35}}
  .cloudt{{fill:#b5651d}} .bad{{fill:#b42318}} .ln{{stroke:#8c959f;fill:none}} .ah{{fill:#8c959f}}
  .sep{{stroke:#d0d7de}}
  text{{font-family:{FONT}}} .mono{{font-family:{MONO}}}
  @media (prefers-color-scheme: dark) {{
    .bg{{fill:#0d1117}} .card{{fill:#161b22;stroke:#30363d}} .t{{fill:#e6edf3}} .m{{fill:#9198a1}}
    .acc{{fill:#18213f;stroke:#7d93ff}} .accb{{fill:#5b74f0}} .acct{{fill:#9aabff}}
    .rime{{fill:#152015;stroke:#5f8a4e}} .rimet{{fill:#8cc477}} .cloud{{fill:#261a0d;stroke:#b8762d}}
    .cloudt{{fill:#f0a95c}} .bad{{fill:#ff7b72}} .ln{{stroke:#6e7681}} .ah{{fill:#6e7681}}
    .sep{{stroke:#30363d}}
  }}
"""


class Svg:
    def __init__(self, w: int, h: int, title: str) -> None:
        self.w, self.h = w, h
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{escape(title)}">',
            f"<title>{escape(title)}</title>",
            f"<style>{STYLE}</style>",
            '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ah"/></marker></defs>',
            f'<rect class="bg" width="{w}" height="{h}" rx="12"/>',
        ]

    def box(self, x, y, w, h, cls="card", rx=10):
        self.parts.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" stroke-width="1.5"/>')

    def text(self, x, y, s, cls="t", size=15, anchor="start", weight=400, mono=False):
        c = cls + (" mono" if mono else "")
        self.parts.append(f'<text class="{c}" x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
                          f'font-weight="{weight}">{escape(s)}</text>')

    def arrow(self, x1, y1, x2, y2, both=False, dash=False):
        extra = ' marker-start="url(#a)"' if both else ""
        d = ' stroke-dasharray="5 4"' if dash else ""
        self.parts.append(f'<path class="ln" d="M{x1} {y1}L{x2} {y2}" stroke-width="1.6" marker-end="url(#a)"{extra}{d}/>')

    def line(self, x1, y1, x2, y2):
        self.parts.append(f'<path class="sep" d="M{x1} {y1}L{x2} {y2}" stroke-width="1"/>')

    def save(self, path: Path) -> None:
        path.write_text("\n".join(self.parts + ["</svg>"]) + "\n", encoding="utf-8")


L = {
    "zh": {
        "how_title": "Beam 怎样把几个字母变成整句",
        "ctx": "上文（已上屏）", "ctx_v": "我在学深度学习", "keys": "按键",
        "prompt": "Prompt",
        "p1": "上文：我在学深度学习", "p2": "按键：s j w l", "p3": "结果：",
        "model": "Keys-LLM 0.6B", "model_s": "Qwen3 微调 · Q8_0 · llama.cpp",
        "model_b": "Vulkan GPU / CPU，全部在本机",
        "s1": "① 增量 Top-1", "s1s": "KV 前缀复用 + 上次结果作草稿验证",
        "s2": "② 限时 beam search", "s2s": "5 束 · 每次按键 100 ms 预算",
        "menu": "候选菜单", "from_beam": "模型", "rime_more": "6+  雾凇词典候选",
        "ctx_note": "上文决定首选：同样是 sjwl，",
        "ctx_a": "「我在学深度学习」→ 神经网络", "ctx_b": "「昨晚又熬夜了」→ 睡觉晚了",
        "run": "运行结构",
        "f1": "输入法框架", "f1s": "Fcitx5-Rime / 小狼毫",
        "f2": "Rime 插件", "f2s": "beam_translator",
        "f3": "本机通信", "f3s": "Unix socket / TCP + token",
        "f4": "beamd", "f4s": "模型推理服务",
        "f5": "雾凇 translator", "f5s": "词典兜底",
        "fallback": "超过 400 ms 或服务不可用：这次按键只显示雾凇候选，打字不会卡住。",
        "cmp_title": "和现有输入法的区别",
        "c1": "传统拼音输入法", "c1s": "词典 + 词频 / n-gram（如雾凇拼音）",
        "c1a": "按键切分成音节", "c1b": "在词典里查词和词组", "c1c": "按词频拼接成句",
        "c1x": "本机 · 即时 · 长句靠词库收录",
        "c2": "云端 AI 输入", "c2s": "大模型运行在服务器上",
        "c2a": "按键和上下文发送到服务器", "c2b": "云端大模型生成候选", "c2c": "联网返回候选",
        "c2x": "需要联网 · 输入内容离开本机",
        "c3": "Beam", "c3s": "按键条件化的本机语言模型",
        "c3a": "按键 + 最近 64 字上文", "c3b": "0.6B 模型直接生成整句", "c3c": "限时 beam search 出 5 个候选",
        "c3x": "本机 · GPU 可选 · 780M 上约 50–150 ms",
        "tk": "按键", "tr": "雾凇拼音首选", "tb": "Beam 首选",
        "ctxrow": "上文「昨晚又熬夜了」",
        "foot": "实测：雾凇拼音为全新用户词库、无上下文；Beam 为 0.1.0-beta.2 发布模型。云端一栏是一般形态示意，不对应具体产品。",
    },
    "en": {
        "how_title": "How Beam turns a few keys into a sentence",
        "ctx": "Context (committed)", "ctx_v": "我在学深度学习", "keys": "Keys",
        "prompt": "Prompt",
        "p1": "上文：我在学深度学习", "p2": "按键：s j w l", "p3": "结果：",
        "model": "Keys-LLM 0.6B", "model_s": "Qwen3 tune · Q8_0 · llama.cpp",
        "model_b": "Vulkan GPU / CPU, fully local",
        "s1": "① Incremental Top-1", "s1s": "KV reuse + last result as draft",
        "s2": "② Bounded beam search", "s2s": "5 beams · 100 ms per keystroke",
        "menu": "Candidates", "from_beam": "model", "rime_more": "6+  Rime Ice dictionary",
        "ctx_note": "Context picks the first candidate for the same sjwl:",
        "ctx_a": "“I'm studying deep learning” → 神经网络 (neural network)",
        "ctx_b": "“Up late again” → 睡觉晚了 (slept late)",
        "run": "Runtime",
        "f1": "IME framework", "f1s": "Fcitx5-Rime / Weasel",
        "f2": "Rime plugin", "f2s": "beam_translator",
        "f3": "Local transport", "f3s": "Unix socket / TCP + token",
        "f4": "beamd", "f4s": "inference service",
        "f5": "Rime Ice", "f5s": "dictionary fallback",
        "fallback": "Over 400 ms or service down: Rime Ice only for that keystroke.",
        "cmp_title": "How Beam differs from existing input methods",
        "c1": "Classic pinyin IME", "c1s": "dictionary + frequency / n-gram (e.g. Rime Ice)",
        "c1a": "split keys into syllables", "c1b": "look up words and phrases", "c1c": "join by frequency",
        "c1x": "local · instant · needs dictionary entries",
        "c2": "Cloud AI input", "c2s": "large model on a server",
        "c2a": "send keys and context to a server", "c2b": "cloud model generates", "c2c": "candidates over the network",
        "c2x": "needs network · input leaves the machine",
        "c3": "Beam", "c3s": "keys-conditioned local language model",
        "c3a": "keys + last 64 characters", "c3b": "0.6B model writes the sentence", "c3c": "beam search → 5 candidates",
        "c3x": "local · GPU optional · ~50–150 ms on a 780M",
        "tk": "Keys", "tr": "Rime Ice first candidate", "tb": "Beam first candidate",
        "ctxrow": "context “up late again”",
        "foot": "Measured: Rime Ice with a fresh user dictionary and no context; Beam 0.1.0-beta.2 release model. The cloud column is a generic sketch, not a specific product.",
    },
}

# (keys, context label key or None, Rime Ice first, Beam first) — measured, see the module docstring.
EXAMPLES = [
    ("wmqcfb", None, "唯美清纯发布", "我们去吃饭吧"),
    ("jtwsqcfb", None, "今天晚上其次伐兵", "今天晚上去吃饭吧"),
    ("yonglinux", None, "用力奴性", "用Linux"),
    ("sjwl", "ctxrow", "神经网络", "睡觉晚了"),
]
MENU = ["神经网络", "睡觉晚了", "设计网络", "数据网络", "设计未来"]  # sjwl with 上文 我在学深度学习


def how_it_works(t: dict) -> Svg:
    s = Svg(1000, 570, t["how_title"])
    s.text(30, 44, t["how_title"], size=22, weight=700)
    # inputs
    s.box(30, 80, 190, 80)
    s.text(46, 108, t["ctx"], "m", 13)
    s.text(46, 140, t["ctx_v"], size=17, weight=600)
    s.box(30, 190, 190, 80)
    s.text(46, 218, t["keys"], "m", 13)
    s.text(46, 252, "s j w l", size=24, weight=700, mono=True)
    s.arrow(220, 120, 258, 160)
    s.arrow(220, 230, 258, 190)
    # prompt
    s.box(260, 100, 210, 130)
    s.text(276, 126, t["prompt"], "m", 13)
    for i, line in enumerate((t["p1"], t["p2"], t["p3"])):
        s.text(276, 156 + i * 26, line, size=15, mono=True)
    s.arrow(470, 175, 508, 175)
    # model
    s.box(510, 70, 230, 250, "acc", 12)
    s.text(526, 100, t["model"], "acct", 19, weight=700)
    s.text(526, 122, t["model_s"], "m", 12)
    s.text(526, 140, t["model_b"], "m", 12)
    for i, (a, b) in enumerate(((t["s1"], t["s1s"]), (t["s2"], t["s2s"]))):
        y = 158 + i * 76
        s.box(524, y, 202, 62, "card", 8)
        s.text(536, y + 25, a, size=14, weight=600)
        s.text(536, y + 47, b, "m", 11.5)
    s.arrow(740, 195, 778, 195)
    # candidate menu
    s.box(780, 70, 190, 250)
    s.text(796, 98, t["menu"], "m", 13)
    s.text(954, 98, "sjwl", "m", 13, anchor="end", mono=True)
    s.line(792, 110, 958, 110)
    for i, c in enumerate(MENU):
        y = 136 + i * 30
        if i == 0:
            s.box(788, y - 21, 174, 30, "accb", 6)
        cls = "on" if i == 0 else "t"
        s.text(800, y, f"{i + 1}", cls if i == 0 else "m", 14, mono=True)
        s.text(822, y, c, cls, 16, weight=600 if i == 0 else 400)
    s.text(954, 136, t["from_beam"], "on", 11, anchor="end")
    s.line(792, 278, 958, 278)
    s.text(800, 302, t["rime_more"], "rimet", 13)
    # context note
    s.text(30, 360, t["ctx_note"], "m", 14)
    s.text(30, 384, t["ctx_a"], "acct", 14, weight=600)
    s.text(500, 384, t["ctx_b"], "acct", 14, weight=600)
    # runtime lane
    s.line(30, 408, 970, 408)
    s.text(30, 436, t["run"], "m", 13, weight=600)
    boxes = [(30, "f1", "f1s", "card"), (260, "f2", "f2s", "card"), (500, "f3", "f3s", "card"), (740, "f4", "f4s", "acc")]
    for x, a, b, cls in boxes:
        s.box(x, 448, 210 if x < 740 else 230, 56, cls, 8)
        s.text(x + 14, 471, t[a], "acct" if cls == "acc" else "t", 14, weight=600)
        s.text(x + 14, 492, t[b], "m", 12, mono=b in ("f2s", "f3s"))
    s.arrow(240, 476, 258, 476)
    s.arrow(470, 476, 498, 476, both=True)
    s.arrow(710, 476, 738, 476, both=True)
    s.box(260, 514, 210, 44, "rime", 8)
    s.text(274, 541, f"{t['f5']} · {t['f5s']}", "rimet", 12.5, weight=600)
    s.parts.append('<path class="ln" d="M135 504L135 536L258 536" stroke-width="1.6" marker-end="url(#a)"/>')
    s.text(500, 541, t["fallback"], "m", 12)
    return s


def comparison(t: dict) -> Svg:
    s = Svg(1000, 560, t["cmp_title"])
    s.text(30, 44, t["cmp_title"], size=22, weight=700)
    cols = [(25, "c1", "rime", "rimet"), (350, "c2", "cloud", "cloudt"), (675, "c3", "acc", "acct")]
    for x, k, cls, tc in cols:
        s.box(x, 66, 300, 230, cls, 12)
        s.text(x + 16, 94, t[k], tc, 18, weight=700)
        s.text(x + 16, 114, t[k + "s"], "m", 12)
        for i, step in enumerate("abc"):
            y = 128 + i * 44
            s.box(x + 16, y, 268, 32, "card", 7)
            s.text(x + 150, y + 21, t[k + step], "t", 13, anchor="middle")
            if i < 2:
                s.arrow(x + 150, y + 32, x + 150, y + 43)
        s.text(x + 16, 280, t[k + "x"], tc, 12, weight=600)
    # measured examples
    top = 322
    s.box(25, top, 950, 182)
    xs = (45, 345, 665)
    for x, k, cls in zip(xs, ("tk", "tr", "tb"), ("m", "rimet", "acct")):
        s.text(x, top + 28, t[k], cls, 13, weight=600)
    s.line(37, top + 40, 963, top + 40)
    for i, (keys, ctx, rime, beam) in enumerate(EXAMPLES):
        y = top + 70 + i * 32
        s.text(xs[0], y, keys, "t", 16, weight=600, mono=True)
        if ctx:
            s.text(xs[0] + 12 + 10 * len(keys), y, t[ctx], "m", 12)
        s.text(xs[1], y, rime, "bad" if i < 3 else "t", 16)
        s.text(xs[2], y, beam, "acct", 16, weight=700)
    s.text(25, 530, t["foot"], "m", 11.5)
    return s


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for lang, t in L.items():
        suffix = "" if lang == "zh" else ".en"
        how_it_works(t).save(OUT / f"how-it-works{suffix}.svg")
        comparison(t).save(OUT / f"comparison{suffix}.svg")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
