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

### 画面構成

```
┌──────────────────────────────────────────────┐
│  請求書 OCR デモ（タイトル）                  │
├──────────────────────────────────────────────┤
│  [PDF をアップロード]                         │
├──────────────────────────────────────────────┤
│  ▼ アップロード後に表示                       │
│                                              │
│  [ 抽出結果 ]  [ 可視化 ]  ← タブ切り替え   │
│                                              │
│  【抽出結果タブ】                            │
│  請求元: 株式会社〇〇  請求先: △△           │
│  請求書番号: INV-001   請求日: 2025-02-19    │
│  合計金額: ¥110,000    支払期限: 2025-03-31  │
│                                              │
│  【明細】                                    │
│  ┌──────────┬────┬──────┬───┐               │
│  │ 品目     │数量│ 単価 │金額│               │
│  └──────────┴────┴──────┴───┘               │
│                                              │
│  [生データ（JSON）▼]                         │
│                                              │
│  【可視化タブ】                              │
│  ┌───────────────────┬────────────────────┐  │
│  │ PDF画像           │ 凡例               │  │
│  │ （ボックス付き）   │ ■緑  請求元        │  │
│  │                   │ ■青  請求先        │  │
│  │                   │ ■赤  金額          │  │
│  │                   │ ■橙  日付          │  │
│  │                   │ ■紫  請求書番号    │  │
│  └───────────────────┴────────────────────┘  │
└──────────────────────────────────────────────┘
```

### 実装方針

| 要素 | 使用する Streamlit コンポーネント |
|------|----------------------------------|
| ファイルアップロード | `st.file_uploader(type=["pdf"])` |
| タブ切り替え | `st.tabs(["抽出結果", "可視化"])` |
| 主要フィールド表示 | `st.metric` を 3 列グリッド |
| 明細テーブル | `st.dataframe` |
| 生 JSON | `st.expander` + `st.json` |
| 可視化画像 | `st.image`（Pillow で描画した PIL Image） |
| 凡例 | `st.dataframe` または `st.markdown` |
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
