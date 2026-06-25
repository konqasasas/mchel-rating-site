# Mchel Rating Site — GitHub Pages公開用

このフォルダは、そのままGitHub DesktopでGitHubにアップロードし、GitHub Pagesで無料公開するための公開用プロジェクトです。

## 重要：公開するフォルダ

公開ページは `docs/` フォルダに入っています。
GitHub Pagesでは、`main` ブランチの `/docs` を公開元に選んでください。

- `docs/index.html`：総合レートランキング
- `docs/about.html`：レートの説明
- `docs/players/<UUID>.html`：プレイヤー個人ページ

## GitHub Pagesの設定

1. GitHubでこのフォルダ全体を含むリポジトリを作成してpushします。
2. リポジトリの **Settings → Pages** を開きます。
3. **Build and deployment** の **Source** を **Deploy from a branch** にします。
4. Branchを **main**、Folderを **/docs** にします。
5. **Save** を押します。
6. 数分後に表示されるURLを開きます。

## 今後、レートCSVを更新してサイトを作り直す場合

Python 3.10以上で以下のように実行します。

```bash
python build_site.py \
  --overall-csv rating_output/overall_ranking.csv \
  --components-csv rating_output/best30_components.csv \
  --metadata-json rating_output/rating_metadata.json \
  --snapshot-summary mchel_1_12_2_run_summary_excluding_seasonal.json \
  --out-dir docs
```

`docs/` の内容が更新されるので、GitHub Desktopで変更をCommitしてPushすれば、公開サイトも更新されます。

## フォルダ構成

```text
.
├─ docs/                # 公開されるHTMLサイト（GitHub Pagesの公開元）
├─ static/              # テンプレート生成に使うCSS/JavaScript
├─ examples/            # このサイト生成に使ったサンプル出力
├─ build_site.py        # CSVからdocs/を自動生成するスクリプト
└─ .gitignore
```

## 注意

- GitHubのPublicリポジトリに置いたファイルは誰でも閲覧できます。秘密情報・パスワード・APIキーは絶対に入れないでください。
- このサイトは公開ランキングAPIを元にした非公式・ファンメイドのサイトです。
