"""
CT precision test (Step2) -- IEEE TNSM research
ファイアホースから「新興サービス」をどれだけの精度で拾えるか(H2)。

1. crt.sh Postgres から直近ログをサンプル取得
2. apex抽出→共有インフラ/マルチテナント除去(データ駆動)
3. RDAP登録日で「新規(<=120日)」候補を抽出
4. ホームページ取得で稼働サービスか判定(precision proxy)
ファネルと的中率を出力。
"""
import json, datetime as dt, urllib.parse, re
from concurrent.futures import ThreadPoolExecutor
import pg8000.native as pg
import requests, tldextract

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (research-measurement)"})

SHARED = {
    "github.io","vercel.app","netlify.app","herokuapp.com","replit.app","replit.dev",
    "streamlit.app","notion.site","substack.com","web.app","firebaseapp.com","pages.dev",
    "fly.dev","render.com","glitch.me","quickconnect.to","ui.direct","workdaysuv.com",
    "amazonaws.com","cloudfront.net","azurewebsites.net","googleusercontent.com",
    "myshopify.com","wixsite.com","weebly.com","wordpress.com","blogspot.com",
    "sharepoint.com","salesforce.com","zendesk.com","netcraze.io","direct.quickconnect.to",
    "cdn.cloudflare.net","akamaihd.net","cloudflaressl.com","sendgrid.net","mail.ru",
}
PARK_KW = re.compile(r"(domain (is )?for sale|parked|buy this domain|godaddy|coming soon|"
                     r"default web page|under construction|sedo|namecheap parking)", re.I)
RND = re.compile(r"^[a-z0-9]{10,}$")  # ランダム文字列っぽいラベル


def fetch_recent(total=6000, batch=1500, max_fail=8):
    """最新ログから total 件を『時刻カーソル降順』でページング。
    OFFSET不使用=後半が重くならない。バッチ失敗は同カーソルで粘る=堅牢
    (大きな total でも途中で打ち切られにくい)。"""
    import time as _t
    out, cursor, fails = [], None, 0
    while len(out) < total:
        where = "" if cursor is None else f"WHERE e.ENTRY_TIMESTAMP < '{cursor}'"
        q = (f"SELECT e.ENTRY_TIMESTAMP, x509_commonName(c.CERTIFICATE) "
             f"FROM ct_log_entry e JOIN certificate c ON c.ID = e.CERTIFICATE_ID "
             f"{where} ORDER BY e.ENTRY_TIMESTAMP DESC LIMIT {batch}")
        rows = None
        for attempt in range(4):
            try:
                con = pg.Connection(user="guest", host="crt.sh", port=5432,
                                    database="certwatch", timeout=60)
                con.run("SET statement_timeout = 60000")
                rows = con.run(q)
                con.close()
                break
            except Exception as ex:
                print(f"    retry {attempt+1}: {repr(ex)[:70]}", flush=True)
                _t.sleep(3 + attempt * 3)
        if rows is None:                       # バッチ全リトライ失敗
            fails += 1
            if fails >= max_fail:
                print(f"    失敗{fails}回到達、ここまでで返す({len(out)}件)", flush=True)
                break
            _t.sleep(5)
            continue                           # 同じcursorで粘る
        if not rows:
            break
        for ts, cn in rows:
            if cn:
                out.append(cn)
        cursor = str(rows[-1][0])               # 降順の末尾=最小ts
        print(f"    取得 {len(out)} ...", flush=True)
        if len(rows) < batch:                   # もう無い
            break
        _t.sleep(0.5)                           # crt.shへの配慮
    return out


def apex(host):
    host = (host or "").lower().strip().lstrip("*.")
    ext = tldextract.extract(host)
    if not ext.domain or not ext.suffix:
        return None, None
    return f"{ext.domain}.{ext.suffix}", host


def rdap_created(domain):
    try:
        r = S.get(f"https://rdap.org/domain/{domain}", timeout=15, allow_redirects=True)
        if r.status_code == 200:
            for ev in r.json().get("events", []):
                if ev.get("eventAction") == "registration":
                    return ev.get("eventDate", "")[:10]
    except Exception:
        pass
    return None


