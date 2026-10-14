# -*- coding: utf-8 -*-
"""主軸C: 全件流量を複数の時間帯で再測し、1日あたり流量と被覆率を安定化する。

  python ct_yield_multi.py [WINDOW_MIN]   取り込み完了帯（30〜114時間前）の8時点で WINDOW_MIN 分ずつ全件取得

単一の10分窓からの外挿は時間帯による流量変動の影響を受けるため、時刻をずらした複数窓の平均と
ばらつきを報告する。結果は fullwindow テーブルに追記する。
"""
import sys, time, statistics, datetime as dt, sqlite3
from ct_yield import fetch_window, apex, SHARED, RND, DB

SAMPLE = 50000
OFFSETS = [30, 42, 54, 66, 78, 90, 102, 114]   # 12時間刻み=時刻帯と曜日を分散


def measure(minutes, off):
    rows, t_start, t_end = fetch_window(minutes, offset_hours=off)
    if not rows:
        return None
    span = (t_start - t_end).total_seconds() / 60.0
    apexes = {}
    for ts, cn in rows:
        if not cn: continue
        ap, host = apex(cn)
        if ap: apexes.setdefault(ap, set()).add(host)
    clean = sum(1 for ap, hs in apexes.items()
                if ap not in SHARED and len(hs) < 8 and not RND.match(ap.split(".")[0]))
    f = 60 * 24 / span
    return dict(off=off, start=t_start, end=t_end, span=span, n=len(rows),
                daily=len(rows) * f, apex=len(apexes) * f, clean=clean * f)


def main():
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    offsets = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else OFFSETS
    res = []
    for off in offsets:
        print(f"[C] offset={off}h から {minutes}分 を全件取得 ...", flush=True)
        try:
            r = measure(minutes, off)
        except Exception as ex:
            print(f"    失敗: {repr(ex)[:80]}", flush=True); r = None
        if not r:
            continue
        cov = 100 * min(1, SAMPLE / r["daily"])
        res.append(r)
        print(f"    {r['end']}〜{r['start']} ({r['span']:.1f}分) 証明書{r['n']} → {r['daily']:,.0f}/日"
              f"  apex {r['apex']:,.0f}/日  clean {r['clean']:,.0f}/日  被覆 {cov:.2f}%", flush=True)
        try:
            c = sqlite3.connect(DB, timeout=180); c.execute("PRAGMA busy_timeout=180000")
            c.execute("""CREATE TABLE IF NOT EXISTS fullwindow(
                win_start TEXT, win_end TEXT, minutes REAL, n_certs INT, n_apex INT,
                n_clean_apex INT, daily_certs INT, coverage_pct REAL, at TEXT)""")
            c.execute("INSERT INTO fullwindow VALUES(?,?,?,?,?,?,?,?,?)",
                      (str(r["start"]), str(r["end"]), r["span"], r["n"], int(r["apex"]), int(r["clean"]),
                       int(r["daily"]), cov, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
            c.commit(); c.close()
        except Exception as ex:
            print(f"    (DB保存スキップ {repr(ex)[:50]})")
        time.sleep(5)
    if res:
        d = [r["daily"] for r in res]; a = [r["apex"] for r in res]; cl = [r["clean"] for r in res]
        sd = statistics.stdev(d) if len(d) > 1 else 0
        print("\n==== 主軸C 集計（複数窓） ====")
        print(f"  窓数={len(res)}  1日あたり証明書 平均{statistics.mean(d):,.0f} (SD {sd:,.0f}, 範囲 {min(d):,.0f}〜{max(d):,.0f})")
        print(f"  ユニークapex/日 平均{statistics.mean(a):,.0f}  品質フィルタ後/日 平均{statistics.mean(cl):,.0f}")
        covs = [100 * SAMPLE / x for x in d]
        print(f"  被覆率 平均{statistics.mean(covs):.2f}% (範囲 {min(covs):.2f}〜{max(covs):.2f}%)")
        p = SAMPLE / statistics.mean(d)
        for k in (1, 3, 5):
            print(f"  捕捉確率 k={k}: {100*(1-(1-p)**k):.1f}%")


if __name__ == "__main__":
    main()
