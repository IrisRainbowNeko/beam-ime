#!/usr/bin/env python3
"""Controlled prequential personalization experiment on an existing Beam checkpoint.

Streams are simulated from held-out public text, not chronological user logs.
The shared bank is a mixture-of-LoRA prototype, not a reproduction of LINEUP.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import statistics
import time
from pathlib import Path

from keys_llm_format import build_prompt, letters, normalize_keys, structural_valid


TOPICS = {
    "software": "代码 编程 开发 程序 软件 linux python docker github rust 数据库 接口".split(),
    "hardware": "电脑 手机 显卡 内存 硬盘 苹果 电池 屏幕 主机 键盘 路由器".split(),
    "research": "模型 训练 算法 实验 论文 神经 参数 推理 学习 数据集".split(),
    "work": "工作 公司 项目 会议 工资 同事 客户 产品 业务 面试 上班".split(),
    "daily": "生活 吃饭 旅游 周末 朋友 家里 医院 咖啡 酒店 高铁 房子".split(),
    "school": "学校 学生 老师 考试 作业 课程 大学 毕业 专业 教育".split(),
    "media": "游戏 电影 音乐 小说 动画 视频 电视剧 播放 直播 摄影".split(),
    "other": [],
}
PROFILES = {"technical": ("software", "hardware"), "professional": ("work", "school"),
            "everyday": ("other", "media")}
PREFERENCES = {"technical": ("数据", "模型", "测试"), "professional": ("时间", "梦想", "城市"),
               "everyday": ("手机", "明星", "超市")}


def read_rows(path):
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def signature(row):
    return row["keys"], row.get("context", "")[-64:]


def usable(row):
    return (2 <= len(row["target"]) <= 20 and 2 <= len(letters(row["keys"])) <= 32
            and structural_valid(row["keys"], {"result": row["target"], "segments": None}))


def topic(row):
    text = (row.get("context", "") + row["target"]).lower()
    scores = [(sum(word in text for word in words), name) for name, words in TOPICS.items() if words]
    score, name = max(scores)
    return name if score else "other"


def sample_rows(paths, count, excluded, rng):
    sample, seen, eligible = [], set(), 0
    for path in paths:
        for row in read_rows(path):
            if row["target"] in excluded or row["target"] in seen or not usable(row):
                continue
            seen.add(row["target"])
            eligible += 1
            if len(sample) < count:
                sample.append(row)
            else:
                index = rng.randrange(eligible)
                if index < count:
                    sample[index] = row
    if len(sample) < count:
        raise ValueError(f"Only {len(sample)} distinct usable rows, need {count}")
    return sample


def prepare(args):
    rng = random.Random(61006)
    pools, seen = collections.defaultdict(list), set()
    for row in read_rows(args.personal_test):
        if usable(row) and row["target"] not in seen:
            seen.add(row["target"])
            pools[topic(row)].append(row)
    print(json.dumps({"personal_topic_rows": {k: len(v) for k, v in pools.items()}}), flush=True)
    streams = []
    for seed in args.seeds:
        for profile, domains in PROFILES.items():
            local_rng = random.Random(f"{seed}:{profile}")
            available = {name: list(pools[name]) for name in domains}
            for values in available.values():
                local_rng.shuffle(values)
            history = collections.defaultdict(list)
            events = []
            for block in range(4):
                domain = domains[1] if block == 2 else domains[0]
                for position in range(args.block_size):
                    if position % 8 == 0:
                        k = (position // 8) % 3
                        preferences = PREFERENCES[profile]
                        if block == 2:
                            preferences = PREFERENCES[list(PROFILES)[(list(PROFILES).index(profile) + 1) % 3]]
                        row = {"keys": ("sj", "mx", "cs")[k], "target": preferences[k],
                               "context": "继续记录今天的事情", "variant": "abbreviated",
                               "source": "synthetic-preference", "category": "controlled"}
                    else:
                        repeat = history[domain] and local_rng.random() < 0.35
                        if repeat:
                            row = dict(local_rng.choice(history[domain]))
                        else:
                            if not available[domain]:
                                raise ValueError(f"Insufficient distinct {domain} test rows; reduce --block-size")
                            row = dict(available[domain].pop())
                            history[domain].append(row)
                        row["category"] = "natural"
                    row.update(block=block, index=len(events), domain=domain)
                    row["context"] = row.get("context", "")[-64:]
                    events.append(row)
            streams.append({"seed": seed, "profile": profile, "events": events})
    personal_targets = {r["target"] for s in streams for r in s["events"]}
    generic = []
    for path in args.generic_test:
        generic.extend(sample_rows([path], args.generic_count // len(args.generic_test), personal_targets, rng))
    excluded = personal_targets | {r["target"] for r in generic}
    replay = sample_rows(args.generic_train, args.replay_count, excluded, rng)
    banks, counts, bank_seen = collections.defaultdict(list), collections.Counter(), set()
    for row in read_rows(args.bank_train):
        if not usable(row) or row["target"] in excluded or row["target"] in bank_seen:
            continue
        bank_seen.add(row["target"])
        name = topic(row)
        counts[name] += 1
        if len(banks[name]) < args.bank_count:
            banks[name].append(row)
        else:
            index = rng.randrange(counts[name])
            if index < args.bank_count:
                banks[name][index] = row
    for name in TOPICS:
        if len(banks[name]) < args.bank_count:
            raise ValueError(f"Only {len(banks[name])} bank rows in {name}")
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    experiment = {"kind": "simulated-public-text-stream-v1", "streams": streams, "generic": generic,
                  "replay": replay, "bank": dict(banks), "settings": vars(args),
                  "personal_topic_rows": {k: len(v) for k, v in pools.items()}}
    write_json(out / "data.json", experiment)
    print(json.dumps({"streams": len(streams), "events": sum(len(s["events"]) for s in streams),
                      "generic": len(generic), "replay": len(replay),
                      "bank_rows": {k: len(v) for k, v in banks.items()}}), flush=True)


def context_similarity(a, b):
    if not a or not b:
        return 0.0
    aa = {a[i:i + 2] for i in range(max(1, len(a) - 1))}
    bb = {b[i:i + 2] for i in range(max(1, len(b) - 1))}
    return len(aa & bb) / len(aa | bb)


class PersonalMemory:
    def __init__(self):
        self.records = collections.defaultdict(dict)
        self.weights = [2.0, 1.5, 1.0, 1.0]
        self.initial = list(self.weights)
        self.clock = 0

    def features(self, keys, context, text, rank):
        record = self.records.get(keys, {}).get(text)
        if not record:
            return [1.0 / rank if rank else 0.0, 0.0, 0.0, 0.0]
        strength = record["count"] / (record["count"] + 2.0)
        similarity = max(context_similarity(context, value) for value in record["contexts"])
        return [1.0 / rank if rank else 0.0, strength,
                strength * math.exp(-(self.clock - record["last"]) / 32), strength * similarity]

    def rank(self, row, candidates):
        # No target is read here. All extra candidates come from earlier commits.
        keys = normalize_keys(row["keys"])
        candidates = list(dict.fromkeys(candidates))
        original = {text: i + 1 for i, text in enumerate(candidates)}
        known = self.records.get(keys, {})
        candidates += sorted((text for text in known if text not in original),
                             key=lambda text: known[text]["last"], reverse=True)[:8]
        features = {text: self.features(keys, row.get("context", ""), text, original.get(text, 0))
                    for text in candidates}
        ranked = sorted(candidates, key=lambda text: sum(a * b for a, b in zip(self.weights, features[text])),
                        reverse=True)
        return ranked[:5], features

    def learn(self, row, ranked, features):
        target = row["target"]
        if ranked and ranked[0] != target and target in features:
            difference = [a - b for a, b in zip(features[target], features[ranked[0]])]
            margin = sum(a * b for a, b in zip(self.weights, difference))
            gradient = 1.0 / (1.0 + math.exp(max(-30.0, min(30.0, margin))))
            self.weights = [w + 0.15 * (gradient * d - 0.02 * (w - initial))
                            for w, d, initial in zip(self.weights, difference, self.initial)]
            # Personal feedback must not invert the model order on unrelated keys.
            self.weights[0] = max(0.0, self.weights[0])
        self.clock += 1
        keys = normalize_keys(row["keys"])
        record = self.records[keys].setdefault(target, {"count": 0, "last": 0, "contexts": []})
        record["count"] += 1
        record["last"] = self.clock
        record["contexts"] = (record["contexts"] + [row.get("context", "")])[-4:]


def replay_stream(events, outputs, memory=None, learn=True):
    cases, past = [], set()
    for row, generated in zip(events, outputs, strict=True):
        started = time.perf_counter()
        ranked, features = memory.rank(row, generated) if memory else (generated, {})
        elapsed = 1000 * (time.perf_counter() - started)
        case = {"index": row.get("index"), "block": row.get("block"),
                "category": row.get("category", "generic"), "target": row["target"],
                "keys": row["keys"], "variant": row.get("variant"), "seen": row["target"] in past,
                "model_top": generated[:5], "top": ranked[:5], "ranker_ms": elapsed}
        cases.append(case)
        if memory and learn:
            memory.learn(row, ranked, features)
        past.add(row["target"])
    return cases


def metrics(cases):
    if not cases:
        return {"rows": 0}
    times = sorted(row["ranker_ms"] for row in cases)
    return {"rows": len(cases), "top1": sum(r["top"][:1] == [r["target"]] for r in cases) / len(cases),
            "top5": sum(r["target"] in r["top"] for r in cases) / len(cases),
            "model_top1": sum(r["model_top"][:1] == [r["target"]] for r in cases) / len(cases),
            "ranker_p95_ms": times[min(len(times) - 1, math.ceil(len(times) * 0.95) - 1)]}


def encode(tokenizer, row):
    prompt = build_prompt(row["keys"], row.get("context", "")[-64:]) + "结果："
    prefix = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    target = tokenizer(row["target"], add_special_tokens=False)["input_ids"] + [tokenizer.eos_token_id]
    return {"input_ids": prefix + target, "labels": [-100] * len(prefix) + target}


def collate(examples, tokenizer, device):
    import torch
    size = max(len(row["input_ids"]) for row in examples)
    ids = torch.full((len(examples), size), tokenizer.eos_token_id, device=device, dtype=torch.long)
    labels = torch.full_like(ids, -100)
    mask = torch.zeros_like(ids)
    for index, row in enumerate(examples):
        width = len(row["input_ids"])
        ids[index, :width] = torch.tensor(row["input_ids"], device=device)
        labels[index, :width] = torch.tensor(row["labels"], device=device)
        mask[index, :width] = 1
    return {"input_ids": ids, "labels": labels, "attention_mask": mask}


def teacher_cache(model, tokenizer, examples):
    import torch
    model.eval()
    with torch.no_grad():
        for start in range(0, len(examples), 8):
            rows = examples[start:start + 8]
            batch = collate(rows, tokenizer, model.device)
            logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits[:, :-1]
            for index, row in enumerate(rows):
                chosen = logits[index][batch["labels"][index, 1:] != -100].float().softmax(-1)
                probabilities, indices = chosen.topk(32, dim=-1)
                row["teacher_ids"] = indices.cpu()
                row["teacher_probs"] = torch.cat((probabilities, (1 - probabilities.sum(-1, keepdim=True)).clamp_min(1e-8)), -1).cpu()


def optimize(model, tokenizer, recent, older, generic, optimizer, steps, seed):
    import torch
    rng = random.Random(seed)
    model.train()
    model.config.use_cache = False
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    started, losses = time.perf_counter(), []
    for step in range(steps):
        personal = rng.choices(recent, k=8) + rng.choices(older or recent, k=4)
        references = rng.sample(generic, 4)
        batch = collate(personal + references, tokenizer, model.device)
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logits = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"]).logits[:, :-1]
        labels = batch["labels"][:, 1:]
        valid = labels != -100
        personal_loss = torch.nn.functional.cross_entropy(logits[:12][valid[:12]].float(), labels[:12][valid[:12]])
        replay_loss, kl = logits.new_zeros((), dtype=torch.float32), logits.new_zeros((), dtype=torch.float32)
        for offset, reference in enumerate(references, 12):
            log_probs = logits[offset][valid[offset]].float().log_softmax(-1)
            replay_loss = replay_loss + torch.nn.functional.nll_loss(log_probs, labels[offset][valid[offset]]) / 4
            selected = log_probs.gather(-1, reference["teacher_ids"].to(model.device)).exp()
            student = torch.cat((selected, (1 - selected.sum(-1, keepdim=True)).clamp_min(1e-8)), -1)
            teacher = reference["teacher_probs"].to(model.device)
            kl = kl + (teacher * (teacher.clamp_min(1e-8).log() - student.clamp_min(1e-8).log())).sum(-1).mean() / 4
        loss = personal_loss + 0.25 * replay_loss + kl
        loss.backward()
        torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad), 1.0)
        optimizer.step()
        losses.append(float(loss.detach()))
    torch.cuda.synchronize()
    model.eval()
    return {"seconds": time.perf_counter() - started, "steps": steps,
            "loss": statistics.mean(losses), "peak_allocated_mib": torch.cuda.max_memory_allocated() / 2**20,
            "trainable_parameters": sum(p.numel() for p in model.parameters() if p.requires_grad)}


def generate(model, tokenizer, rows, beams=5, batch_size=12):
    import torch
    outputs = [None] * len(rows)
    order = sorted(range(len(rows)), key=lambda i: len(rows[i]["keys"]))
    model.eval()
    with torch.no_grad():
        for start in range(0, len(order), batch_size):
            indices = order[start:start + batch_size]
            prompts = [build_prompt(rows[i]["keys"], rows[i].get("context", "")[-64:]) + "结果：" for i in indices]
            encoded = tokenizer(prompts, add_special_tokens=False, padding=True, return_tensors="pt").to(model.device)
            tokens = model.generate(**encoded, num_beams=beams, num_return_sequences=beams, do_sample=False,
                                    max_new_tokens=2 * max(len(letters(rows[i]["keys"])) for i in indices) + 8,
                                    pad_token_id=tokenizer.eos_token_id, eos_token_id=tokenizer.eos_token_id,
                                    use_cache=True)
            texts = tokenizer.batch_decode(tokens[:, encoded["input_ids"].shape[1]:], skip_special_tokens=True)
            for offset, index in enumerate(indices):
                candidates = []
                for raw in texts[offset * beams:(offset + 1) * beams]:
                    text = raw.split("\n", 1)[0].strip()
                    if text not in candidates and structural_valid(rows[index]["keys"], {"result": text, "segments": None}):
                        candidates.append(text)
                outputs[index] = candidates
    return outputs


def latency(model, tokenizer, rows):
    import torch
    timings = []
    generate(model, tokenizer, rows[:2], batch_size=1)
    for row in rows[:16]:
        torch.cuda.synchronize()
        started = time.perf_counter()
        generate(model, tokenizer, [row], batch_size=1)
        torch.cuda.synchronize()
        timings.append(1000 * (time.perf_counter() - started))
    return {"backend": "transformers-bf16-cuda-batch1-beam5", "samples": len(timings),
            "median_ms": statistics.median(timings), "p95_ms": sorted(timings)[math.ceil(len(timings) * .95) - 1]}


def attach_lora(base, method, learning_rate, seed):
    import torch
    from peft import LoraConfig, get_peft_model
    from peft.optimizers import create_lorafa_optimizer
    torch.manual_seed(seed)
    model = get_peft_model(base, LoraConfig(r=8, lora_alpha=16, target_modules=["q_proj", "v_proj"],
                                          lora_dropout=0.0, bias="none", task_type="CAUSAL_LM"))
    if method == "lorafa":
        optimizer = create_lorafa_optimizer(model, r=8, lora_alpha=16, lr=learning_rate)
    else:
        optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=learning_rate)
    return model, optimizer


def extract_basis(model):
    values = {}
    for name, module in model.get_base_model().named_modules():
        if hasattr(module, "lora_A"):
            values[name] = {"a": module.lora_A["default"].weight.detach().cpu().clone(),
                            "b": module.lora_B["default"].weight.detach().cpu().clone() * module.scaling["default"]}
    return values


def attach_basis(base, bank, learning_rate):
    import torch

    class BasisLinear(torch.nn.Module):
        def __init__(self, linear, values, coefficients):
            super().__init__()
            self.linear = linear
            self.register_buffer("a", torch.stack([v["a"] for v in values]).to(linear.weight))
            self.register_buffer("b", torch.stack([v["b"] for v in values]).to(linear.weight))
            object.__setattr__(self, "coefficients", coefficients)

        def forward(self, inputs):
            result = self.linear(inputs)
            # One scalar per prelearned adapter, shared across all its layers.
            for index in range(len(self.a)):
                delta = torch.nn.functional.linear(torch.nn.functional.linear(inputs, self.a[index]), self.b[index])
                result = result + self.coefficients[index].to(result.dtype) * delta
            return result

    for parameter in base.parameters():
        parameter.requires_grad_(False)
    coefficient = torch.nn.Parameter(torch.zeros(len(bank), device=base.device))
    base.register_parameter("personal_basis_coefficients", coefficient)
    originals = {}
    for name in bank[0]:
        originals[name] = base.get_submodule(name)
        base.set_submodule(name, BasisLinear(originals[name], [item[name] for item in bank], coefficient))
    return base, torch.optim.AdamW([coefficient], lr=learning_rate, weight_decay=0.01), originals


def detach_basis(model, originals):
    for name, module in originals.items():
        model.set_submodule(name, module)
    delattr(model, "personal_basis_coefficients")
    return model


def save_result(out, method, stream, cases, probes, training, model_latency, extra):
    seen = set()
    for case in cases:
        case["seen"] = case["target"] in seen
        seen.add(case["target"])
    result = {"method": method, "seed": stream["seed"], "profile": stream["profile"],
              "metrics": {"all": metrics(cases), "natural": metrics([r for r in cases if r["category"] == "natural"]),
                          "controlled": metrics([r for r in cases if r["category"] == "controlled"]),
                          "new_target": metrics([r for r in cases if not r["seen"]]),
                          "repeated_target": metrics([r for r in cases if r["seen"]])},
              "blocks": {str(block): metrics([r for r in cases if r["block"] == block]) for block in range(4)},
              "generic_before": metrics(probes[0]), "generic_after": metrics(probes[-1]),
              "training": training, "latency": model_latency, "extra": extra}
    prefix = out / f"{method}-{stream['profile']}-{stream['seed']}"
    write_json(prefix.with_suffix(".json"), result)
    write_json(prefix.with_suffix(".cases.json"), {"stream": cases, "generic": probes})
    print(json.dumps({"done": prefix.name, "natural": result["metrics"]["natural"],
                      "generic_after": result["generic_after"], "train_seconds": sum(t["seconds"] for t in training)}), flush=True)


def run(args):
    import platform
    import torch
    import transformers
    import peft
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(8)
    torch.cuda.set_per_process_memory_fraction(args.gpu_fraction)
    data_path = Path(args.data)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    tokenizer = AutoTokenizer.from_pretrained(args.checkpoint, padding_side="left")
    tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(args.checkpoint, dtype=torch.bfloat16, attn_implementation="sdpa").to("cuda").eval()
    for parameter in base.parameters():
        parameter.requires_grad_(False)
    environment = {"torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__,
                   "gpu": torch.cuda.get_device_name(), "python": platform.python_version(), "args": vars(args),
                   "data_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
                   "protocol": "Score each event before its memory update; train adapters only after a completed block."
                               " All users reset model adapters and memory. Generic probes never train."}
    write_json(out / "environment.json", environment)
    print(json.dumps({"loaded": args.checkpoint, "gpu": environment["gpu"]}), flush=True)
    baseline_path = out / "base-predictions.json"
    if baseline_path.exists():
        cached = json.loads(baseline_path.read_text(encoding="utf-8"))
        if cached["data_sha256"] != environment["data_sha256"] or cached["checkpoint"] != args.checkpoint:
            raise ValueError("Cached baseline belongs to a different experiment")
    else:
        unique = {}
        for row in data["generic"] + [r for s in data["streams"] for r in s["events"]]:
            unique[signature(row)] = row
        rows = list(unique.values())
        predictions = generate(base, tokenizer, rows)
        cached = {"data_sha256": environment["data_sha256"], "checkpoint": args.checkpoint,
                  "predictions": [[list(signature(row)), pred] for row, pred in zip(rows, predictions)],
                  "latency": latency(base, tokenizer, data["generic"])}
        write_json(baseline_path, cached)
    base_outputs = {tuple(key): values for key, values in cached["predictions"]}
    generic_outputs = [base_outputs[signature(row)] for row in data["generic"]]
    generic_before = replay_stream(data["generic"], generic_outputs)
    print(json.dumps({"baseline_generic": metrics(generic_before)}), flush=True)
    for stream in data["streams"]:
        outputs = [base_outputs[signature(row)] for row in stream["events"]]
        for method in ("baseline", "memory"):
            if (out / f"{method}-{stream['profile']}-{stream['seed']}.json").exists():
                continue
            memory = PersonalMemory() if method == "memory" else None
            cases = replay_stream(stream["events"], outputs, memory)
            after = replay_stream(data["generic"], generic_outputs, memory, learn=False)
            save_result(out, method, stream, cases, [generic_before, after], [], cached["latency"], {})
    if not set(args.methods) & {"lora", "lorafa", "basis"}:
        return
    replay = [encode(tokenizer, row) for row in data["replay"]]
    teacher_cache(base, tokenizer, replay)
    print(json.dumps({"teacher_cache_rows": len(replay)}), flush=True)
    bank = []
    if "basis" in args.methods:
        for index, (name, rows) in enumerate(data["bank"].items()):
            path = out / f"bank-{name}.pt"
            if path.exists():
                bank.append(torch.load(path, map_location="cpu", weights_only=True))
                continue
            model, optimizer = attach_lora(base, "lora", args.learning_rate, 42 + index)
            encoded = [encode(tokenizer, row) for row in rows]
            training = optimize(model, tokenizer, encoded, [], replay, optimizer, args.bank_steps, 42 + index)
            values = extract_basis(model)
            bank.append(values)
            torch.save(values, path)
            write_json(path.with_suffix(".json"), training)
            base = model.unload()
            del optimizer, model
            torch.cuda.empty_cache()
            print(json.dumps({"bank": name, **training}), flush=True)
    for stream in data["streams"]:
        for method in args.methods:
            if method not in ("lora", "lorafa", "basis"):
                continue
            if (out / f"{method}-{stream['profile']}-{stream['seed']}.json").exists():
                continue
            originals = None
            if method == "basis":
                model, optimizer, originals = attach_basis(base, bank, args.basis_learning_rate)
            else:
                model, optimizer = attach_lora(base, method, args.learning_rate, stream["seed"])
            memory, cases, training, previous = PersonalMemory(), [], [], []
            for block in range(4):
                rows = [row for row in stream["events"] if row["block"] == block]
                outputs = ([base_outputs[signature(row)] for row in rows] if block == 0
                           else generate(model, tokenizer, rows))
                cases.extend(replay_stream(rows, outputs, memory))
                encoded = [encode(tokenizer, row) for row in rows]
                timing = optimize(model, tokenizer, encoded, previous, replay, optimizer, args.steps, stream["seed"] + block)
                training.append(timing)
                previous.extend(encoded)
                print(json.dumps({"method": method, "profile": stream["profile"], "seed": stream["seed"],
                                  "block": block, "top1": metrics(cases)["top1"], **timing}), flush=True)
            final_generic = generate(model, tokenizer, data["generic"])
            after = replay_stream(data["generic"], final_generic, memory, learn=False)
            model_latency = latency(model, tokenizer, data["generic"])
            extra = {}
            if method == "basis":
                extra["coefficients"] = model.personal_basis_coefficients.detach().cpu().tolist()
                extra["shared_bank_bytes"] = sum(p.stat().st_size for p in out.glob("bank-*.pt"))
                base = detach_basis(model, originals)
            else:
                model.save_pretrained(out / f"adapter-{method}-{stream['profile']}-{stream['seed']}")
                base = model.unload()
            save_result(out, method, stream, cases, [generic_before, after], training, model_latency, extra)
            del optimizer, model
            torch.cuda.empty_cache()


def summarize(args):
    out = Path(args.output)
    methods = collections.defaultdict(list)
    for path in out.glob("*.json"):
        if ".cases." in path.name:
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if "method" in value:
            methods[value["method"]].append(value)
    summary = {}
    for method, runs in methods.items():
        cases, probes = [], []
        for run in runs:
            path = out / f"{method}-{run['profile']}-{run['seed']}.cases.json"
            records = json.loads(path.read_text(encoding="utf-8"))
            cases.extend(records["stream"])
            probes.extend(records["generic"][-1])
        summary[method] = {"runs": len(runs), "natural": metrics([r for r in cases if r["category"] == "natural"]),
                           "controlled": metrics([r for r in cases if r["category"] == "controlled"]),
                           "new_target": metrics([r for r in cases if not r["seen"]]),
                           "repeated_target": metrics([r for r in cases if r["seen"]]),
                           "generic_after": metrics(probes),
                           "mean_train_seconds": statistics.mean(sum(t["seconds"] for t in r["training"]) for r in runs),
                           "peak_train_mib": max((t["peak_allocated_mib"] for r in runs for t in r["training"]), default=0),
                           "trainable_parameters": max((t["trainable_parameters"] for r in runs for t in r["training"]), default=0),
                           "mean_inference_p95_ms": statistics.mean(r["latency"]["p95_ms"] for r in runs),
                           "blocks": {str(b): metrics([r for r in cases if r["block"] == b]) for b in range(4)}}
    bank_times = [json.loads(p.read_text())["seconds"] for p in out.glob("bank-*.json")]
    bank_bytes = max((r["extra"].get("shared_bank_bytes", 0) for runs in methods.values() for r in runs), default=0)
    result = {"methods": summary, "shared_bank_pretrain_seconds": sum(bank_times), "shared_bank_bytes": bank_bytes}
    write_json(out / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def rerank(args):
    import shutil

    source, out = Path(args.results), Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    streams = {(s["profile"], s["seed"]): s for s in data["streams"]}
    for path in source.glob("bank-*.json"):
        shutil.copy2(path, out / path.name)
    environment = json.loads((source / "environment.json").read_text(encoding="utf-8"))
    environment["reranking"] = {"source_results": str(source.resolve()), "policy": "nonnegative-model-rank-personal-residual-v3",
                                "data_sha256": hashlib.sha256(Path(args.data).read_bytes()).hexdigest()}
    write_json(out / "environment.json", environment)
    for path in sorted(source.glob("*.json")):
        if ".cases." in path.name:
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if "method" not in result:
            continue
        stream = streams[result["profile"], result["seed"]]
        stored = json.loads(path.with_suffix(".cases.json").read_text(encoding="utf-8"))
        memory = None if result["method"] == "baseline" else PersonalMemory()
        cases = replay_stream(stream["events"], [r["model_top"] for r in stored["stream"]], memory)
        after = replay_stream(data["generic"], [r["model_top"] for r in stored["generic"][-1]], memory, learn=False)
        save_result(out, result["method"], stream, cases, [stored["generic"][0], after], result["training"],
                    result["latency"], result["extra"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--personal-test", required=True)
    prepare_parser.add_argument("--bank-train", required=True)
    prepare_parser.add_argument("--generic-test", nargs="+", required=True)
    prepare_parser.add_argument("--generic-train", nargs="+", required=True)
    prepare_parser.add_argument("--output", required=True)
    prepare_parser.add_argument("--seeds", nargs="+", type=int, default=[17, 29])
    prepare_parser.add_argument("--block-size", type=int, default=64)
    prepare_parser.add_argument("--generic-count", type=int, default=128)
    prepare_parser.add_argument("--replay-count", type=int, default=256)
    prepare_parser.add_argument("--bank-count", type=int, default=256)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--checkpoint", required=True)
    run_parser.add_argument("--data", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--methods", nargs="+", choices=["memory", "lora", "lorafa", "basis"], default=["lora", "lorafa", "basis"])
    run_parser.add_argument("--steps", type=int, default=24)
    run_parser.add_argument("--bank-steps", type=int, default=48)
    run_parser.add_argument("--learning-rate", type=float, default=2e-4)
    run_parser.add_argument("--basis-learning-rate", type=float, default=0.03)
    run_parser.add_argument("--gpu-fraction", type=float, default=0.22)
    summary_parser = commands.add_parser("summarize")
    summary_parser.add_argument("--output", required=True)
    rerank_parser = commands.add_parser("rerank")
    rerank_parser.add_argument("--data", required=True)
    rerank_parser.add_argument("--results", required=True)
    rerank_parser.add_argument("--output", required=True)
    args = parser.parse_args()
    {"prepare": prepare, "run": run, "summarize": summarize, "rerank": rerank}[args.command](args)


if __name__ == "__main__":
    main()
