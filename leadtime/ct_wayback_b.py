# -*- coding: utf-8 -*-
"""主軸B: 新規ローンチ帯のサービスが，初回証明書日以降に Wayback Machine へ初めて記録された日を取得する。

  python ct_wayback_b.py run      b_plan のうち先行日数0〜365日の apex について CDX API で照会
  python ct_wayback_b.py report   初回証明書日・初回記録日・告知日の前後関係を集計

告知日（Show HN 投稿日）はサービスの公開日そのものではないため，第三者のクローラが記録した日を
「遅くともこの日までには外部から見えていた」目安として用いる。以前の所有者の時代の記録を拾わないよう，
照会は初回証明書日以降に限る（from=初回証明書日）。応答は 2xx/3xx を記録ありとみなす（apex→www の転送を含めるため）。
"""
import sqlite3, sys, time, datetime as dt, statistics as S
import requests

DB = "ct_data.db"
H = requests.Session()
H.headers.update({"User-Agent": "Mozilla/5.0 (research service-measurement; academic)"})


def first_capture_after(host, ymd):
    url = (f"https://web.archive.org/cdx/search/cdx?url={host}&from={ymd}&output=json&limit=1"
           "&fl=timestamp,statuscode&filter=statuscode:[23]..")
    for attempt in range(3):
        try:
            r = H.get(url, timeout=60)
            if r.status_code == 200:
                rows = r.json() if r.text.strip() else []
                return (rows[1][0] if len(rows) > 1 else None), "ok"
            if r.status_code in (429, 503): time.sleep(20 * (attempt + 1)); continue
        except Exception:
            time.sleep(10 * (attempt + 1))
    return None, "fail"


def targets(c):
    d = lambda x: dt.date.fromisoformat(x[:10])
    rs = c.execute("""SELECT b.apex, b.hn_date, l.first_cert_date FROM b_plan b JOIN leadtime l ON l.apex=b.apex
                      WHERE l.first_cert_date IS NOT NULL""").fetchall()
    return [r for r in rs if 0 <= (d(r[1]) - d(r[2])).days <= 365]


def run():
    c = sqlite3.connect(DB, timeout=180); c.execute("PRAGMA busy_timeout=180000")
    c.execute("DROP TABLE IF EXISTS wayback") if c.execute(
        "SELECT COUNT(*) FROM pragma_table_info('wayback') WHERE name='from_date'").fetchone()[0] == 0 else None
    c.execute("CREATE TABLE IF NOT EXISTS wayback(apex TEXT PRIMARY KEY, from_date TEXT, first_ts TEXT, status TEXT, at TEXT)")
    done = {a for (a,) in c.execute("SELECT apex FROM wayback WHERE status='ok'")}
    todo = [r for r in targets(c) if r[0] not in done]
    print(f"[WB] 対象 {len(todo)}件", flush=True)
    for i, (a, hn, fc) in enumerate(todo, 1):
        ymd = fc[:10].replace("-", "")
        t, st = first_capture_after(a, ymd); time.sleep(1.5)
        if st == "ok" and not t:
            t, st = first_capture_after("www." + a, ymd); time.sleep(1.5)
        c.execute("INSERT OR REPLACE INTO wayback VALUES(?,?,?,?,?)",
                  (a, fc[:10], t, st, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        c.commit()
        if i % 25 == 0: print(f"  [WB] {i}/{len(todo)}", flush=True)
    print("[WB] 完了", flush=True)


def report():
    c = sqlite3.connect(DB, timeout=180)
    d = lambda x: dt.date.fromisoformat(x[:10])
    w = lambda t: dt.date(int(t[:4]), int(t[4:6]), int(t[6:8]))
    tg = {r[0]: r for r in targets(c)}
    rows = [(a, tg[a][1], tg[a][2], t, st) for a, t, st in c.execute("SELECT apex, first_ts, status FROM wayback") if a in tg]
    ok = [r for r in rows if r[4] == "ok"]; cap = [r for r in ok if r[3]]
    print(f"新規ローンチ帯 {len(tg)}件  照会済 {len(ok)}件  初回証明書日以降の記録あり {len(cap)}件 ({100*len(cap)/max(1,len(ok)):.1f}%)")
    if not cap: return
    c2w = [(w(r[3]) - d(r[2])).days for r in cap]
    w2h = [(d(r[1]) - w(r[3])).days for r in cap]
    q = lambda L: S.quantiles(L, n=4) if len(L) >= 4 else [0, 0, 0]
    print(f"初回証明書→初回記録 中央値 {S.median(c2w):.0f}日 (p25 {q(c2w)[0]:.0f}, p75 {q(c2w)[2]:.0f})  同日 {sum(x==0 for x in c2w)}件")
    print(f"初回記録が告知より前 {sum(x>0 for x in w2h)}件 ({100*sum(x>0 for x in w2h)/len(cap):.0f}%)  同日 {sum(x==0 for x in w2h)}件"
          f"  後 {sum(x<0 for x in w2h)}件   初回記録→告知 中央値 {S.median(w2h):.0f}日")
    nocap = len(ok) - len(cap)
    print(f"告知まで（照会時点まで）記録なし {nocap}件")


if __name__ == "__main__":
    run() if (len(sys.argv) > 1 and sys.argv[1] == "run") else report()
