# デモアプリ 設計ドキュメント

## 概要

Azure Document Intelligence の `prebuilt-invoice` モデルを使い、請求書 PDF から構造化データを抽出する Streamlit デモアプリ。

---

## ディレクトリ構成

```
MHSC-Azure-AI-OCR/
├── app.py              # Streamlit エントリーポイント
├── ocr/
│   └── analyzer.py     # Azure Document Intelligence 呼び出しロジック
├── docs/
│   ├── design.md               # このファイル
│   └── azure_doc_intelligence_guide.md
├── データ/             # サンプル PDF
├── requirements.txt
├── .env                # APIキー（git管理外）
└── .env.example
```

---

## データフロー

```
ユーザー
  │ PDF をアップロード
  ▼
app.py（Streamlit UI）
  │ ファイルのバイト列を渡す
  ▼
ocr/analyzer.py
  │ Azure Document Intelligence API を呼び出す
  ▼
Azure（クラウド）
  │ AnalyzeResult（JSON）を返す
  ▼
ocr/analyzer.py
  │ 必要なフィールドだけを dict に整形して返す
  ▼
app.py（Streamlit UI）
  │ 結果を画面に表示
  ▼
ユーザー
```

---

## ocr/analyzer.py 設計

### 責務

- Azure Document Intelligence クライアントの生成
- PDF バイト列を受け取り、OCR を実行
- 結果を画面表示用の辞書形式に整形して返す

### 公開インターフェース

```python
def analyze_invoice(pdf_bytes: bytes) -> dict:
    """
    PDF のバイト列を受け取り、請求書フィールドを抽出して返す。

    Returns:
        {
            "vendor_name":    str | None,
            "customer_name":  str | None,
            "invoice_id":     str | None,
            "invoice_date":   str | None,
            "due_date":       str | None,
            "invoice_total":  str | None,
            "sub_total":      str | None,
            "total_tax":      str | None,
            "amount_due":     str | None,
            "items": [
                {
                    "description": str | None,
                    "quantity":    str | None,
                    "unit_price":  str | None,
                    "amount":      str | None,
                },
                ...
            ],
            "raw_fields": dict   # Azure API の生レスポンス（デバッグ用）
        }
    """
```

### 内部処理の流れ

1. 環境変数からエンドポイント・キーを読み込む
2. `DocumentIntelligenceClient` を生成
3. `begin_analyze_document("prebuilt-invoice", body=pdf_bytes, ...)` を呼び出す
4. `poller.result()` で結果を取得
5. `result.documents[0].fields` から各フィールドの `content` を取り出す
6. 整形した dict を返す

### エラーハンドリング方針

- 認証エラー（401）・エンドポイントエラー（404）は例外をそのまま呼び出し元へ伝播させる
- フィールドが存在しない場合は `None` を返す（`if field:` で対処済みのため例外にしない）

---

## app.py 設計

### 画面構成（共通レイアウト）

各タブは左右分割。左に PDF 可視化、右にフィールドパネル。

