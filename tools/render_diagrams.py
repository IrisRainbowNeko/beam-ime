#!/usr/bin/env python3
"""Render the README figure (docs/images/overview*.svg) in Chinese and English.

    python3 tools/render_diagrams.py

One figure: the same keys go through a classic dictionary IME and through Beam, followed by
the effect of context, more examples and the runtime layout. All candidate lists are measured,
not invented: Beam from the 0.1.0-beta.2 release model (beam-0.6b-q8_0.gguf, beam_ms 1000),
Rime Ice from a fresh rime_ice schema with an empty user dictionary. Re-measure before editing.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"

FONT = "'Noto Sans CJK SC','Source Han Sans SC','PingFang SC','Microsoft YaHei',sans-serif"
MONO = "'JetBrains Mono','Cascadia Mono','DejaVu Sans Mono',monospace"

STYLE = f"""
  .bg{{fill:url(#bg)}} .card{{fill:#ffffff;stroke:#d8dee6}} .t{{fill:#1f2328}} .m{{fill:#5d6772}}
  .lane-r{{fill:#f2f7ef;stroke:#bcd6b0}} .lane-b{{fill:#eef2ff;stroke:#b9c5fb}}
  .pill-r{{fill:#4f8a3a}} .pill-b{{fill:url(#pb)}} .on{{fill:#ffffff}}
  .rt{{fill:#447a31}} .bt{{fill:#3a52d6}} .bad{{fill:#c0362c}}
  .llm{{fill:url(#llm);stroke:#7d8ff5}} .inner{{fill:#ffffff;fill-opacity:.82;stroke:#c9d2fb}}
  .hl-r{{fill:#e4ecdf}} .hl-b{{fill:url(#pb)}} .badge-r{{fill:#4f8a3a}} .badge-b{{fill:#4f6bed}}
  .ln{{stroke:#9aa4ae;fill:none}} .ah{{fill:#9aa4ae}} .lnb{{stroke:#6b82f0;fill:none}} .ahb{{fill:#6b82f0}}
  .sep{{stroke:#e1e6ec}} .chip-r{{fill:#e4ecdf}} .chip-b{{fill:#dfe6fe}}
  text{{font-family:{FONT}}} .mono{{font-family:{MONO}}}
  @media (prefers-color-scheme: dark) {{
    .bg{{fill:#0d1117}} .card{{fill:#161b22;stroke:#30363d}} .t{{fill:#e6edf3}} .m{{fill:#9198a1}}
    .lane-r{{fill:#111a10;stroke:#2f4a28}} .lane-b{{fill:#10152b;stroke:#33408a}}
    .pill-r{{fill:#3f7330}} .rt{{fill:#8cc477}} .bt{{fill:#9aabff}} .bad{{fill:#ff7b72}}
    .llm{{fill:#18213f;stroke:#5b6fd6}} .inner{{fill:#0f1530;fill-opacity:1;stroke:#33408a}}
    .hl-r{{fill:#1d2b1a}} .sep{{stroke:#30363d}} .chip-r{{fill:#1d2b1a}} .chip-b{{fill:#1b2347}}
    .ln{{stroke:#6e7681}} .ah{{fill:#6e7681}} .lnb{{stroke:#7d93ff}} .ahb{{fill:#7d93ff}}
  }}
"""

DEFS = """<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fbfcfe"/><stop offset="1" stop-color="#f3f5f9"/></linearGradient>
  <linearGradient id="pb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5b74f0"/><stop offset="1" stop-color="#8a5cf0"/></linearGradient>
  <linearGradient id="llm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e9eeff"/><stop offset="1" stop-color="#f1e9ff"/></linearGradient>
  <filter id="sh" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="1.5" stdDeviation="2.2" flood-color="#1f2a44" flood-opacity=".10"/></filter>
  <marker id="a" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ah"/></marker>
  <marker id="ab" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ahb"/></marker>
</defs>"""


class Svg:
    def __init__(self, w: int, h: int, title: str) -> None:
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{escape(title)}">',
            f"<title>{escape(title)}</title>", f"<style>{STYLE}</style>", DEFS,
            f'<rect class="bg" width="{w}" height="{h}" rx="16"/>',
        ]

    def box(self, x, y, w, h, cls="card", rx=12, shadow=False):
        f = ' filter="url(#sh)"' if shadow else ""
        self.parts.append(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" stroke-width="1.2"{f}/>')

    def text(self, x, y, s, cls="t", size=15, anchor="start", weight=400, mono=False, spacing=0):
        c = cls + (" mono" if mono else "")
        ls = f' letter-spacing="{spacing}"' if spacing else ""
        self.parts.append(f'<text class="{c}" x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" '
                          f'font-weight="{weight}"{ls}>{escape(s)}</text>')

    def path(self, d, blue=False, both=False, width=1.6, dash=False):
        cls, m = ("lnb", "ab") if blue else ("ln", "a")
        extra = f' marker-start="url(#{m})"' if both else ""
        da = ' stroke-dasharray="4 4"' if dash else ""
        self.parts.append(f'<path class="{cls}" d="{d}" stroke-width="{width}" marker-end="url(#{m})"{extra}{da}/>')

    def line(self, x1, y1, x2, y2):
        self.parts.append(f'<path class="sep" d="M{x1} {y1}L{x2} {y2}" stroke-width="1"/>')

    def pill(self, x, y, s, cls, size=13, pad=12, char_w=None):
        w = pad * 2 + text_width(s, size)
        self.box(x, y, w, size + 13, cls, (size + 13) / 2)
        self.text(x + pad, y + size + 3, s, "on", size, weight=700)
        return w

    def badge(self, cx, cy, n, cls):
        self.parts.append(f'<circle class="{cls}" cx="{cx}" cy="{cy}" r="10"/>')
        self.text(cx, cy + 4.5, str(n), "on", 12, anchor="middle", weight=700)

    def save(self, path: Path) -> None:
        path.write_text("\n".join(self.parts + ["</svg>"]) + "\n", encoding="utf-8")


def text_width(s: str, size: float) -> float:
    return sum(size if ord(c) > 0x2E80 else size * (0.66 if c.isupper() else 0.56) for c in s)


L = {
    "zh": {
        "title": "Beam：几个字母，一句话",
        "sub": "同一组按键，分别交给传统拼音输入法和 Beam",
        "ctx": "上文（已上屏）", "keys": "按键",
        "lane_r": "传统拼音输入法 · 雾凇拼音", "lane_b": "Beam 输入法 · Beam-LLM",
        "r1": "音节切分", "r1s": "每个首字母都展开成音节",
        "r2": "查词典", "r2s": "唯美 · 清纯 · 发布 …",
        "r3": "按词频拼接", "r3s": "词库里有什么，就拼什么",
        "rchip": "本机 · 即时 · 不看上文 · 长句靠词库收录",
        "prompt": "Prompt", "p1": "上文：下班了", "p2": "按键：w m q c f b", "p3": "结果：",
        "llm": "Beam-LLM 0.6B", "llm_s": "Qwen3-0.6B 微调 · Q8_0 · llama.cpp · Vulkan / CPU",
        "b1": "增量首选", "b1s": "KV 复用 + 上次结果作草稿",
        "b2": "限时 beam search", "b2s": "5 束 · 每键 100 ms 预算",
        "bchip": "本机 · GPU 可选 · 780M 上约 50–150 ms · 读最近 64 字上文",
        "menu": "候选", "rime_more": "6+ 雾凇候选",
        "pa": "上文决定首选", "pa_keys": "同样输入 sjwl",
        "pa1": "「我在学深度学习」", "pa1r": "神经网络", "pa2": "「昨晚又熬夜了」", "pa2r": "睡觉晚了",
        "pb": "更多实测", "pb_h": "雾凇首选 → Beam 首选",
        "pc": "全部在本机运行",
        "c1": "Fcitx5\n小狼毫", "c2": "Rime\n插件", "c3": "beamd\nBeam-LLM",
        "cfall": "超过 400 ms 或服务不可用：这次按键只用雾凇候选",
        "foot": "实测数据：Beam 为 0.1.0-beta.2 发布模型；雾凇拼音为全新用户词库。",
    },
    "en": {
        "title": "Beam: a few letters, a whole sentence",
        "sub": "The same keys, typed into a classic pinyin IME and into Beam",
        "ctx": "Context (committed)", "keys": "Keys",
        "lane_r": "Classic pinyin IME · Rime Ice", "lane_b": "Beam IME · Beam-LLM",
        "r1": "Split syllables", "r1s": "initials → candidate syllables",
        "r2": "Dictionary lookup", "r2s": "唯美 · 清纯 · 发布 …",
        "r3": "Join by frequency", "r3s": "only what the dictionary has",
        "rchip": "local · instant · ignores context · long phrases need entries",
        "prompt": "Prompt", "p1": "上文：下班了", "p2": "按键：w m q c f b", "p3": "结果：",
        "llm": "Beam-LLM 0.6B", "llm_s": "Qwen3-0.6B fine-tune · Q8_0 · llama.cpp · Vulkan / CPU",
        "b1": "Incremental Top-1", "b1s": "KV reuse + last result as draft",
        "b2": "Timed beam search", "b2s": "5 beams · 100 ms per key",
        "bchip": "local · GPU optional · ~50–150 ms on a 780M · reads the last 64 characters",
        "menu": "Candidates", "rime_more": "6+ Rime Ice",
        "pa": "Context picks the answer", "pa_keys": "same keys: sjwl",
        "pa1": "“studying deep learning”", "pa1r": "神经网络", "pa2": "“up late again”", "pa2r": "睡觉晚了",
        "pb": "More examples", "pb_h": "Rime Ice first → Beam first",
        "pc": "Everything runs locally",
        "c1": "Fcitx5\nWeasel", "c2": "Rime\nplugin", "c3": "beamd\nBeam-LLM",
        "cfall": "Over 400 ms or service down: Rime Ice only for that key",
        "foot": "Measured: Beam 0.1.0-beta.2 release model; Rime Ice with a fresh user dictionary.",
    },
}

# Measured candidate lists (see the module docstring).
KEYS, CONTEXT = "wmqcfb", "下班了"
RIME_MENU = ["唯美清纯发布", "唯美清纯", "威马汽车", "唯美青春", "我们青春"]
BEAM_MENU = ["我们去吃饭吧", "我们去吃饭不", "我没去吃饭吧", "我目前吃饭不", "我们去吃饭呗"]
MORE = [("jtwsqcfb", "今天晚上其次伐兵", "今天晚上去吃饭吧"), ("yonglinux", "用力奴性", "用Linux")]


def menu(s: Svg, x, y, items, hl, extra=None, extra_cls="rt"):
    h = 46 + 30 * len(items) + (30 if extra else 0)
    s.box(x, y, 180, h, "card", 12, shadow=True)
    s.text(x + 16, y + 26, s_menu_title, "m", 12)
    s.text(x + 164, y + 26, KEYS, "m", 12, anchor="end", mono=True)
    s.line(x + 12, y + 36, x + 168, y + 36)
    for i, c in enumerate(items):
        yy = y + 62 + i * 30
        if i == 0:
            s.box(x + 8, yy - 20, 164, 28, hl, 7)
        first_cls = "on" if hl == "hl-b" else ("bad" if hl == "hl-r" else "t")
        s.text(x + 22, yy, str(i + 1), first_cls if i == 0 else "m", 13, mono=True)
        s.text(x + 42, yy, c, first_cls if i == 0 else "t", 15.5, weight=700 if i == 0 else 400)
    if extra:
        yy = y + 62 + len(items) * 30
        s.line(x + 12, yy - 20, x + 168, yy - 20)
        s.text(x + 22, yy + 2, extra, extra_cls, 12.5, weight=600)
    return h


s_menu_title = ""


def overview(t: dict) -> Svg:
    global s_menu_title
    s_menu_title = t["menu"]
    W, H = 1200, 880
    s = Svg(W, H, t["title"])
    s.text(44, 56, t["title"], size=28, weight=800)
    s.text(44, 84, t["sub"], "m", 15)

    # lanes
    s.box(290, 112, 870, 236, "lane-r", 16)
    s.box(290, 366, 870, 292, "lane-b", 16)
    s.pill(310, 128, t["lane_r"], "pill-r")
    s.pill(310, 382, t["lane_b"], "pill-b")

    # shared inputs
    s.box(40, 290, 210, 96, "card", 14, shadow=True)
    s.text(58, 316, t["keys"], "m", 12.5)
    s.text(58, 360, " ".join(KEYS), size=27, weight=800, mono=True)
    s.box(40, 410, 210, 86, "card", 14, shadow=True)
    s.text(58, 436, t["ctx"], "m", 12.5)
    s.text(58, 472, CONTEXT, size=22, weight=700)
    # keys feed both lanes; context only reaches Beam
    s.path("M250 326C278 326 270 228 300 228")
    s.path("M250 350C280 350 274 470 300 470", blue=True)
    s.path("M250 453C280 453 276 496 300 496", blue=True)

    # classic lane steps
    steps = [(t["r1"], t["r1s"]), (t["r2"], t["r2s"]), (t["r3"], t["r3s"])]
    for i, (a, b) in enumerate(steps):
        x = 310 + i * 210
        s.box(x, 176, 190, 104, "card", 12, shadow=True)
        s.badge(x + 24, 202, i + 1, "badge-r")
        s.text(x + 42, 207, a, size=15, weight=700)
        s.text(x + 16, 246, b, "m", 12.5 if i != 1 else 13.5)
        if i < 2:
            s.path(f"M{x + 190} 228L{x + 208} 228")
    s.path("M940 228L958 228")
    s.box(310, 296, 20 + text_width(t["rchip"], 12.5), 30, "chip-r", 15)
    s.text(320, 316, t["rchip"], "rt", 12.5, weight=600)
    menu(s, 962, 124, RIME_MENU, "hl-r")

    # Beam lane
    s.box(310, 430, 196, 150, "card", 12, shadow=True)
    s.text(326, 456, t["prompt"], "m", 12.5)
    for i, ln in enumerate((t["p1"], t["p2"], t["p3"])):
        s.text(326, 490 + i * 28, ln, size=15, mono=True)
    s.path("M506 505L528 505", blue=True)
    s.box(530, 422, 410, 166, "llm", 16, shadow=True)
    s.text(552, 456, t["llm"], "bt", 22, weight=800)
    s.text(552, 479, t["llm_s"], "m", 12)
    for i, (a, b) in enumerate(((t["b1"], t["b1s"]), (t["b2"], t["b2s"]))):
        x = 548 + i * 194
        s.box(x, 496, 176, 74, "inner", 10)
        s.badge(x + 22, 520, i + 1, "badge-b")
        s.text(x + 40, 525, a, size=14, weight=700)
        s.text(x + 14, 554, b, "m", 12)
    s.path("M725 533L741 533", blue=True)
    s.path("M940 505L958 505", blue=True)
    s.box(310, 612, 20 + text_width(t["bchip"], 12.5), 30, "chip-b", 15)
    s.text(320, 632, t["bchip"], "bt", 12.5, weight=600)
    menu(s, 962, 376, BEAM_MENU, "hl-b", extra=t["rime_more"])

    # bottom panels
    y0, ph = 682, 160
    # (a) context
    s.box(40, y0, 360, ph, "card", 14, shadow=True)
    s.text(60, y0 + 32, t["pa"], size=16, weight=800)
    s.text(380, y0 + 32, t["pa_keys"], "m", 12, anchor="end")
    for i, (c, r) in enumerate(((t["pa1"], t["pa1r"]), (t["pa2"], t["pa2r"]))):
        yy = y0 + 76 + i * 44
        s.text(60, yy, c, "t", 14)
        s.path(f"M{60 + text_width(c, 14) + 10} {yy - 5}L{60 + text_width(c, 14) + 36} {yy - 5}", blue=True)
        s.box(60 + text_width(c, 14) + 44, yy - 23, 98, 32, "chip-b", 9)
        s.text(60 + text_width(c, 14) + 93, yy - 1, r, "bt", 15.5, anchor="middle", weight=700)
    # (b) more examples
    s.box(420, y0, 360, ph, "card", 14, shadow=True)
    s.text(440, y0 + 32, t["pb"], size=16, weight=800)
    s.text(760, y0 + 32, t["pb_h"], "m", 12, anchor="end")
    for i, (k, r, b) in enumerate(MORE):
        yy = y0 + 70 + i * 48
        s.text(440, yy, k, "m", 13, mono=True)
        s.text(440, yy + 24, r, "bad", 15)
        x2 = 440 + text_width(r, 15) + 10
        s.path(f"M{x2} {yy + 19}L{x2 + 24} {yy + 19}", blue=True)
        s.text(x2 + 32, yy + 24, b, "bt", 15.5, weight=700)
    # (c) runtime
    s.box(800, y0, 360, ph, "card", 14, shadow=True)
    s.text(820, y0 + 32, t["pc"], size=16, weight=800)
    for lab, cls, x, w in ((t["c1"], "card", 820, 100), (t["c2"], "card", 938, 84), (t["c3"], "llm", 1040, 100)):
        s.box(x, y0 + 50, w, 50, cls, 9)
        rows = lab.split("\n")
        for j, row in enumerate(rows):
            s.text(x + w / 2, y0 + 80 + (j - (len(rows) - 1) / 2) * 16, row, "bt" if cls == "llm" else "t", 12,
                   anchor="middle", weight=700)
    s.path(f"M921 {y0 + 75}L937 {y0 + 75}")
    s.path(f"M1023 {y0 + 75}L1039 {y0 + 75}", both=True)
    s.text(820, y0 + 124, t["cfall"], "m", 12)
    s.text(820, y0 + 144, "Unix socket / loopback TCP + token", "m", 11.5, mono=True)

    s.text(44, H - 18, t["foot"], "m", 11.5)
    return s


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for lang, t in L.items():
        overview(t).save(OUT / f"overview{'' if lang == 'zh' else '.en'}.svg")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
