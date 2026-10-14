"""
first-cert 基準の新規性 (IEEE TNSM) -- crt.sh から各ドメインの「史上初の証明書」日を取得。
これは crt.sh 側の履歴に依存するため、こちらの収集稼働と無関係に後から正確に計算できる。

  python ct_firstcert.py sample [N]   直近clean seen の N 件(既定145)の first-cert を取得しキャッシュ、
                                      first_seen との差(lead/lag)分布と「真に新規」割合を集計。
                                      分類ラベル(me_labels.csv)があればカテゴリ別も出す。

新規性の定義:
  first_cert_date … そのapexを含む証明書の最小 not_before(= 事実上の誕生日)
  gap_days       … first_seen_date - first_cert_date  (我々が捕捉するまでの遅れ)
  born_in_window … first_cert_date >= 収集開始日(2026-06-16) なら観測期間内に誕生=真に新規
出力: ct_data.db の firstcert(apex, first_cert_date, first_cert_ts, n_certs, checked_at)
"""
import sqlite3, sys, csv, datetime as dt, json, time
from concurrent.futures import ThreadPoolExecutor
import requests

DB = "ct_data.db"
WINDOW_START = "2026-06-16"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (research service-measurement; academic)"})


def _norm(s):
    return (s or "").strip().lower().lstrip("*.")


def first_cert(apex):
    """crt.sh JSON から apex(および *.apex)を含む証明書の最小 not_before/entry_timestamp を返す。"""
    url = f"https://crt.sh/?q={apex}&output=json"
    data = None
    for attempt in range(9):
        try:
            r = S.get(url, timeout=35)
            if r.status_code != 200 or not r.text.strip():
                time.sleep(3.0 * (attempt + 1)); continue   # 502等はバックオフして再試行
            data = r.json()
            break
        except Exception:
            time.sleep(3.0 * (attempt + 1))
    if data is None:
        return None, None, 0
    nb_min = None; ts_min = None; n = 0
    for e in data:
        names = set()
        for f in (e.get("common_name"), e.get("name_value")):
            for part in (f or "").split("\n"):
                names.add(_norm(part))
        # apex そのもの、または apex のサブドメインのみ採用
        if not any(nm == apex or nm.endswith("." + apex) for nm in names):
            continue
        n += 1
        nb = e.get("not_before"); ts = e.get("entry_timestamp")
        if nb and (nb_min is None or nb < nb_min):
            nb_min = nb
        if ts and (ts_min is None or ts < ts_min):
            ts_min = ts
    return (nb_min[:10] if nb_min else None), ts_min, n


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "sample"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 145
    c = sqlite3.connect(DB, timeout=120)
    c.execute("PRAGMA busy_timeout=120000")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS firstcert(
        apex TEXT PRIMARY KEY, first_cert_date TEXT, first_cert_ts TEXT,
        n_certs INT, checked_at TEXT)""")
    rows = c.execute("SELECT apex, first_seen_date FROM seen WHERE COALESCE(suspect,0)=0 "
                     "ORDER BY first_seen_ts DESC LIMIT ?", (n,)).fetchall()
    # 成功(first_cert_date 非NULL)のみキャッシュ扱い。失敗は再試行する。
    cached = {a for (a,) in c.execute("SELECT apex FROM firstcert WHERE first_cert_date IS NOT NULL")}
    todo = [r for r in rows if r[0] not in cached]
    print(f"[firstcert] 対象 {len(rows)} / 未取得 {len(todo)} を crt.sh から取得 ...", flush=True)

    def work(row):
        apex, _ = row
        d, ts, k = first_cert(apex)
        return apex, d, ts, k

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    done = 0
    with ThreadPoolExecutor(max_workers=1) as ex:   # 逐次: crt.shに優しく、成功率優先
        for apex, d, ts, k in ex.map(work, todo):
            c.execute("INSERT OR REPLACE INTO firstcert VALUES(?,?,?,?,?)", (apex, d, ts, k, now))
            c.commit()   # 1行ごとにコミット=ロック保持を最小化(並行ジョブと共存)
            done += 1
            if done % 25 == 0:
                print(f"  ... {done}/{len(todo)}", flush=True)

    # 集計
    fc = {a: (d, ts, k) for a, d, ts, k in c.execute("SELECT apex,first_cert_date,first_cert_ts,n_certs FROM firstcert")}
    fs = {a: fsd for a, fsd in c.execute("SELECT apex, first_seen_date FROM seen")}
    c.close()

    # ラベル(任意)
    ref = {}
    try:
        ref = {r["apex"]: r["ref"] for r in csv.DictReader(open("me_labels.csv", encoding="utf-8"))}
    except Exception:
        pass

    def dd(s):
        try: return dt.date.fromisoformat(s)
        except Exception: return None

    universe = [r[0] for r in rows]
    got = [a for a in universe if a in fc and fc[a][0]]
    miss = [a for a in universe if a not in got]
    gaps = []; born = 0; aged = 0
    per_cat = {}
    for a in got:
        fcd = dd(fc[a][0]); fsd = dd(fs.get(a))
        if not (fcd and fsd):
            continue
        gap = (fsd - fcd).days
        gaps.append(gap)
        is_born = fc[a][0] >= WINDOW_START
        if is_born: born += 1
        else: aged += 1
        if ref:
            cat = ref.get(a, "?")
            per_cat.setdefault(cat, {"born": 0, "aged": 0})
            per_cat[cat]["born" if is_born else "aged"] += 1

    gaps.sort()
    def pct(p): return gaps[min(len(gaps)-1, int(p*len(gaps)))] if gaps else 0
    print("\n==== first-cert 新規性 集計 ====")
    print(f"取得成功 {len(got)} / 対象 {len(universe)}  (未取得/証明書なし {len(miss)})")
    if gaps:
        print(f"gap_days = first_seen - first_cert (捕捉の遅れ): "
              f"median={gaps[len(gaps)//2]}  p10={pct(0.1)}  p90={pct(0.9)}  min={gaps[0]} max={gaps[-1]}")
        neg = sum(1 for g in gaps if g < 0)
        print(f"  gap<0 (証明書より前に観測=異常/タイムゾーン境界): {neg}")
        within7 = sum(1 for g in gaps if 0 <= g <= 7)
        within30 = sum(1 for g in gaps if 0 <= g <= 30)
        print(f"  0<=gap<=7日: {within7} ({100*within7/len(gaps):.0f}%)   0<=gap<=30日: {within30} ({100*within30/len(gaps):.0f}%)")
    print(f"\n真に新規(first_cert >= {WINDOW_START}, 観測期間内に誕生): {born} ({100*born/max(1,born+aged):.0f}%)")
    print(f"既存/再利用(first_cert < 開始日): {aged} ({100*aged/max(1,born+aged):.0f}%)")
    if per_cat:
        print("\n-- カテゴリ別 (born=真に新規 / aged=既存) --")
        print(f"{'cat':<10}{'born':>6}{'aged':>6}{'born率':>8}")
        for cat in sorted(per_cat, key=lambda k: -(per_cat[k]['born']+per_cat[k]['aged'])):
            b = per_cat[cat]["born"]; ag = per_cat[cat]["aged"]; t = b+ag
            print(f"{cat:<10}{b:>6}{ag:>6}{100*b/max(1,t):>7.0f}%")


if __name__ == "__main__":
    main()
