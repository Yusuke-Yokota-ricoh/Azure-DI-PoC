# Azure Document Intelligence 入門ガイド

> このガイドは、Azure Document Intelligence を初めて使う方向けの教育用資料です。
> このプロジェクトでの利用（請求書 OCR デモ）を念頭に置いて書いています。

---

## 1. Azure Document Intelligence とは

Azure Document Intelligence（旧名: Azure Form Recognizer）は、PDF や画像からテキストや構造化データを自動抽出する Azure のマネージドサービスです。

### 何ができるか

- 請求書・領収書・身分証明書などから項目を自動抽出
- 手書き・印刷・写真スキャンなど様々な品質のドキュメントに対応
- 抽出結果を JSON 形式で返却（アプリへの組み込みが容易）
- 日本語を含む 27 言語に対応（`prebuilt-invoice` の場合）

### 従来の OCR との違い

| 項目 | 従来の OCR | Azure Document Intelligence |
|------|-----------|------------------------------|
| 出力 | 文字列のみ | 構造化データ（フィールド名 + 値 + 信頼度） |
| 使いやすさ | 後処理が必要 | そのまま使える JSON |
| 対応フォーマット | 制限あり | PDF / JPEG / PNG / TIFF / BMP |

---

## 2. 主要モデル一覧

Azure Document Intelligence には複数のモデルがあります。用途に合わせて選択します。

| モデル ID | 用途 | このデモでの使用 |
|-----------|------|-----------------|
| `prebuilt-invoice` | 請求書・発注書・公共料金明細 | **使用する** |
| `prebuilt-read` | 汎用テキスト抽出（OCR のみ） | 参考 |
| `prebuilt-layout` | テキスト＋表＋レイアウト構造の抽出 | 参考 |
| `prebuilt-receipt` | レシート（小売・飲食） | 参考 |
| カスタムモデル | 自社独自フォームへの対応 | 対象外 |

---

## 3. prebuilt-invoice モデルが抽出するフィールド

`prebuilt-invoice` は以下のフィールドを自動認識します。日本語請求書でも多くのフィールドが抽出可能です。

### ドキュメント全体フィールド

| フィールド名 | 内容 | 例 |
|-------------|------|----|
| `VendorName` | 請求元会社名 | 株式会社〇〇 |
| `VendorAddress` | 請求元住所 | 東京都千代田区... |
| `VendorAddressRecipient` | 請求元担当者名 | 山田 太郎 |
| `CustomerName` | 請求先会社名 | △△株式会社 |
| `CustomerId` | 顧客 ID | C-12345 |
| `CustomerAddress` | 請求先住所 | 大阪府大阪市... |
| `InvoiceId` | 請求書番号 | INV-2025-001 |
| `InvoiceDate` | 請求日 | 2025-02-19 |
| `DueDate` | 支払期限 | 2025-03-31 |
| `PurchaseOrder` | 発注番号 | PO-98765 |
| `BillingAddress` | 請求先住所（別途） | — |
| `SubTotal` | 小計 | ¥100,000 |
| `TotalTax` | 消費税額 | ¥10,000 |
| `InvoiceTotal` | 合計金額 | ¥110,000 |
| `AmountDue` | 支払残高 | ¥110,000 |
| `ServiceStartDate` | サービス開始日 | 2025-02-01 |
| `ServiceEndDate` | サービス終了日 | 2025-02-28 |

### 明細行フィールド（Items 配列）

| フィールド名 | 内容 |
|-------------|------|
| `Description` | 品目名 |
| `Quantity` | 数量 |
| `Unit` | 単位 |
| `UnitPrice` | 単価 |
| `ProductCode` | 品番 |
| `Amount` | 金額 |
| `Tax` | 税額 |

各フィールドには **confidence（信頼度）** スコア（0.0〜1.0）が付与されます。0.9 以上で高精度と判断するのが目安です。

---

## 4. 料金

