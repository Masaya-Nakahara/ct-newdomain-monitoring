# -*- coding: utf-8 -*-
"""主軸B（Show HN 過去24か月）の RDAP 登録日による先行性の集計。

  python ct_b_rdap_report.py

crt.sh が不調で初回証明書日が取れない場合でも，登録日→公開告知の日数は全件で求められる。
初回証明書日が取れたものは，登録日→初回証明書日の差も示す（登録とほぼ同時に証明書を取得するかの確認）。
"""
import sqlite3, statistics as S, datetime as dt, math
from collections import Counter

c = sqlite3.connect("ct_data.db", timeout=120)
d = lambda x: dt.date.fromisoformat(x[:10])


def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; dd = 1 + z * z / n; ce = (p + z * z / (2 * n)) / dd
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dd
    return max(0, ce - h), min(1, ce + h)


def q(L):
    L = sorted(L)
    if len(L) < 4: return f"n={len(L)}"
    a = S.quantiles(L, n=4)
    return f"n={len(L)}  中央値={S.median(L):.0f}日 (p25={a[0]:.0f}, p75={a[2]:.0f})"


rows = c.execute("""SELECT b.apex, b.month, b.hn_date, r.reg_date, r.status, s.status, l.first_cert_date
    FROM b_plan b LEFT JOIN rdap r ON r.apex=b.apex
    LEFT JOIN scale_sample s ON s.apex=b.apex AND s.kind='B'
    LEFT JOIN leadtime l ON l.apex=b.apex""").fetchall()
n = len(rows)
st = Counter(r[4] or "未取得" for r in rows)
print(f"==== 主軸B（Show HN 24か月）RDAP ====\n計画 {n}件  RDAP状態 {dict(st)}")
ok = [r for r in rows if r[3]]
print(f"登録日あり {len(ok)}件 ({100*len(ok)/n:.1f}%)  TLD上位 {Counter(r[0].split('.')[-1] for r in ok).most_common(6)}")
print(f"登録日なしのTLD上位 {Counter(r[0].split('.')[-1] for r in rows if not r[3]).most_common(8)}")

lead = [(d(r[2]) - d(r[3])).days for r in ok]
neg = sum(1 for x in lead if x < 0)
print(f"\n登録→告知: 全体 {q(lead)}  負（告知が登録より前）={neg}件")
band = [x for x in lead if 0 <= x <= 365]
k = len(band); lo, hi = wilson(k, len(lead))
print(f"新規ドメインでのローンチ（登録から365日以内に告知）: {k}/{len(lead)} = {100*k/len(lead):.1f}% (CI {100*lo:.1f}-{100*hi:.1f})")
print(f"  その登録→告知: {q(band)}  30日以上={100*sum(x>=30 for x in band)/k:.0f}%  90日以上={100*sum(x>=90 for x in band)/k:.0f}%"
      f"  0-1日={sum(x<=1 for x in band)}件  7日以内={sum(x<=7 for x in band)}件")
for th in (30, 60, 120):
    kk = sum(1 for x in lead if 0 <= x <= th)
    print(f"  登録から{th}日以内に告知: {kk}件 ({100*kk/len(lead):.1f}%)")

both = [r for r in ok if r[6]]
gap = [(d(r[6]) - d(r[3])).days for r in both]
if gap:
    print(f"\n初回証明書日もある {len(both)}件: 登録→初回証明書 {q(gap)}  7日以内={100*sum(0<=g<=7 for g in gap)/len(gap):.0f}%"
          f"  証明書が登録より前={sum(g<0 for g in gap)}件")
    bb = [r for r in both if 0 <= (d(r[2]) - d(r[3])).days <= 365]
    g2 = [(d(r[6]) - d(r[3])).days for r in bb]
    if g2: print(f"  うち新規ドメインでのローンチ {len(bb)}件: 登録→初回証明書 {q(g2)}  7日以内={100*sum(0<=g<=7 for g in g2)/len(g2):.0f}%")

print("\n月別（登録日あり / 新規ドメインでのローンチ割合 / 登録→告知の中央値）")
for m in sorted({r[1] for r in rows}):
    sub = [(d(r[2]) - d(r[3])).days for r in ok if r[1] == m]
    bd = [x for x in sub if 0 <= x <= 365]
    if sub: print(f"  {m}: {len(sub):3d}件  {100*len(bd)/len(sub):5.1f}%  {S.median(bd) if bd else '-'}")
