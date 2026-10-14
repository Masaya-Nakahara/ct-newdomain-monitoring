# -*- coding: utf-8 -*-
"""重み公開モデル（Qwen2.5-7B-Instruct, GGUF Q4_K_M）を llama.cpp で動かし、gold の到達ページ131件を分類する。
温度0・シード固定・重み固定のため、第三者が同一条件で再現できる。

  python classify_gold_local.py TAG     例: qwen_a / qwen_b（2回実行して決定性を確認）

前提: localllm/bin/llama-server.exe と localllm/models/*.gguf
出力: llm_gold_<TAG>.csv, repeat_report.txt（追記）
"""
import sys, os, csv, json, time, sqlite3, subprocess, datetime as dt
from math import comb
import requests
import classify_gold_spark as G

TAG = sys.argv[1] if len(sys.argv) > 1 else "qwen_a"
HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "localllm", "bin", "llama-server.exe")
MODEL = os.path.join(HERE, "localllm", "models", "qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf")
PORT = 8089
SCHEMA = {"type": "object", "properties": {"category": {"type": "string", "enum": G.CATS}},
          "required": ["category"], "additionalProperties": False}


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n)


def start_server():
    log = open(os.path.join(HERE, f"llama_server_{TAG}.log"), "w", encoding="utf-8")
    p = subprocess.Popen([SERVER, "-m", MODEL, "--port", str(PORT), "-c", "4096", "-t", "8",
                          "--temp", "0", "--seed", "42"], stdout=log, stderr=subprocess.STDOUT)
    for _ in range(300):
        try:
            if requests.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200:
                return p
        except Exception:
            pass
        time.sleep(2)
    p.kill(); raise SystemExit("llama-server が起動しませんでした")


def classify(page):
    user = (G.RUBRIC + "\n\nClassify this ONE domain. Respond only with JSON {\"category\": ...}.\n\n"
            + json.dumps(page, ensure_ascii=False))
    body = {"messages": [{"role": "user", "content": user}], "temperature": 0, "seed": 42,
            "max_tokens": 20, "response_format": {"type": "json_schema", "json_schema": {"name": "c", "schema": SCHEMA}}}
    r = requests.post(f"http://127.0.0.1:{PORT}/v1/chat/completions", json=body, timeout=600)
    txt = r.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(txt)["category"]
    except Exception:
        return "unknown"


def main():
    base = {r["apex"]: r for r in csv.DictReader(open("llm_gold_spark.csv", encoding="utf-8"))}
    c = sqlite3.connect(G.DB, timeout=120)
    pages = {a: (ti, m, tx) for a, ti, m, tx in c.execute(
        "SELECT apex,title,meta,text FROM pages WHERE status IS NOT NULL AND status<>0 AND length(text)>=100")}
    content = [a for a in base if a in pages]
    srv = start_server()
    pred, t0 = {}, time.time()
    try:
        for i, a in enumerate(content, 1):
            page = {"apex": a, "title": (pages[a][0] or "")[:120], "meta": (pages[a][1] or "")[:200],
                    "text": (pages[a][2] or "")[:900]}
            pred[a] = G.MAP3.get(classify(page), "live")
            if i % 10 == 0:
                print(f"  [{TAG}] {i}/{len(content)}  {(time.time()-t0)/i:.1f}s/件", flush=True)
    finally:
        srv.kill()
    with open(f"llm_gold_{TAG}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["apex", "gold", "heuristic", "llm_first", f"llm_{TAG}"])
        for a in content: w.writerow([a, base[a]["gold"], base[a]["heuristic"], base[a]["llm_spark"], pred[a]])
    n = len(content)
    acc = sum(1 for a in content if pred[a] == base[a]["gold"])
    ab = [a for a in content if base[a]["gold"] == "abuse"]
    rec = sum(1 for a in ab if pred[a] == "abuse")
    b_ = sum(1 for a in ab if base[a]["heuristic"] == "abuse" and pred[a] != "abuse")
    c_ = sum(1 for a in ab if base[a]["heuristic"] != "abuse" and pred[a] == "abuse")
    pa = [a for a in content if pred[a] == "abuse"]
    prec = sum(1 for a in pa if base[a]["gold"] == "abuse") / len(pa) if pa else 0
    lines = [f"=== {dt.datetime.now().isoformat(timespec='seconds')}  model=Qwen2.5-7B-Instruct-Q4_K_M (llama.cpp b11342, temp0, seed42) tag={TAG}  n={n} ===",
             f"  正解率: {100*acc/n:.1f}%   abuse再現率: {rec}/{len(ab)}={rec/len(ab):.2f}   abuse適合率: {prec:.2f}",
             f"  abuse再現 McNemar vs ヒューリスティック: heurのみ{b_} / LLMのみ{c_}  p={mcnemar(b_, c_):.3f}",
             f"  所要 {(time.time()-t0)/60:.1f}分"]
    open("repeat_report.txt", "a", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
