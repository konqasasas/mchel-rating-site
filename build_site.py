#!/usr/bin/env python3
"""Build a static MCHel 1.12.2 overall-rating website.

Inputs are outputs produced by mchel_best30_rating.py. The generated site is
plain HTML/CSS/JavaScript, so it can be published on GitHub Pages or any
static host.

Example:
  python build_site.py \
      --overall-csv rating_output/overall_ranking.csv \
      --components-csv rating_output/best30_components.csv \
      --metadata-json rating_output/rating_metadata.json \
      --snapshot-summary mchel_1_12_2_run_summary_excluding_seasonal.json \
      --out-dir docs
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

JST = timezone(timedelta(hours=9), name="JST")
SITE_TITLE = "MCHel 1.12.2 アスレチック総合レート"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build static MCHel rating pages.")
    parser.add_argument("--overall-csv", type=Path, required=True)
    parser.add_argument("--components-csv", type=Path, required=True)
    parser.add_argument("--metadata-json", type=Path, required=True)
    parser.add_argument("--snapshot-summary", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("docs"))
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def float_value(value: str | None, fallback: float = 0.0) -> float:
    try:
        return float(value or fallback)
    except (TypeError, ValueError):
        return fallback


def int_value(value: str | None, fallback: int = 0) -> int:
    try:
        return int(float(value or fallback))
    except (TypeError, ValueError):
        return fallback


def format_time(ms: int) -> str:
    minutes, remainder = divmod(ms, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes}:{seconds:02d}.{millis:03d}"


def format_jst(value: str | None) -> str:
    if not value:
        return "取得日時不明"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(JST).strftime("%Y年%-m月%-d日 %H:%M JST")
    except ValueError:
        return escape(value)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def nav(current: str = "") -> str:
    def link(href: str, label: str, key: str) -> str:
        cls = "nav-link active" if current == key else "nav-link"
        return f'<a class="{cls}" href="{href}">{label}</a>'
    return f"""
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="index.html" aria-label="トップへ">
      <span class="brand-mark">M</span>
      <span><strong>MCHel Rating</strong><small>1.12.2 Athletic</small></span>
    </a>
    <nav class="main-nav" aria-label="メインナビゲーション">
      {link('index.html', '総合ランキング', 'ranking')}
      {link('about.html', 'レートについて', 'about')}
    </nav>
  </div>
</header>
"""


def footer(updated: str) -> str:
    return f"""
<footer class="site-footer">
  <div class="footer-inner">
    <p>非公式・ファンメイドの集計サイトです。記録データは公開ランキングAPIをもとに集計しています。</p>
    <p>最終データ取得：<time>{escape(updated)}</time></p>
  </div>
</footer>
"""


def page_shell(title: str, body: str, updated: str, current: str = "", depth: int = 0, description: str = "") -> str:
    prefix = "../" * depth
    # Rewrite navigation paths and static paths for player pages.
    header = nav(current).replace('href="index.html"', f'href="{prefix}index.html"').replace('href="about.html"', f'href="{prefix}about.html"')
    desc = description or "MCHel 1.12.2アスレチックの公開タイムアタックランキングから算出した総合レート。"
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{escape(desc)}">
  <meta name="robots" content="index,follow">
  <title>{escape(title)} | {SITE_TITLE}</title>
  <link rel="stylesheet" href="{prefix}static/style.css">
  <script defer src="{prefix}static/app.js"></script>
</head>
<body>
{header}
<main>
{body}
</main>
{footer(updated)}
</body>
</html>
"""


def tier_badge(tier: str) -> str:
    return f'<span class="tier tier-{escape(tier.lower())}">{escape(tier)}</span>'