```
┌──────────────────────────────────────────────────────────────┐
│  請求書 OCR デモ                                              │
├──────────────────────────────────────────────────────────────┤
│  [PDF をアップロード]                                         │
├──────────────────────────────────────────────────────────────┤
│  [ Tab1 ]  [ Tab2 ]  [ Tab3 ]                               │
│  ┌─────────────────────────┬──────────────────────────────┐  │
│  │ PDF + ボックス（左60%）  │ フィールドパネル（右40%）    │  │
│  │                         │                              │  │
│  │  ■■□□  ← 各タブで     │  請求元: 〇〇  ✅ 95%        │  │
│  │  □■□□    異なる色・    │  合計金額: ¥110,000 ⚠ 72%  │  │
│  │  □□■□    スタイルの     │  ...                         │  │
│  │          ボックスを表示  │  Invoice vs Layout 比較      │  │
│  └─────────────────────────┴──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### タブ構成（3タブ）

| タブ名 | PDF ボックス | フィールドパネル |
|--------|-------------|----------------|
| **Invoice + 信頼度** | フィールド単位の塗りつぶしボックス（色 = フィールド種別） | 信頼度バッジ付きフィールド・明細・生JSON |
| **Layout + 正規表現** | 行単位の枠線のみボックス（薄グレー＝レイアウトが見ているもの） | 正規表現抽出フィールド（信頼度N/A）・生テキスト |
| **統合（Invoice + Layout）** | 両方を重ねて表示（Invoice塗り = 色、Layout枠線 = グレー） | Invoice vs Layout フィールド比較（一致 ✓ / 不一致 ≠） |

### 信頼度バッジの色分け

| 信頼度 | バッジ | 意味 |
|--------|--------|------|
| ≥ 0.90 | ✅ 緑 | 高精度 |
| 0.70〜0.89 | ⚠️ 橙 | 要確認 |
| < 0.70 | ❌ 赤 | 低精度・手動確認推奨 |

### バウンディングボックスのスタイル

| ソース | スタイル | 意味 |
|--------|---------|------|
| Invoice（フィールド単位） | 半透明塗り + 色付き枠線 | モデルが「このフィールド」と判断した範囲 |
| Layout（行単位） | 枠線のみ（薄グレー） | モデルが認識した全テキスト行 |

`_render_page()` は各ボックスの `"style"` キーで描画を切り替える：
- `"filled"` → `draw.polygon(pts, fill=rgb+(50,), outline=rgb+(230,))`
- `"outline"` → `draw.polygon(pts, outline=(130,130,130,150))`

### 並列 API 呼び出し

```
PDF アップロード
  ├─→ [Thread 1] prebuilt-invoice → invoice_result（フィールドボックス）
  └─→ [Thread 2] prebuilt-layout → layout_result（行ボックス + 正規表現）
          ↓ 両方完了後
      3タブを描画
```

`concurrent.futures.ThreadPoolExecutor` で2つの API 呼び出しを並列実行し、待ち時間を削減する。

### 実装方針

| 要素 | 使用する Streamlit コンポーネント |
|------|----------------------------------|
| ファイルアップロード | `st.file_uploader(type=["pdf"])` |
| タブ切り替え | `st.tabs([...])` × 5 |
| 主要フィールド表示（基本/regex） | `st.metric` を 3 列グリッド |
| 主要フィールド表示（信頼度付き） | `st.markdown` + HTML カラーバッジ |
| 明細テーブル | `st.dataframe` |
| 比較テーブル | `st.dataframe`（pandas DataFrame） |
| 生 JSON | `st.expander` + `st.json` |
| 可視化画像 | `st.image`（Pillow で描画した PIL Image） |
| 凡例 | `st.markdown` |
| エラー表示 | `st.error` |
| 処理中スピナー | `st.spinner` |

### 可視化の仕組み

```
PDF バイト列
  ├─→ pymupdf (fitz) で各ページを画像に変換（DPI=150）
  │      page_image = fitz.open(stream=pdf_bytes)[i].get_pixmap(matrix)
  │
  └─→ analyze_invoice() の bounding_boxes からポリゴン座標を取得
        polygon = [x1_inch, y1_inch, x2_inch, ...]
        │
        └─→ Pillow で半透明ボックスを描画
              pixel = inch * DPI
              draw.polygon(pts, fill=(r,g,b,40), outline=(r,g,b,220))
```

### カラースキーム

| 色 | 対象フィールド |
|----|---------------|
| 緑 | VendorName（請求元） |
| 青 | CustomerName（請求先） |
| 紫 | InvoiceId（請求書番号） |
| 橙 | InvoiceDate / DueDate（日付） |
| 赤 | InvoiceTotal / SubTotal / TotalTax / AmountDue（金額） |

---

## 実装順序

1. `ocr/analyzer.py` を実装・単体動作確認（CLI）
2. `app.py` を実装・ブラウザで動作確認
3. `README.md` にセットアップ手順を記載
