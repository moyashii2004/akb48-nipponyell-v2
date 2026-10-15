# AKB48 × ニッポンエール 個人用トラッカー（無料版）

## 現状と限界
- **完成済み**: GitHub Actionsの6時間ごとの巡回、登録済み公式Webページのリンク抽出、重複排除、変更履歴（新規リンク）、静的検索画面。
- **未完成**: 47都道府県すべてのJA公式サイトURL登録、記事本文からの出演者名・会場・日時の正確な抽出、X・Instagramの無償で安定した自動収集、本人認証付きホスティング。
- Bluesky連携はオプションで初期無効。**JA公式アカウントのハンドルを検証した場合のみ** `sources.json` の `allowed_handles` に登録して有効化してください。検索APIの仕様や認証要件は変わります。
- X/Instagramは公式APIの無料・安定取得を保証できないため未実装。無許可のログイン回避・アクセス制限回避は行いません。
- `robots.txt` で拒否されたページはスキップします。サイトの利用規約と負荷に配慮してください。
- 発見したURLは「候補」であり、出演者が確定したイベントではありません。

## GitHubへの登録（重要）
`collector.py`、`index.html`、`sources.json`、`requirements.txt`、`data/`、`.github/` を **リポジトリ直下** に置きます。ZIP内の親フォルダごとアップロードしないでください。

## 実行
Actions > Update AKB48 Event Data > Run workflow。Pythonをローカル実行するなら `pip install -r requirements.txt && python collector.py`。

## 監視先の増やし方
`sources.json` に `{"type":"website","url":"https://JA公式のニュース一覧URL","enabled":true}` を追加。**実在する公式URLを検証してから**登録してください。県本部トップだけでは地域JAのニュースを網羅しません。

## 閲覧
`index.html` は `data/*.json` と同じルートでHTTP配信してください。GitHubのPrivateリポジトリだけではWeb公開時のアクセス制限になりません。GitHub Pagesでの公開は認証を伴わない場合があるため、自分専用にするならローカル起動、または認証付きの別ホスティングを選んでください。