def build_index(overall: list[dict[str, str]], metadata: dict[str, Any], updated: str) -> str:
    player_count = len(overall)
    course_count = metadata.get("eligible_course_count", "—")
    top_rating = float_value(overall[0]["published_rating"]) if overall else 0
    rows = []
    for player in overall:
        uuid = player["player_uuid"]
        rank = int_value(player["overall_rank"])
        rating = float_value(player["published_rating"])
        rows.append(f"""
<tr data-player-row data-name="{escape(player['player_name']).lower()}" data-tier="{escape(player['tier'])}" data-rank="{rank}">
  <td class="rank-cell">{rank}</td>
  <td class="player-cell"><a href="players/{escape(uuid)}.html">{escape(player['player_name'])}</a></td>
  <td>{tier_badge(player['tier'])}</td>
  <td class="rating-cell">{rating:,.2f}</td>
  <td>{int_value(player['eligible_course_count'])}</td>
  <td>{float_value(player['best_course_score']):.3f}</td>
  <td>{float_value(player['thirtieth_course_score']):.3f}</td>
</tr>""")
    tier_options = "".join(f'<option value="{tier}">{tier}</option>' for tier in ["SSS", "SS", "S", "A", "B", "C", "D"])
    body = f"""
<section class="hero">
  <div class="container hero-grid">
    <div>
      <p class="eyebrow">MINECRAFT JAPAN COMMUNITY SERVER</p>
      <h1>アスレチック<br><span>総合レートランキング</span></h1>
      <p class="hero-copy">1.12.2の公開タイムアタック記録をもとに、プレイヤーごとの上位30コースを評価した非公式総合ランキングです。</p>
      <div class="hero-actions"><a class="button primary" href="#ranking">ランキングを見る</a><a class="button ghost" href="about.html">算出方法</a></div>
    </div>
    <aside class="hero-card" aria-label="集計概要">
      <p class="card-kicker">CURRENT SNAPSHOT</p>
      <div class="stat-grid">
        <div><strong>{player_count}</strong><span>公式対象プレイヤー</span></div>
        <div><strong>{course_count}</strong><span>対象コース</span></div>
        <div><strong>{top_rating:,.0f}</strong><span>最高レート</span></div>
        <div><strong>30</strong><span>採用記録数</span></div>
      </div>
      <p class="small-muted">更新：{escape(updated)}</p>
    </aside>
  </div>
</section>
<section class="container section" id="ranking">
  <div class="section-heading">
    <div>
      <p class="eyebrow">LEADERBOARD</p>
      <h2>総合ランキング</h2>
      <p>プレイヤー名をクリックすると、採用されたBest 30の詳細を見られます。</p>
    </div>
    <span id="visible-count" class="result-count">{player_count} 人表示中</span>
  </div>
  <div class="filter-bar" aria-label="ランキング検索">
    <label class="search-field"><span class="sr-only">プレイヤー名で検索</span><input id="player-search" type="search" placeholder="プレイヤー名で検索"></label>
    <label class="select-field"><span class="sr-only">Tierで絞り込み</span><select id="tier-filter"><option value="">全Tier</option>{tier_options}</select></label>
    <button id="clear-filters" class="button small" type="button">絞り込みを解除</button>
  </div>
  <div class="table-wrap">
    <table class="ranking-table">
      <thead><tr><th>順位</th><th>プレイヤー</th><th>Tier</th><th>レート</th><th>対象コース<br>ランクイン数</th><th>最高<br>コーススコア</th><th>Best 30<br>最低スコア</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <p class="table-note">対象コースは、取得時点で上位100件が埋まっているコースです。ランキング外は未挑戦か101位以下かを判別できないため、減点には使用していません。</p>
</section>
"""
    return page_shell("総合ランキング", body, updated, current="ranking")


