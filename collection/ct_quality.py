"""品質判定の共通ロジック(収集フィルタ＆タグ付けで共用)。依存はreのみ。"""
import re

# 乱用されやすい安価TLD(目安)
SPAM_TLDS = {"top", "xyz", "shop", "click", "lat", "sbs", "cfd", "icu", "cyou", "bond",
             "quest", "rest", "fun", "online", "site", "store", "live", "autos", "boats",
             "makeup", "hair", "skin", "monster", "beauty", "bid", "loan", "work", "kim",
             "men", "mom", "lol", "pics", "wiki"}

# 明確な共有インフラ/ワイルドカードDNS/開発基盤(=サービスではない)。収集時に除外。
EXTRA_SHARED = {"sslip.io", "nip.io", "aws.dev", "sfdc.net", "a2z.org.cn",
                "stripe-terminal-local-reader.net", "quickconnect.to", "ui.direct",
                "workdaysuv.com", "it.com", "medellin.onl", "amazonaws.com",
                "azurewebsites.net", "cloudfront.net", "googleusercontent.com"}

RND = re.compile(r"^[a-z0-9]{10,}$")


def is_infra_host(host):
    """深いサブドメイン(>=5ラベル)=テナント/インフラの可能性大。"""
    return bool(host) and host.count(".") >= 4


def looks_random(label):
    s = label.replace("-", "")
    if len(s) < 6:
        return False
    vowels = sum(c in "aeiou" for c in s)
    digits = sum(c.isdigit() for c in s)
    if len(s) >= 8 and vowels / len(s) < 0.18:
        return True
    if len(s) >= 8 and digits / len(s) > 0.4:
        return True
    if re.match(r"^[a-z0-9]{14,}$", label):
        return True
    return False


def should_skip(apex, host, shared=frozenset()):
    """収集時に完全除外(=明らかにサービスでない)。"""
    label = apex.split(".")[0]
    return (apex in shared or apex in EXTRA_SHARED
            or RND.match(label) is not None
            or is_infra_host(host))


def is_suspect(apex, tld, host):
    """グレー(本物のサービスでない可能性)。削除せずフラグのみ。"""
    label = apex.split(".")[0]
    return bool(looks_random(label) or label.count("-") >= 3 or len(label) >= 22
                or apex.startswith("xn--") or ".xn--" in apex
                or is_infra_host(host) or tld in SPAM_TLDS)
