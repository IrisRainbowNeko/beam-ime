#!/usr/bin/env python3
"""Learn invented terms and phrase patterns, then test held-out codes and sentences.

Each exposure is a confirmed user utterance, not an optimizer step. All-linear
arms adapt attention and MLP projections, leaving embeddings and output frozen.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import time
from functools import lru_cache
from pathlib import Path

from pypinyin import Style, lazy_pinyin

from keys_llm_format import HAN_PATTERN, letters, readings
from personalization_experiment import collate, context_similarity, encode, generate, optimize, teacher_cache, write_json


PROFILES = {
    "technical": {
        "terms": ["星澜引擎", "霁川网关", "雾桥框架", "青屿协议"],
        "conflicts": ["心理预期", "检查外挂", "完全可见", "企业信用"],
        "conflict_contexts": ["大家对这次涨薪的", "这个游戏客户端先做一遍", "窗口展开以后内容已经", "贷款审核主要参考"],
        "introduce": "我们把新开发的组件命名为", "topic": "接下来继续处理开发环境",
        "train_frames": ["启动{}", "升级{}", "{}准备好了", "记录{}的状态", "暂停{}", "重启{}"],
        "test_frames": ["检查{}的日志", "给{}增加缓存", "{}需要重新配置", "今天开始测试{}"],
        "objects": ["日志文件", "缓存服务", "配置文件", "测试代码"],
        "places": ["临时目录", "测试环境", "备份目录", "本地仓库"],
        "new_objects": ["代理节点", "监控面板"], "new_places": ["生产环境", "共享空间"],
        "actions_a": ["查日志", "看监控", "找原因", "对接口"],
        "actions_b": ["改配置", "调参数", "修代码", "跑测试"],
        "new_a": ["检查依赖", "核对版本"], "new_b": ["调整开关", "更新组件"],
    },
    "professional": {
        "terms": ["晴墨计划", "岚镜工单", "星汐看板", "云禾档案"],
        "conflicts": ["全面进化", "了解更多", "详细看吧", "用户档案"],
        "conflict_contexts": ["这次升级让产品实现了", "点击这个按钮可以", "这份资料你再", "客服正在核对这位客户的"],
        "introduce": "团队把新的内部流程称为", "topic": "继续整理今天的工作记录",
        "train_frames": ["更新{}", "打开{}", "{}已经完成", "整理{}的记录", "关闭{}", "提交{}"],
        "test_frames": ["核对{}的进度", "把{}交给同事", "{}需要重新整理", "明天继续跟进{}"],
        "objects": ["客户资料", "会议记录", "合同附件", "报销单据"],
        "places": ["项目文档", "共享目录", "本周计划", "工作清单"],
        "new_objects": ["采购申请", "预算明细"], "new_places": ["归档区域", "审批队列"],
        "actions_a": ["查资料", "看需求", "问同事", "对账目"],
        "actions_b": ["写报告", "做方案", "发邮件", "提申请"],
        "new_a": ["核实数量", "确认时间"], "new_b": ["安排会议", "整理材料"],
    },
    "everyday": {
        "terms": ["栖禾咖啡", "沐星书屋", "青岚画室", "月汐小馆"],
        "conflicts": ["切换客服", "明显失误", "起来喝水", "游戏相关"],
        "conflict_contexts": ["这个问题需要换个人处理我先", "刚才的操作存在一个", "坐了太久应该", "这个论坛主要讨论的内容是"],
        "introduce": "我们给常去的新店起名叫", "topic": "继续安排这周的日常活动",
        "train_frames": ["去{}", "找到{}", "{}已经开门", "记录{}的位置", "离开{}", "路过{}"],
        "test_frames": ["约朋友去{}", "{}今天营业吗", "我在{}门口等你", "下周再去一次{}"],
        "objects": ["新买的书", "旅行照片", "购物清单", "旧的衣服"],
        "places": ["卧室柜子", "客厅书架", "桌面文件", "周末计划"],
        "new_objects": ["手写笔记", "露营用品"], "new_places": ["储物箱子", "阳台角落"],
        "actions_a": ["买东西", "看天气", "查路线", "问朋友"],
        "actions_b": ["出门玩", "定时间", "订车票", "做准备"],
        "new_a": ["检查行李", "联系司机"], "new_b": ["收拾房间", "出发回家"],
    },
}
LEVELS = [1, 3, 10, 30]
ARMS = {"qv_plain8": (["q_proj", "v_proj"], 8, False),
        "qv_aug8": (["q_proj", "v_proj"], 8, True),
        "all_aug8": ("all-linear", 8, True),
        "all_aug16": ("all-linear", 16, True),
        "all_aug8_anchors": ("all-linear", 8, True)}


@lru_cache(maxsize=32768)
def syllables(text):
    return tuple(lazy_pinyin(text, style=Style.NORMAL, errors="raise"))


def keys_for(text, mode):
    values = syllables(text)
    if mode == "full":
        return "".join(values)
    if mode == "initial":
        return "".join(s[0] for s in values)
    offset = 0 if mode == "mixed0" else 1
    return "".join(s if (i + offset) % 3 == 0 else s[:2] if (i + offset) % 3 == 1 else s[0]
                   for i, s in enumerate(values))


def row(target, context, mode="full", **extra):
    keys = keys_for(target, mode)
    if len(keys) > 40:
        raise ValueError(f"Input exceeds Beam's 40-letter limit: {target}")
    return {"target": target, "context": context, "keys": keys, "variant": mode, **extra}


def curriculum(profile):
    result = []
    for exposure in range(1, 31):
        for index, term in enumerate(profile["terms"]):
            target = term if exposure == 1 else profile["train_frames"][(exposure - 2) % 6].format(term)
            context = profile["introduce"] if exposure == 1 else profile["topic"]
            result.append(row(target, context, exposure=exposure, concept=f"term{index}", term=term))
        for pattern in range(2):
            a = (exposure - 1) % 4
            b = ((exposure - 1) // 4) % 4
            target = (f"把{profile['objects'][a]}归到{profile['places'][b]}" if pattern == 0
                      else f"先{profile['actions_a'][a]}再{profile['actions_b'][b]}")
            result.append(row(target, profile["topic"], exposure=exposure, concept=f"pattern{pattern}", term=""))
    return result


def probes(profile):
    result = []
    for index, term in enumerate(profile["terms"]):
        result.append(row(term, profile["introduce"], group="term_seen", term=term))
        for mode in ("mixed0", "mixed1"):
            result.append(row(term, profile["introduce"], mode, group="key_transfer", term=term))
        for context in ("你刚刚提到的那个名字是什么", "接下来的安排仍然围绕这个名称"):
            result.append(row(term, context, "initial", group="context_transfer", term=term))
        for index2, frame in enumerate(profile["test_frames"]):
            result.append(row(frame.format(term), profile["topic"], "initial" if index2 % 2 == 0 else "mixed1",
                              group="sentence_transfer", term=term))
        conflict = profile["conflicts"][index]
        if keys_for(conflict, "initial") != keys_for(term, "initial"):
            raise ValueError(f"Initial-code conflict is not valid: {term} / {conflict}")
        result.append(row(conflict, profile["conflict_contexts"][index], "initial", group="conflict", term=""))
    for pattern in range(2):
        original = (f"把{profile['objects'][0]}归到{profile['places'][0]}" if pattern == 0
                    else f"先{profile['actions_a'][0]}再{profile['actions_b'][0]}")
        result.append(row(original, profile["topic"], group="phrase_seen", term=""))
        result.append(row(original, profile["topic"], "mixed1", group="phrase_keys", term=""))
        for a in range(2):
            for b in range(2):
                target = (f"把{profile['new_objects'][a]}归到{profile['new_places'][b]}" if pattern == 0
                          else f"先{profile['new_a'][a]}再{profile['new_b'][b]}")
                result.append(row(target, profile["topic"], "initial", group="template_transfer", term=""))
    return result


@lru_cache(maxsize=65536)
def align(text, keys):
    if not HAN_PATTERN.fullmatch(text):
        return None

    @lru_cache(maxsize=None)
    def visit(index, position):
        if index == len(text):
            return (position,) if position == len(keys) else None
        for end in range(position + 1, min(len(keys), position + 6) + 1):
            part = keys[position:end]
            if any(value.startswith(part) for value in readings(text[index])):
                rest = visit(index + 1, end)
                if rest:
                    return (position,) + rest
        return None

    return visit(0, 0)


class CompositionalMemory:
    def __init__(self):
        self.entries = {}

    def learn(self, event):
        text = event["target"]
        for entry, record in self.entries.items():
            if entry in text:
                record["count"] += 1
                record["contexts"] = (record["contexts"] + [event["context"]])[-8:]
        if text not in self.entries:
            self.entries[text] = {"count": 1, "contexts": [event["context"]]}

    def bonus(self, candidate, context):
        values = []
        for text, record in self.entries.items():
            if text in candidate:
                similarity = max(context_similarity(context, c) for c in record["contexts"])
                values.append(min(2.0, math.log1p(record["count"])) * len(text) / len(candidate) * (0.5 + 0.5 * similarity))
        return max(values, default=0.0)

    def candidates(self, query, model_candidates):
        # The query's target and evaluation group are intentionally never read.
        keys = letters(query["keys"])
        values = list(dict.fromkeys(model_candidates))
        extra = []
        for text in self.entries:
            if align(text, keys) is not None:
                extra.append(text)
        for candidate in model_candidates:
            boundaries = align(candidate, keys)
            if boundaries is None:
                continue
            for text in self.entries:
                for start in range(len(candidate) - len(text) + 1):
                    end = start + len(text)
                    if candidate[start:end] != text and align(text, keys[boundaries[start]:boundaries[end]]) is not None:
                        extra.append(candidate[:start] + text + candidate[end:])
        extra = sorted(set(extra) - set(values), key=lambda c: self.bonus(c, query.get("context", "")), reverse=True)
        return values + extra[:5]


def protect_personal_candidate(memory, query, generated, ranked):
    personal = [c for c in memory.candidates(query, generated) if memory.bonus(c, query.get("context", "")) > 0]
    kept = list(ranked[:5])
    if personal:
        preferred = max(personal, key=lambda c: memory.bonus(c, query.get("context", "")))
        if preferred not in kept:
            kept = kept[:4] + [preferred]
    return kept


def score_candidates(model, tokenizer, queries, candidates):
    import torch
    flattened, locations = [], []
    for i, (query, options) in enumerate(zip(queries, candidates, strict=True)):
        for candidate in options:
            flattened.append(encode(tokenizer, {**query, "target": candidate}))
            locations.append(i)
    scores = [[] for _ in queries]
    model.eval()
    with torch.no_grad():
        for start in range(0, len(flattened), 16):
            batch = collate(flattened[start:start + 16], tokenizer, model.device)
            logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits[:, :-1]
            labels = batch["labels"][:, 1:]
            for offset in range(len(logits)):
                valid = labels[offset] != -100
                loss = torch.nn.functional.cross_entropy(logits[offset][valid].float(), labels[offset][valid])
                scores[locations[start + offset]].append(-float(loss))
    return scores


def evaluate(model, tokenizer, queries, generated, memory):
    composed = [memory.candidates(query, options) for query, options in zip(queries, generated, strict=True)]
    active = [i for i, (a, b) in enumerate(zip(composed, generated))
              if a != b or any(memory.bonus(c, queries[i].get("context", "")) > 0 for c in a)]
    ranks = [list(options) for options in generated]
    scores = score_candidates(model, tokenizer, [queries[i] for i in active], [composed[i] for i in active])
    for index, values in zip(active, scores):
        ranks[index] = sorted(composed[index], key=lambda text: values[composed[index].index(text)]
                              + memory.bonus(text, queries[index].get("context", "")), reverse=True)[:5]
    cases = []
    for query, original, combined, recalled in zip(queries, generated, ranks, composed, strict=True):
        cases.append({**query, "model_top": original, "combined_top": combined,
                      "dictionary_union_recall": query["target"] in recalled})
    return cases


def summarize_cases(cases):
    groups = collections.defaultdict(list)
    for case in cases:
        groups[case["group"]].append(case)
    result = {}
    for name, values in groups.items():
        entry = {"rows": len(values)}
        for mode in ("model", "combined"):
            for k in (1, 5):
                entry[f"{mode}_top{k}"] = sum(r["target"] in r[f"{mode}_top"][:k] for r in values) / len(values)
            applicable = [r for r in values if r.get("term")]
            if applicable:
                entry[f"{mode}_term_top1"] = sum(any(r["term"] in c for c in r[f"{mode}_top"][:1]) for r in applicable) / len(applicable)
        entry["dictionary_union_recall"] = sum(r["dictionary_union_recall"] for r in values) / len(values)
        result[name] = entry
    return result


def prepare(args):
    old = json.loads(Path(args.previous_data).read_text(encoding="utf-8"))
    profiles = {}
    for name, description in PROFILES.items():
        train, test = curriculum(description), probes(description)
        trained = {r["target"] for r in train}
        for item in test:
            if item["group"] in ("sentence_transfer", "template_transfer") and item["target"] in trained:
                raise ValueError("A transfer target overlaps training")
        profiles[name] = {"train": train, "test": test}
    generic = [{**r, "group": "generic", "term": ""} for r in old["generic"][:64]]
    value = {"kind": "invented-term-and-template-transfer-v1", "levels": LEVELS, "profiles": profiles,
             "generic": generic, "replay": old["replay"], "distractors": old["bank"]["other"][:128],
             "conditions": "No test sentence or held-out code is used as a training example. Augmentation uses only full and initial codes."}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "data.json", value)
    print(json.dumps({name: {"train_utterances": len(p["train"]), "test": dict(collections.Counter(r["group"] for r in p["test"]))}
                      for name, p in profiles.items()}, ensure_ascii=False), flush=True)


def training_examples(tokenizer, rows, augmented):
    values = []
    for event in rows:
        values.append(encode(tokenizer, event))
        if augmented:
            values.append(encode(tokenizer, {**event, "keys": keys_for(event["target"], "initial")}))
    return values


def run(args):
    import torch
    import transformers
    import peft
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(8)
    torch.cuda.set_per_process_memory_fraction(0.22)
    data_path, output = Path(args.data), Path(args.output)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint, padding_side="left")
    tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(args.checkpoint, dtype=torch.bfloat16, attn_implementation="sdpa").to("cuda").eval()
    for parameter in base.parameters():
        parameter.requires_grad_(False)
    metadata = {"args": vars(args), "torch": torch.__version__, "transformers": transformers.__version__,
                "peft": peft.__version__, "gpu": torch.cuda.get_device_name(),
                "data_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(output / "environment.json", metadata)
    base_generic = generate(base, tokenizer, data["generic"])
    baseline = {}
    for name, profile in data["profiles"].items():
        queries = profile["test"] + data["generic"]
        generated = generate(base, tokenizer, profile["test"]) + base_generic
        memory = CompositionalMemory()
        previous = 0
        for level in LEVELS:
            for event in profile["train"]:
                if previous < event["exposure"] <= level:
                    memory.learn(event)
            cases = evaluate(base, tokenizer, queries, generated, memory)
            result = {"arm": "dictionary", "profile": name, "seed": None, "level": level,
                      "training": {}, "metrics": summarize_cases(cases), "cases": cases}
            write_json(output / f"dictionary-{name}-{level}.json", result)
            print(json.dumps({k: v for k, v in result.items() if k != "cases"}), flush=True)
            previous = level
        baseline[name] = generated
    replay = [encode(tokenizer, r) for r in data["replay"]]
    teacher_cache(base, tokenizer, replay)
    anchored_replay = {}
    if "all_aug8_anchors" in args.arms:
        # Preserve the frozen model on the same ambiguous codes in unrelated,
        # public contexts. No conflict-test target or test context is read here.
        contexts = [r["context"] for r in data["replay"] if len(r.get("context", "")) >= 8][:12]
        for name, profile in data["profiles"].items():
            terms = [r["target"] for r in profile["train"] if r["exposure"] == 1 and r.get("term")]
            queries = [{"keys": keys_for(term, "initial"), "context": context, "target": ""}
                       for term in terms for context in contexts]
            predicted = generate(base, tokenizer, queries)
            anchors = []
            for query, candidates in zip(queries, predicted, strict=True):
                valid = [c for c in candidates if align(c, query["keys"]) is not None and c not in terms]
                if valid:
                    anchors.append({**query, "target": valid[0]})
            if len(anchors) < 16:
                raise ValueError(f"Insufficient phonetic collision anchors for {name}: {len(anchors)}")
            encoded = [encode(tokenizer, r) for r in anchors]
            teacher_cache(base, tokenizer, encoded)
            anchored_replay[name] = replay + encoded * 6
            write_json(output / f"collision-anchors-{name}.json", anchors)
            print(json.dumps({"collision_anchors": name, "rows": len(anchors)}), flush=True)
    for seed in args.seeds:
        for name, profile in data["profiles"].items():
            queries = profile["test"] + data["generic"]
            for arm in args.arms:
                if (output / f"{arm}-{name}-{seed}-retention.json").exists():
                    continue
                targets, rank, augmented = ARMS[arm]
                torch.manual_seed(seed)
                model = get_peft_model(base, LoraConfig(r=rank, lora_alpha=2 * rank, target_modules=targets,
                                                      lora_dropout=0.0, bias="none", task_type="CAUSAL_LM"))
                optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=args.learning_rate)
                memory, previous, history = CompositionalMemory(), 0, []
                references = anchored_replay[name] if arm == "all_aug8_anchors" else replay
                for level in LEVELS:
                    events = [r for r in profile["train"] if previous < r["exposure"] <= level]
                    encoded = training_examples(tokenizer, events, augmented)
                    training = optimize(model, tokenizer, encoded, history, references, optimizer, args.steps, seed + level)
                    history.extend(encoded)
                    for event in events:
                        memory.learn(event)
                    generated = generate(model, tokenizer, queries)
                    cases = evaluate(model, tokenizer, queries, generated, memory)
                    result = {"arm": arm, "profile": name, "seed": seed, "level": level,
                              "utterances": sum(r["exposure"] <= level for r in profile["train"]),
                              "training": training, "metrics": summarize_cases(cases), "cases": cases}
                    write_json(output / f"{arm}-{name}-{seed}-{level}.json", result)
                    print(json.dumps({k: v for k, v in result.items() if k != "cases"}), flush=True)
                    previous = level
                model.save_pretrained(output / f"adapter-{arm}-{name}-{seed}")
                # A second topic supplies new input while old user examples remain in replay.
                distractors = [encode(tokenizer, r) for r in data["distractors"]]
                timing = optimize(model, tokenizer, distractors, history, references, optimizer, args.retention_steps, seed + 1000)
                generated = generate(model, tokenizer, queries)
                cases = evaluate(model, tokenizer, queries, generated, memory)
                result = {"arm": arm, "profile": name, "seed": seed, "level": "retention", "training": timing,
                          "metrics": summarize_cases(cases), "cases": cases}
                write_json(output / f"{arm}-{name}-{seed}-retention.json", result)
                print(json.dumps({k: v for k, v in result.items() if k != "cases"}), flush=True)
                base = model.unload()
                del optimizer, model
                torch.cuda.empty_cache()


def summarize(args):
    output = Path(args.output)
    groups = collections.defaultdict(list)
    for path in output.glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if "arm" in value:
            groups[value["arm"], str(value["level"])].append(value)
    result = {}
    for (arm, level), values in groups.items():
        cases = [r for value in values for r in value["cases"]]
        result.setdefault(arm, {})[level] = {"runs": len(values), "metrics": summarize_cases(cases),
                                            "mean_update_seconds": sum(v["training"].get("seconds", 0) for v in values) / len(values),
                                            "peak_train_mib": max(v["training"].get("peak_allocated_mib", 0) for v in values),
                                            "trainable_parameters": max(v["training"].get("trainable_parameters", 0) for v in values)}
    write_json(output / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cpu_benchmark(args):
    import resource
    import torch
    import transformers
    import peft
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(args.threads)
    torch.manual_seed(11)
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint, extra_special_tokens={})
    tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(args.checkpoint, torch_dtype=torch.bfloat16,
                                               attn_implementation="sdpa")
    model = get_peft_model(model, LoraConfig(r=8, lora_alpha=16, target_modules="all-linear",
                                           lora_dropout=0.0, bias="none", task_type="CAUSAL_LM"))
    optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=2e-4)
    examples = [encode(tokenizer, r) for r in data["profiles"]["technical"]["train"][:6]]
    rng = random.Random(11)
    references = []
    teacher_seconds = 0.0
    if args.full_recipe:
        events = data["profiles"]["technical"]["train"]
        recent = training_examples(tokenizer, [r for r in events if 3 < r["exposure"] <= 10], True)
        older = training_examples(tokenizer, [r for r in events if r["exposure"] <= 3], True)
        references = [encode(tokenizer, r) for r in rng.sample(data["replay"], 24)]
        started = time.perf_counter()
        for reference in references:
            teacher_cache(model, tokenizer, [reference])
        teacher_seconds = time.perf_counter() - started
    model.train()
    model.config.use_cache = False
    measurements = []
    for step in range(args.steps + 1):
        optimizer.zero_grad(set_to_none=True)
        started = time.perf_counter()
        if args.full_recipe:
            personal = rng.choices(recent, k=8) + rng.choices(older, k=4)
            selected_references = rng.sample(references, 4)
            target_tokens = sum(sum(label != -100 for label in example["labels"][1:]) for example in personal)
            loss_value, tokens = 0.0, 0
            # Accumulation keeps the same loss weights as the GPU batch-16 recipe.
            for offset, example in enumerate(personal + selected_references):
                batch = collate([example], tokenizer, "cpu")
                with torch.autocast("cpu", dtype=torch.bfloat16):
                    logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits[0, :-1]
                labels = batch["labels"][0, 1:]
                valid = labels != -100
                if offset < 12:
                    loss = torch.nn.functional.cross_entropy(logits[valid].float(), labels[valid], reduction="sum") / target_tokens
                else:
                    log_probs = logits[valid].float().log_softmax(-1)
                    selected = log_probs.gather(-1, example["teacher_ids"]).exp()
                    student = torch.cat((selected, (1 - selected.sum(-1, keepdim=True)).clamp_min(1e-8)), -1)
                    teacher = example["teacher_probs"]
                    kl = (teacher * (teacher.clamp_min(1e-8).log() - student.clamp_min(1e-8).log())).sum(-1).mean()
                    loss = (0.25 * torch.nn.functional.nll_loss(log_probs, labels[valid]) + kl) / 4
                loss.backward()
                loss_value += float(loss.detach())
                tokens += int(batch["attention_mask"].sum())
        else:
            batch = collate([examples[step % len(examples)]], tokenizer, "cpu")
            with torch.autocast("cpu", dtype=torch.bfloat16):
                loss = model(**batch).loss
            loss.backward()
            loss_value = float(loss.detach())
            tokens = int(batch["attention_mask"].sum())
        torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.0)
        optimizer.step()
        record = {"step": step, "seconds": time.perf_counter() - started,
                  "tokens": tokens, "loss": loss_value,
                  "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
        print(json.dumps(record), flush=True)
        if step:
            measurements.append(record)
    result = {"kind": "cpu-microbatch-training-benchmark", "torch": torch.__version__,
              "transformers": transformers.__version__, "peft": peft.__version__,
              "threads": args.threads, "microbatch": 1, "warmup_steps": 1,
              "effective_batch": 16 if args.full_recipe else 1,
              "teacher_cache_seconds": teacher_seconds, "teacher_cache_rows": len(references),
              "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad),
              "steps": measurements, "mean_step_seconds": sum(x["seconds"] for x in measurements) / len(measurements),
              "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
              "scope": ("BF16 all-linear rank-8; personal CE + 0.25 replay CE + teacher KL; evaluation excluded."
                        if args.full_recipe else "BF16 all-linear rank-8 supervised update; teacher caching and evaluation excluded.")}
    write_json(args.output, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--previous-data", required=True)
    prepare_parser.add_argument("--output", required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--checkpoint", required=True)
    run_parser.add_argument("--data", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--seeds", type=int, nargs="+", default=[11, 37])
    run_parser.add_argument("--arms", nargs="+", choices=list(ARMS), default=list(ARMS))
    run_parser.add_argument("--steps", type=int, default=24)
    run_parser.add_argument("--retention-steps", type=int, default=96)
    run_parser.add_argument("--learning-rate", type=float, default=2e-4)
    summary_parser = commands.add_parser("summarize")
    summary_parser.add_argument("--output", required=True)
    cpu_parser = commands.add_parser("cpu-benchmark")
    cpu_parser.add_argument("--checkpoint", required=True)
    cpu_parser.add_argument("--data", required=True)
    cpu_parser.add_argument("--output", required=True)
    cpu_parser.add_argument("--threads", type=int, default=4)
    cpu_parser.add_argument("--steps", type=int, default=6)
    cpu_parser.add_argument("--full-recipe", action="store_true")
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "summarize": summarize, "cpu-benchmark": cpu_benchmark}[args.command](args)


if __name__ == "__main__":
    main()
