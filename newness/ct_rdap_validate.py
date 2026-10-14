# -*- coding: utf-8 -*-
"""主軸Aの妥当性検証: RDAP登録日と初回証明書日を照合する。

  python ct_rdap_validate.py run      scale_sample(kind='A') の全ドメイン（crt.sh成功/欠測とも）の RDAP 登録日を取得
  python ct_rdap_validate.py report   照合結果の集計

目的:
  (1) 初回証明書日による「新規(≤120日)/既存」判定が、RDAP登録日による判定とどの程度一致するか
  (2) crt.sh で取得できなかった（欠測）ドメインの年齢分布 → 欠測バイアスの方向と大きさ
  (3) 初回証明書日 < 登録日 となる再取得（持ち主の変化）の割合
RDAPサーバは IANA bootstrap (data.iana.org/rdap/dns.json) で TLD ごとに決める。
"""
import sqlite3, sys, time, math, datetime as dt
import requests

DB = "ct_data.db"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (research service-measurement; academic)",
                  "Accept": "application/rdap+json"})


def bootstrap():
    j = S.get("https://data.iana.org/rdap/dns.json", timeout=30).json()
    m = {}
    for tlds, urls in j["services"]:
        for t in tlds: m[t.lower()] = urls[0].rstrip("/") + "/"
    return m


def rdap_reg(apex, boot):
    parts = apex.split(".")
    base = boot.get(".".join(parts[-2:])) or boot.get(parts[-1])
    if not base: return None, "no_server"
    for attempt in range(3):
        try:
            r = S.get(base + "domain/" + apex, timeout=20)
            if r.status_code == 404: return None, "not_found"
            if r.status_code == 429: time.sleep(10 * (attempt + 1)); continue
            if r.status_code != 200: time.sleep(2); continue
            for e in r.json().get("events", []):
                if e.get("eventAction") == "registration" and e.get("eventDate"):
                    return e["eventDate"][:10], "ok"
            return None, "no_reg_event"
        except Exception:
            time.sleep(2)
    return None, "fail"


def run(target="a"):
    c = sqlite3.connect(DB, timeout=180); c.execute("PRAGMA busy_timeout=180000")
    c.execute("""CREATE TABLE IF NOT EXISTS rdap(apex TEXT PRIMARY KEY, reg_date TEXT, status TEXT, at TEXT)""")
    boot = bootstrap()
    done = {a for (a,) in c.execute("SELECT apex FROM rdap WHERE status IN ('ok','not_found','no_server','no_reg_event')")}
    src = ("SELECT apex FROM a_plan ORDER BY RANDOM()" if target == "a" else   # 主軸A: 無作為標本
           "SELECT apex FROM b_plan UNION SELECT apex FROM leadtime")          # 主軸B: Show HN の計画＋突合済み
    todo = [a for (a,) in c.execute(src) if a not in done]
    print(f"[RDAP] 対象 {len(todo)}件", flush=True)
    for i, a in enumerate(todo, 1):
        reg, st = rdap_reg(a, boot)
        c.execute("INSERT OR REPLACE INTO rdap VALUES(?,?,?,?)",
                  (a, reg, st, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        c.commit()
        if i % 50 == 0: print(f"  [RDAP] {i}/{len(todo)}", flush=True)
        time.sleep(0.8)
    print("[RDAP] 完了", flush=True)


def report():
    c = sqlite3.connect(DB, timeout=180)
    rows = c.execute("""SELECT p.apex, p.stratum, f.first_cert_date, r.reg_date, r.status
        FROM a_plan p LEFT JOIN firstcert f ON f.apex=p.apex AND f.first_cert_date IS NOT NULL
        LEFT JOIN rdap r ON r.apex=p.apex""").fetchall()
    d = lambda x: dt.date.fromisoformat(x[:10])
    def wil(k, n, z=1.96):
        if n == 0: return (0, 0)
        p = k / n; dd = 1 + z*z/n; ce = (p + z*z/(2*n)) / dd
        h = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / dd
        return max(0, ce-h), min(1, ce+h)
    st = {}
    for r in rows: st[r[4]] = st.get(r[4], 0) + 1
    print(f"a_plan {len(rows)}件  RDAP取得状況: {st}")
    reg = [r for r in rows if r[3]]
    for label, sub in [("全期間", reg), ("warm-up除外(06-30以降)", [r for r in reg if r[1] > "2026-06-29"])]:
        n = len(sub); old = sum(1 for r in sub if (d(r[1]) - d(r[3])).days > 120)
        lo, hi = wil(old, n)
        if n:
            print(f"[RDAP基準 {label}] n={n}  既存(登録>120日前) {100*old/n:.1f}% (95%CI {100*lo:.1f}-{100*hi:.1f})"
                  f"  新規(≤120日) {n-old}件")
    by = {}
    for r in reg: by.setdefault(r[1][:7], []).append((d(r[1]) - d(r[3])).days)
    print("  月別 既存割合: " + "  ".join(f"{m}:{100*sum(x>120 for x in g)/len(g):.0f}%(n={len(g)})" for m, g in sorted(by.items())))
    tl = {}
    for r in rows:
        t_ = r[0].split(".")[-1]; tl.setdefault(t_, [0, 0]); tl[t_][0] += 1; tl[t_][1] += 1 if r[3] else 0
    print("  TLD別RDAP取得率(上位): " + ", ".join(f".{k}:{v[1]}/{v[0]}" for k, v in sorted(tl.items(), key=lambda x: -x[1][0])[:10]))
    both = [r for r in rows if r[2] and r[3]]
    if both:
        agree = sum(1 for r in both if ((d(r[1]) - d(r[2])).days <= 120) == ((d(r[1]) - d(r[3])).days <= 120))
        reuse = sum(1 for r in both if d(r[2]) < d(r[3]))
        print(f"[初回証明書との照合] 両方あり n={len(both)}  新規/既存判定の一致率 {100*agree/len(both):.1f}%"
              f"  初回証明書<登録日(再取得の疑い) {reuse}件 ({100*reuse/len(both):.1f}%)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run": run(sys.argv[2] if len(sys.argv) > 2 else "a")
    else: report()