def build_player_page(player: dict[str, str], components: list[dict[str, str]], updated: str) -> str:
    rank = int_value(player["overall_rank"])
    rating = float_value(player["published_rating"])
    p_index = float_value(player["raw_performance_index"])
    all_count = int_value(player["eligible_course_count"])
    records = []
    for comp in sorted(components, key=lambda c: int_value(c["best30_position"])):
        course = comp["course_name"]
        source = "https://api.mchel.net/v1/athletic/" + quote(course, safe="") + "/ranking"
        records.append(f"""
<tr>
  <td>{int_value(comp['best30_position'])}</td>
  <td><a href="{escape(source)}" target="_blank" rel="noopener noreferrer">{escape(course)}</a></td>
  <td>{format_time(int_value(comp['time_ms']))}</td>
  <td>{format_time(int_value(comp['course_record_time_ms']))}</td>
  <td>{float_value(comp['course_score']):.3f}</td>
  <td>{float_value(comp['weight']):.3f}</td>
  <td>{float_value(comp['weighted_score']):.3f}</td>
</tr>""")
    uuid = player["player_uuid"]
    body = f"""
<section class="player-hero">
  <div class="container">
    <a class="back-link" href="../index.html">← 総合ランキングへ戻る</a>
    <div class="player-heading">
      <div>
        <p class="eyebrow">PLAYER PROFILE</p>
        <h1>{escape(player['player_name'])}</h1>
        <p class="uuid">UUID: <code>{escape(uuid)}</code></p>
      </div>
      <div class="hero-rating">
        {tier_badge(player['tier'])}
        <strong>{rating:,.2f}</strong>
        <span>総合レート</span>
      </div>
    </div>
    <div class="profile-stats">
      <div><span>総合順位</span><strong>#{rank}</strong></div>
      <div><span>対象コース<br>ランクイン数</span><strong>{all_count}</strong></div>
      <div><span>最高コーススコア</span><strong>{float_value(player['best_course_score']):.3f}</strong></div>
      <div><span>Best 30最低スコア</span><strong>{float_value(player['thirtieth_course_score']):.3f}</strong></div>
      <div><span>素点 P</span><strong>{p_index:.4f}</strong></div>
    </div>
  </div>
</section>
<section class="container section">
  <div class="section-heading compact">
    <div>
      <p class="eyebrow">BEST 30 COMPONENTS</p>
      <h2>レートに採用された30コース</h2>
      <p>コーススコアは、そのコースの最速タイムを100としたタイム比率です。各コース名は元の公開ランキングAPIにリンクしています。</p>
    </div>
  </div>
  <div class="table-wrap">
    <table class="components-table">
      <thead><tr><th>採用順</th><th>コース</th><th>本人タイム</th><th>最速タイム</th><th>コース<br>スコア</th><th>重み</th><th>重み付き<br>スコア</th></tr></thead>
      <tbody>{''.join(records)}</tbody>
    </table>
  </div>
  <div class="info-panel">
    <h3>このレートについて</h3>
    <p>このページの総合レートは、対象コースでのランクイン数が30件以上のプレイヤーだけに表示されます。詳細な計算条件は<a href="../about.html">レートについて</a>をご覧ください。</p>
  </div>
</section>
"""
    return page_shell(f"{player['player_name']} の個人ページ", body, updated, current="", depth=1, description=f"{player['player_name']} のMCHel 1.12.2アスレチック総合レートとBest 30記録。")