> 最新の料金は [Azure 料金ページ](https://azure.microsoft.com/ja-jp/pricing/details/ai-document-intelligence/) を確認してください。

### Free レベル（F0）

- **月 500 ページまで無料**
- デモ・検証用途に最適

### Standard レベル（S0）

| 処理量 | 単価（参考） |
|--------|-------------|
| 〜100 万ページ | $10 / 1,000 ページ |

> このデモ（数十ページ規模）は Free レベルで完結します。

---

## 5. エンドポイントと API キーの確認方法

1. Azure ポータル（https://portal.azure.com）にログイン
2. 作成した Document Intelligence リソースを開く
3. 左メニュー「**キーとエンドポイント**」をクリック
4. 以下の 2 つをコピーして `.env` に設定

```
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<リソース名>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=<キー1 または キー2>
```

---

## 6. Python SDK の使い方

### インストール

```bash
pip install azure-ai-documentintelligence azure-core python-dotenv
```

> **注意:** 旧パッケージ `azure-ai-formrecognizer` は非推奨です。
> 新しい `azure-ai-documentintelligence` を使用してください。

### 基本的なコード構造（ローカルファイルの場合）

```python
import os
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

# 1. 認証情報を環境変数から取得
endpoint = os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"]
key = os.environ["AZURE_DOCUMENT_INTELLIGENCE_KEY"]

# 2. クライアントを作成
client = DocumentIntelligenceClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(key)
)

# 3. ローカル PDF ファイルを読み込んで分析
with open("請求書.pdf", "rb") as f:
    poller = client.begin_analyze_document(
        "prebuilt-invoice",       # 使用するモデル
        body=f,                   # ファイルの内容
        content_type="application/octet-stream",
        locale="ja-JP"            # 日本語ドキュメントの場合
    )

# 4. 結果を取得（同期的に待機）
result = poller.result()

# 5. フィールドを参照
for invoice in result.documents:
    fields = invoice.fields

    vendor = fields.get("VendorName")
    if vendor:
        print(f"請求元: {vendor.get('content')}  信頼度: {vendor.get('confidence'):.2f}")

    total = fields.get("InvoiceTotal")
    if total:
        print(f"合計金額: {total.get('content')}  信頼度: {total.get('confidence'):.2f}")
```

### URL 指定で分析する場合

```python
poller = client.begin_analyze_document(
    "prebuilt-invoice",
    AnalyzeDocumentRequest(url_source="https://example.com/invoice.pdf"),
    locale="ja-JP"
)
```

---

## 7. 結果の JSON 構造

API は以下の 3 セクションを含む JSON を返します。

```
AnalyzeResult
├── content          # ドキュメント全文テキスト
├── pages[]          # ページごとの情報（行・単語・座標）
├── tables[]         # 検出された表
└── documents[]      # モデルが抽出した構造化データ
    └── fields{}     # フィールド名 → 値・信頼度のマップ
```

このデモでは主に `documents[].fields` を使用します。

---

## 8. Document Intelligence Studio（GUI ツール）

コードを書く前に GUI でテスト分析ができます。

1. https://documentintelligence.ai.azure.com/studio を開く
2. 「Invoice」を選択
3. PDF をアップロード → 抽出結果をリアルタイムで確認

商談デモ時の補助ツールとしても使えます。

---

## 9. よくあるエラー

| エラー | 原因 | 対処 |
|--------|------|------|
| `401 Unauthorized` | API キーが間違っている | `.env` の `KEY` を確認 |
| `404 Not Found` | エンドポイント URL が間違っている | 末尾 `/` を含めて確認 |
| `InvalidContent` | PDF が壊れているか暗号化されている | 別のファイルで試す |
| フィールドが `None` | そのフィールドが書類に存在しない | 正常な挙動（`if field:` で対処） |

---

## 参考リンク

- [公式ドキュメント: Document Intelligence 概要](https://learn.microsoft.com/azure/ai-services/document-intelligence/overview)
- [prebuilt-invoice フィールド一覧](https://learn.microsoft.com/azure/ai-services/document-intelligence/prebuilt/invoice)
- [Python SDK クイックスタート](https://learn.microsoft.com/azure/ai-services/document-intelligence/quickstarts/get-started-sdks-rest-api?pivots=programming-language-python)
- [Document Intelligence Studio](https://documentintelligence.ai.azure.com/studio)
- [料金](https://azure.microsoft.com/ja-jp/pricing/details/ai-document-intelligence/)
