# -*- coding: utf-8 -*-
"""gold集合を固定モデル gpt-5.3-codex-spark(Codex, ChatGPTログイン)で3分類し、
gold(著者ラベル)に対するクラス別 精度/再現率/F1 + n + Wilson 95%CI を算出する。
再現性のためモデル名・プロンプト・入力(title120/meta200/text900)・出力スキーマを固定。
出力: llm_gold_spark.csv, gold_eval_report.txt
"""
import sqlite3, json, subprocess, os, tempfile, csv, math, datetime as dt

import glob as _glob
# 最新の Codex 本体（VS Code 拡張に同梱）を使う。古い本体ではモデル一覧の取得に失敗する
CODEX = os.environ.get("CODEX_BIN", "codex")   # Codex CLI の実行ファイル
MODEL = "gpt-5.3-codex-spark"
GOLD = "gold_labels.csv"   # 正解ラベル（非公開）
DB = "ct_data.db"
CATS = ["service","commerce","business","blog","parking","spam","unknown"]
MAP3 = {"service":"live","commerce":"live","business":"live","blog":"live","unknown":"live",
        "parking":"non-content","spam":"abuse"}
RUBRIC = """You are labeling website homepages for an academic study on newly-issued
internet domains (from Certificate Transparency logs). For EACH input domain, read its
title/meta/visible-text (which may be in any language) and assign exactly ONE category:
- service  : a SaaS / web app / online platform / API / tool people sign into or use.
- commerce : an online store selling physical/digital goods (cart, checkout, catalog).
- business : a company/organization presence site (agency, clinic, restaurant, hotel, law
             firm, local business, corporate/brochure site) - informational, not self-serve.
- blog     : a personal blog, news/media, or content/article site.
- parking  : a parked/for-sale/placeholder/"coming soon"/default-server page, no real content.
- spam     : gambling/casino/betting, adult/porn/escort, illegal pharma, replica, SEO/link
             spam, scam. Judge by MEANING in ANY language.
- unknown  : reachable but you genuinely cannot tell the category.
Judge by MEANING across languages, not just English keywords. Respond ONLY via the JSON
schema. Do NOT use any tools or run any commands - answer directly."""

def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0)
    p = k / n; d = 1 + z*z/n
    c = (p + z*z/(2*n)) / d
    h = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return (max(0, c-h), min(1, c+h))

def run_codex(payload):
    schema = {"type":"object","properties":{"results":{"type":"array","items":{"type":"object",
        "properties":{"apex":{"type":"string"},"category":{"type":"string","enum":CATS}},
        "required":["apex","category"],"additionalProperties":False}}},
        "required":["results"],"additionalProperties":False}
    tmp = tempfile.mkdtemp(prefix="gold_"); sf=os.path.join(tmp,"s.json"); of=os.path.join(tmp,"o.json")
    json.dump(schema, open(sf,"w"))
    prompt = RUBRIC + "\n\nClassify EVERY domain. Return {\"results\":[{apex,category}]}.\n\n" + json.dumps(payload, ensure_ascii=False)
    cmd = [CODEX,"exec","-m",MODEL,"-c","model_reasoning_effort=low","-s","read-only",
           "--skip-git-repo-check","--ephemeral","--output-schema",sf,"-o",of,"-"]
    r = subprocess.run(cmd, input=prompt, text=True, encoding="utf-8", capture_output=True,
                       cwd=".")
    if not os.path.exists(of):
        raise SystemExit("codex out missing: " + (r.stderr or "")[-500:])
    raw = open(of, encoding="utf-8").read()
    try: data = json.loads(raw)
    except Exception:
        i,j = raw.find("{"), raw.rfind("}"); data = json.loads(raw[i:j+1])
    return {d["apex"]: d["category"] for d in data.get("results", [])}

def main():
    gold = {}
    with open(GOLD, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            gold[row["apex"]] = {"gold": row["gold"], "draft": row["draft"]}
    c = sqlite3.connect(DB)
    pages = {a:(ti,m,tx) for a,ti,m,tx in c.execute(
        "SELECT apex,title,meta,text FROM pages WHERE status IS NOT NULL AND status<>0 AND length(text)>=100")}
    content = [a for a in gold if a in pages]           # 内容あり集合
    empty = [a for a in gold if a not in pages]         # 未到達/空 → non-content 固定
    print(f"gold={len(gold)} content={len(content)} empty={len(empty)}", flush=True)

    llm = {}
    B = 25
    for i in range(0, len(content), B):
        batch = content[i:i+B]
        payload = [{"apex":a, "title":(pages[a][0] or "")[:120], "meta":(pages[a][1] or "")[:200],
                    "text":(pages[a][2] or "")[:900]} for a in batch]
        got = run_codex(payload)
        for a in batch:
            cat7 = got.get(a, "unknown")
            llm[a] = MAP3.get(cat7, "live")
        print(f"  batch {i//B+1}/{(len(content)+B-1)//B} done ({len(llm)})", flush=True)
    for a in empty:
        llm[a] = "non-content"

    with open("llm_gold_spark.csv","w",encoding="utf-8",newline="") as f:
        w = csv.writer(f); w.writerow(["apex","gold","heuristic","llm_spark"])
        for a in gold: w.writerow([a, gold[a]["gold"], gold[a]["draft"], llm[a]])

    def evaluate(apexes, name, out):
        classes = ["live","non-content","abuse"]
        def stats(pred_key):
            preds = {a:(gold[a]["draft"] if pred_key=="heur" else llm[a]) for a in apexes}
            acc = sum(1 for a in apexes if preds[a]==gold[a]["gold"])/len(apexes)
            lo,hi = wilson(sum(1 for a in apexes if preds[a]==gold[a]["gold"]), len(apexes))
            lines=[f"  正解率: {acc*100:.1f}%  (95%CI {lo*100:.1f}-{hi*100:.1f})  n={len(apexes)}"]
            for cl in classes:
                tp=sum(1 for a in apexes if preds[a]==cl and gold[a]["gold"]==cl)
                fp=sum(1 for a in apexes if preds[a]==cl and gold[a]["gold"]!=cl)
                fn=sum(1 for a in apexes if preds[a]!=cl and gold[a]["gold"]==cl)
                supp=sum(1 for a in apexes if gold[a]["gold"]==cl)
                prec=tp/(tp+fp) if tp+fp else 0; rec=tp/(tp+fn) if tp+fn else 0
                f1=2*prec*rec/(prec+rec) if prec+rec else 0
                rlo,rhi=wilson(tp, tp+fn)
                lines.append(f"    {cl:<12} n={supp:<3} P={prec:.2f} R={rec:.2f}(CI {rlo:.2f}-{rhi:.2f}) F1={f1:.2f}")
            return "\n".join(lines)
        out.append(f"=== {name} ===")
        out.append("[ヒューリスティック]"); out.append(stats("heur"))
        out.append(f"[生成AI {MODEL}]"); out.append(stats("llm"))
        out.append("")

    rep=[f"評価日: {dt.datetime.now().isoformat(timespec='seconds')}  モデル: {MODEL}",
         "gold=著者3分類ラベル / heuristic=語彙規則 / llm=生成AI",""]
    evaluate(list(gold.keys()), f"全{len(gold)}件", rep)
    evaluate(content, f"内容あり{len(content)}件", rep)
    open("gold_eval_report.txt","w",encoding="utf-8").write("\n".join(rep))
    print("\n".join(rep))
    print("\n-> llm_gold_spark.csv / gold_eval_report.txt")

if __name__ == "__main__":
    main()