def homepage_live(domain):
    """(is_live, title) -- 稼働している実サービスのランディングか。"""
    for scheme in ("https://", "http://"):
        try:
            r = S.get(scheme + domain, timeout=8, allow_redirects=True)
            if r.status_code >= 400:
                continue
            html = r.text[:20000]
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            title = (m.group(1).strip()[:80] if m else "")
            if PARK_KW.search(html):
                return False, "PARKED:" + title
            if len(html) < 400 and not title:
                return False, "empty"
            return True, title or "(no title)"
        except Exception:
            continue
    return False, "unreachable"


def main():
    today = dt.date(2026, 6, 15)
    print("[1] crt.sh から直近ログ取得 ...", flush=True)
    cns = fetch_recent(7500)
    print(f"    生エントリ(CN): {len(cns)}", flush=True)

    # apex集計 + サブドメイン多様性(マルチテナント検出)
    by_apex = {}
    for cn in cns:
        ap, host = apex(cn)
        if not ap:
            continue
        by_apex.setdefault(ap, set()).add(host)
    print(f"[2] ユニークapex: {len(by_apex)}", flush=True)

    # フィルタ: 共有インフラ / マルチテナント(サブドメイン>=8) / ランダム名
    cand = []
    for ap, hosts in by_apex.items():
        if ap in SHARED:
            continue
        if len(hosts) >= 8:          # 短時間に多数のサブドメイン = 共有/テナント基盤
            continue
        label = ap.split(".")[0]
        if RND.match(label):         # 明らかに自動生成のランダム名
            continue
        cand.append(ap)
    print(f"[3] 共有インフラ等を除去後の候補apex: {len(cand)}", flush=True)

    cand = cand[:700]  # RDAP負荷上限
    print(f"[4] RDAP登録日を照会 (上限{len(cand)}) ...", flush=True)
    with ThreadPoolExecutor(max_workers=24) as ex:
        regs = list(ex.map(rdap_created, cand))

    # RDAP網羅率 & 登録経過日数ヒストグラム
    rdap_ok = sum(1 for r in regs if r)
    hist = {"<=120":0, "121-365":0, "366-1095":0, ">1095":0}
    new_doms = []
    for ap, rg in zip(cand, regs):
        if not rg:
            continue
        try:
            age = (today - dt.date.fromisoformat(rg)).days
        except Exception:
            continue
        if age <= 120: hist["<=120"]+=1
        elif age <= 365: hist["121-365"]+=1
        elif age <= 1095: hist["366-1095"]+=1
        else: hist[">1095"]+=1
        if 0 <= age <= 120:
            new_doms.append((ap, rg, age))
    print(f"    RDAP網羅率: {rdap_ok}/{len(cand)} = {100*rdap_ok/max(1,len(cand)):.0f}%", flush=True)
    print(f"    登録経過日数: {hist}", flush=True)
    print(f"[5] 新規登録(<=120日)候補: {len(new_doms)}", flush=True)

    print(f"[6] ホームページ稼働判定 ...", flush=True)
    with ThreadPoolExecutor(max_workers=20) as ex:
        live = list(ex.map(lambda x: homepage_live(x[0]), new_doms))

    detected = []
    for (ap, rg, age), (is_live, title) in zip(new_doms, live):
        detected.append({"domain": ap, "registered": rg, "age_days": age,
                         "live": is_live, "title": title})
    hits = [d for d in detected if d["live"]]

    json.dump(detected, open("ct_precision_results.json", "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)

    print("\n==== ファネル ====")
    print(f"生エントリ        : {len(cns)}")
    print(f"ユニークapex      : {len(by_apex)}")
    print(f"インフラ除去後候補: {len(cand)}")
    print(f"新規登録(<=120d)  : {len(new_doms)}")
    print(f"稼働サービス(hit) : {len(hits)}")
    if new_doms:
        print(f"\nprecision(稼働/新規) = {len(hits)}/{len(detected)} = {100*len(hits)/len(detected):.0f}%")
    print("\n-- 検出された新興サービス例 --")
    for d in sorted(hits, key=lambda x: x["age_days"])[:20]:
        print(f"  {d['domain']:<26} 登録{d['registered']} ({d['age_days']:>3}d)  {d['title']}")
    print("\n詳細 -> ct_precision_results.json")


if __name__ == "__main__":
    main()
