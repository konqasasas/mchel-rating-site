# CHELCY SPEEDRUN RATING

Chelcy 1.12.2 の公開アスレチックランキングを使った、非公式の総合レーティングサイトです。

## 公開ページ

GitHub Pages の `docs/` を公開します。

- Overall Ranking
- Player Profile
- Courses
- About Rating

## Rating v1

- 対象: Chelcy 1.12.2
- 期間限定アスレチックは除外
- 100件埋まっているコースを対象
- `Raw Score = 100 × #1 Time / PB`
- Difficulty は 0–1、月初の定期更新時だけ再計算
- `Adjusted Score = Raw Score × (1 + 0.10 × Difficulty)`
- Adjusted Score 上位30件を重み付き平均
- 個人ページでは上位40件まで表示

### Grade

| Grade | Raw Score |
|---|---:|
| SS | 100.000（1位タイムと同タイム） |
| S+ | 99.500+ |
| S | 99.000+ |
| A+ | 98.000+ |
| A | 96.500+ |
| B+ | 95.000+ |
| B | 92.500+ |
| C+ | 90.000+ |
| C | 90.000未満 |

### Tier

| Rating | Tier |
|---:|---|
| 1300+ | Grandmaster |
| 1250+ | Master I |
| 1200+ | Master II |
| 1150+ | Diamond I |
| 1100+ | Diamond II |
| 1050+ | Platinum I |
| 1000+ | Platinum II |
| 950+ | Gold I |
| 900+ | Gold II |
| 850+ | Silver I |
| 800+ | Silver II |
| 750+ | Bronze I |
| 700+ | Bronze II |
| under 700 | Iron |

## 更新

GitHub Actions は毎日 09:17 JST に実行されます。

- 通常日: 前回の Difficulty 表を固定してRatingだけ更新
- 毎月1日: 同じ時刻の取得データから Difficulty 表を更新
- Rating履歴: 日ごとの `overall_ranking.csv` のみ保存
- Difficulty履歴: 月次更新時だけ保存
- Activity: 直近180日を JSONL で保存
- プレイヤーの内部識別子は UUID を使用