def build_about(metadata: dict[str, Any], updated: str) -> str:
    course_count = metadata.get("eligible_course_count", "—")
    best_n = metadata.get("best_n", 30)
    player_rule = metadata.get("official_player_rule", f"eligible_course_count >= {best_n}")
    tiers = metadata.get("tier_counts", {})
    tier_list = ''.join(f'<li><span>{escape(k)}</span><strong>{v}人</strong></li>' for k, v in tiers.items())
    body = f"""
<section class="subhero"><div class="container"><p class="eyebrow">METHODOLOGY</p><h1>レートについて</h1><p>公開タイムアタックランキングを、コース間で比較できる総合レートへ変換するためのルールです。</p></div></section>
<section class="container section prose">
  <div class="method-grid">
    <article class="method-card"><span>01</span><h2>対象コース</h2><p>取得時点でランキングが上位100件まで埋まっているコースだけを対象にします。現在の対象は <strong>{course_count}コース</strong> です。</p></article>
    <article class="method-card"><span>02</span><h2>コーススコア</h2><p>各コースの最速タイムを100点とし、<code>100 × 最速タイム ÷ プレイヤータイム</code> で評価します。タイムが速いほど100に近づきます。</p></article>
    <article class="method-card"><span>03</span><h2>Best {best_n}</h2><p>対象コースへのランクインが {best_n}件以上のプレイヤーが総合順位の対象です。各人の最も高い {best_n}コースを使用します。</p></article>
    <article class="method-card"><span>04</span><h2>重み付き平均</h2><p>Best {best_n}の上位側を少し重くしつつ、30位の記録も評価に反映します。重みは1位=1.00、30位=0.50です。</p></article>
    <article class="method-card"><span>05</span><h2>公開レート</h2><p>重み付き平均の素点Pを有限soft-logitで変換し、100〜1200の公開レートにします。P=100は1200です。</p></article>
    <article class="method-card"><span>06</span><h2>日次更新</h2><p>今後は1日1回のスナップショットを取得して再計算します。記録更新やコース最速タイムの更新によりレートは変動します。</p></article>
  </div>
  <h2>Tier</h2>
  <div class="tier-explainer"><div><b>SSS</b><span>1200.00</span></div><div><b>SS</b><span>1100.00–1199.99</span></div><div><b>S</b><span>1000.00–1099.99</span></div><div><b>A</b><span>900.00–999.99</span></div><div><b>B</b><span>800.00–899.99</span></div><div><b>C</b><span>700.00–799.99</span></div><div><b>D</b><span>100.00–699.99</span></div></div>
  <p class="small-muted">現在の対象者内訳：{escape(', '.join(f'{k} {v}人' for k, v in tiers.items()))}</p>
  <h2>重要な注意</h2>
  <ul>
    <li>データは各コースの公開ランキング上位100件のみです。ランキング外は未挑戦と101位以下を区別できないため、未掲載を減点には使いません。</li>
    <li>期間限定アスレチックは対象外です。</li>
    <li>これは非公式・ファンメイドの集計です。元データの正確性、ゲーム内の公式評価、プレイヤーの総合的な実力を保証するものではありません。</li>
    <li>現在の参加条件：<code>{escape(player_rule)}</code>。</li>
  </ul>
  <p class="updated-callout">現在のデータ取得日時：{escape(updated)}</p>
</section>
"""
    return page_shell("レートについて", body, updated, current="about")


def copy_static_assets(project_root: Path, out_dir: Path) -> None:
    static_src = project_root / "static"
    static_dest = out_dir / "static"
    if static_dest.exists():
        shutil.rmtree(static_dest)
    shutil.copytree(static_src, static_dest)


def main() -> int:
    args = parse_args()
    for path in [args.overall_csv, args.components_csv, args.metadata_json]:
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
    overall = read_csv(args.overall_csv)
    components = read_csv(args.components_csv)
    metadata = json.loads(args.metadata_json.read_text(encoding="utf-8"))
    snapshot: dict[str, Any] = {}
    if args.snapshot_summary and args.snapshot_summary.exists():
        snapshot = json.loads(args.snapshot_summary.read_text(encoding="utf-8"))
    updated = format_jst(snapshot.get("source_run_fetched_at_utc") or snapshot.get("fetched_at_utc"))

    components_by_uuid: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in components:
        components_by_uuid[row["player_uuid"]].append(row)

    out = args.out_dir
    if out.exists():
        shutil.rmtree(out)
    (out / "players").mkdir(parents=True, exist_ok=True)
    copy_static_assets(Path(__file__).resolve().parent, out)

    write_text(out / "index.html", build_index(overall, metadata, updated))
    write_text(out / "about.html", build_about(metadata, updated))
    for player in overall:
        uuid = player["player_uuid"]
        page = build_player_page(player, components_by_uuid.get(uuid, []), updated)
        write_text(out / "players" / f"{uuid}.html", page)

    # Machine-readable metadata for future use.
    site_manifest = {
        "title": SITE_TITLE,
        "updated": updated,
        "player_count": len(overall),
        "player_page_count": len(overall),
        "rating_metadata": metadata,
    }
    write_text(out / "site_manifest.json", json.dumps(site_manifest, ensure_ascii=False, indent=2))
    write_text(out / ".nojekyll", "")
    print(f"Built {len(overall)} player pages in: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
