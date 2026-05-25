# ocr/analyzer.py 解説ガイド（初学者向け）

## このファイルの役割

`ocr/analyzer.py` は Azure Document Intelligence API を呼び出して、OCR（文字認識）の実行と結果の整形を担当するモジュールです。

`app.py`（画面）からは直接 Azure の API を触りません。代わりにこのファイルの関数を呼び出します。役割分担のイメージ：

```
app.py（画面）  →  analyzer.py（API 呼び出し＆整形）  →  Azure（クラウド）
```

---

## 定数・設定値

### 信頼度の閾値

```python
CONFIDENCE_HIGH = 0.90
CONFIDENCE_MID  = 0.70
```

OCR で読み取った文字がどれだけ「正しそうか」を表すスコア（0.0〜1.0）の閾値です。

| 値 | 意味 | 画面上の表示 |
|----|------|------------|
| 0.90 以上 | 高精度（ほぼ確実） | 緑 ✓ |
| 0.70〜0.89 | 要確認 | 橙 ⚠ |
| 0.70 未満 | 低精度 | 赤 ✗ |

### FIELD_CONFIG

```python
FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "VendorName":   ("請求元",     "green"),
    "CustomerName": ("請求先",     "blue"),
    ...
}
```

**キー（"VendorName" など）**: Azure API が返すフィールド名。英語で固定されています。  
**値のタプル**: `(日本語ラベル, 色名)`

バウンディングボックスを画面上に描画するときに「この枠は何色で、何という名前で表示するか」を決めるための辞書です。

### DISPLAY_FIELDS

```python
DISPLAY_FIELDS: list[tuple[str, str]] = [
    ("vendor_name", "請求元"),
    ("customer_name", "請求先"),
    ...
]
```

画面の右パネルに表示するフィールドの一覧と順序です。`("内部キー", "日本語ラベル")` のペアのリストです。

`FIELD_CONFIG` との違い：

| 定数 | キー | 用途 |
|------|------|------|
| `FIELD_CONFIG` | Azure API のキー（英語）| バウンディングボックスの描画設定 |
| `DISPLAY_FIELDS` | 内部キー（snake_case）| 画面パネルの表示順序 |

### _REGEX_PATTERNS

```python
_REGEX_PATTERNS: dict[str, list[str]] = {
    "vendor_name": [
        r'([^\s　\n]*(?:株式会社|有限会社|...)[^\s　\n]*)',
    ],
    ...
}
```

`analyze_layout_regex()` 関数で使う正規表現パターンです。Layout モデルは「全文テキスト」しか返さないので、そこからフィールドを切り出すために正規表現を使います。

- 各フィールドに複数パターンを定義できます（リスト）
- 上から順に試して、最初にマッチしたものを採用します

### _FIELD_MAP

```python
_FIELD_MAP: dict[str, str] = {
    "VendorName":   "vendor_name",
    "CustomerName": "customer_name",
    ...
}
```

Azure API のキー名（英語）を内部キー名（snake_case）に変換するための辞書です。`analyze_invoice()` の中でのみ使います。

---

## 関数

### `_make_client()`

```python
def _make_client() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient(
        endpoint=os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"],
        credential=AzureKeyCredential(os.environ["AZURE_DOCUMENT_INTELLIGENCE_KEY"]),
    )
```

**役割**: Azure に接続するためのクライアントオブジェクトを生成して返します。

**処理の流れ**:
1. `.env` ファイルから読み込んだ環境変数（エンドポイント URL・API キー）を取得
2. それを使って `DocumentIntelligenceClient` を作成して返す

**なぜ関数にするか**: `analyze_invoice()` と `analyze_layout_regex()` の両方で同じ接続処理が必要になるため、重複を避けて共通化しています。

> **注意**: 環境変数が設定されていない場合は `KeyError` が発生します。`.env` ファイルが正しく設定されているかを先に確認してください。

---

### `_empty_result()`

```python
def _empty_result() -> dict:
    out = {k: {"value": None, "confidence": None} for k, _ in DISPLAY_FIELDS}
    out.update({"items": [], "bounding_boxes": [], "raw_fields": {}})
    return out
```

**役割**: 全フィールドが空（None）の「結果テンプレート」を返します。

**返す辞書のイメージ**:

```python
{
    "vendor_name":   {"value": None, "confidence": None},
    "customer_name": {"value": None, "confidence": None},
    # ... 全フィールド分
    "items":          [],
    "bounding_boxes": [],
    "raw_fields":     {},
}
```

**なぜ必要か**: API が何も返さなかった場合（`result.documents` が空のとき）でも、呼び出し元（`app.py`）が「フィールドが存在しない」エラーを起こさないようにするためです。後から `out["vendor_name"] = ...` のように上書きしていきます。

> **辞書内包表記の読み方**: `{k: {"value": None, "confidence": None} for k, _ in DISPLAY_FIELDS}` は「`DISPLAY_FIELDS` の各要素からキー名 `k` を取り出して、値が `{"value": None, "confidence": None}` の辞書を作る」という意味です。

---

### `analyze_invoice(pdf_bytes: bytes) -> dict`

**役割**: `prebuilt-invoice` モデルで PDF を分析し、請求書フィールドと信頼度スコアを返します。

#### 全体の流れ

```
PDF バイト列
  ↓
Azure API 呼び出し（prebuilt-invoice）
  ↓
結果を受け取る（result.documents[0].fields）
  ↓
各フィールドの値・信頼度を取り出す
  ↓
バウンディングボックス情報を取り出す
  ↓
明細行（Items）を取り出す
  ↓
整形した辞書を返す
```

#### コードの詳細解説

**① API 呼び出し**

```python
client = _make_client()
poller = client.begin_analyze_document(
    "prebuilt-invoice",
    body=BytesIO(pdf_bytes),
    content_type="application/octet-stream",
    locale="ja-JP",
)
result = poller.result()
```

- `begin_analyze_document()` は処理開始を指示するだけで、すぐ戻ってきます（非同期）
- `poller.result()` で Azure の処理完了を待ち、結果を受け取ります（ここで数秒待つ）
- `locale="ja-JP"` を指定することで日本語の認識精度が上がります

**② フィールドの取り出し**

```python
for api_key, out_key in _FIELD_MAP.items():
    f = fields.get(api_key)
    if f:
        out[out_key] = {
            "value":      f.get("content"),
            "confidence": f.get("confidence"),
        }
```

- `_FIELD_MAP` を使って Azure のキー名（`"VendorName"`）→ 内部キー名（`"vendor_name"`）に変換
- `f.get("content")` が OCR で読み取った文字列
- `f.get("confidence")` が信頼度スコア（0.0〜1.0）
- `if f:` でフィールドが存在しない場合をスキップ（存在しないフィールドは None のまま）

**③ バウンディングボックスの取り出し**

```python
for api_key, (label, color) in FIELD_CONFIG.items():
    f = fields.get(api_key)
    if not f:
        continue
    for region in f.get("boundingRegions") or []:
        out["bounding_boxes"].append({
            "label":   label,
            "value":   f.get("content", ""),
            "page":    region.get("pageNumber", 1),
            "polygon": region.get("polygon", []),
            "color":   color,
        })
```

- `boundingRegions` は「このフィールドが PDF 上のどの位置にあるか」を表す情報
- `polygon` は座標のリスト（インチ単位）: `[x1, y1, x2, y2, x3, y3, x4, y4]`
- 複数ページにまたがるフィールドは複数の region を持つことがあるため `for` でループ

**④ 明細行の取り出し**

```python
items_field = fields.get("Items")
if items_field:
    for item in items_field.get("valueArray") or []:
        obj = item.get("valueObject") or {}
        out["items"].append({
            "品目": (obj.get("Description") or {}).get("content"),
            "数量": (obj.get("Quantity")    or {}).get("content"),
            ...
        })
```

- `Items` フィールドは配列（複数の明細行）を含む特殊なフィールド
- `valueArray` → 各行、`valueObject` → 各行のフィールド、という構造になっています

---

### `analyze_layout_regex(pdf_bytes: bytes) -> dict`

**役割**: `prebuilt-layout` モデルで全文テキストと行単位ボックスを取得し、正規表現でフィールドを抽出します。

#### `prebuilt-invoice` との違い

| 項目 | prebuilt-invoice | prebuilt-layout |
|------|-----------------|-----------------|
| モデルの種類 | 請求書専用 | 汎用レイアウト認識 |
| フィールド抽出 | 自動（AIが判断） | 正規表現で自前抽出 |
| 信頼度スコア | あり | なし |
| 用途 | 精度重視 | 比較・検証用 |

#### コードの詳細解説

**① 全文テキストの取得**

```python
full_text = result.content or ""
out["raw_fields"] = {"full_text": full_text}
```

`prebuilt-layout` は `result.content` に PDF 全体のテキストを格納します。

**② 正規表現でフィールドを抽出**

```python
for field_key, patterns in _REGEX_PATTERNS.items():
    for pattern in patterns:
        m = re.search(pattern, full_text, re.MULTILINE)
        if m:
            out[field_key] = {"value": m.group(1).strip(), "confidence": None}
            break
```

- `_REGEX_PATTERNS` に定義されたパターンを順番に試す
- `re.search()` で全文テキストを検索し、最初にマッチしたパターンを採用
- `m.group(1)` でキャプチャグループ（`(...)` で囲んだ部分）の文字列を取得
- `break` で最初のマッチで打ち切る（残りのパターンは試さない）

**③ 行単位のバウンディングボックスを取得**

```python
for page in (result.pages or []):
    page_num = page.page_number if hasattr(page, "page_number") else 1
    for line in (page.lines or []):
        polygon = line.polygon if hasattr(line, "polygon") else []
        content = line.content if hasattr(line, "content") else ""
        out["bounding_boxes"].append({
            "label":   content[:20],
            "value":   content,
            "page":    page_num,
            "polygon": polygon or [],
            "color":   "layout",
            "style":   "outline",
        })
```

- `result.pages` は全ページのリスト
- `page.lines` は各ページの「行」のリスト（モデルが認識した1行ずつ）
- `line.polygon` は行の矩形座標（インチ単位）
- `line.content` は行のテキスト
- `hasattr()` でプロパティの有無を確認（SDK のバージョン差異に対応するための防御的コード）
- `"style": "outline"` は `app.py` で「枠線のみで描画」を意味する

---

### `run_all_patterns(pdf_bytes: bytes) -> dict`

```python
def run_all_patterns(pdf_bytes: bytes) -> dict:
    return {
        "invoice":      analyze_invoice(pdf_bytes),
        "layout_regex": analyze_layout_regex(pdf_bytes),
    }
```

**役割**: 2つの分析を辞書にまとめて返すだけのラッパー関数です。

**現在の使われ方**: `app.py` では `ThreadPoolExecutor` で並列実行しているため、この関数は直接使っていません（並列化のために `analyze_invoice` と `analyze_layout_regex` を個別に呼び出しています）。CLI での簡易テスト用として残しています。

---

## 返り値の全体構造

`analyze_invoice()` と `analyze_layout_regex()` はどちらも同じ構造の辞書を返します：

```python
{
    # 各フィールド（9種類）
    "vendor_name":   {"value": "株式会社〇〇", "confidence": 0.95},  # or None
    "customer_name": {"value": "△△株式会社",  "confidence": 0.88},
    "invoice_id":    {"value": "INV-001",      "confidence": None},  # layout は常に None
    # ... 他6フィールド

    # 明細行（invoice のみ。layout は常に []）
    "items": [
        {"品目": "システム開発費", "数量": "1", "単価": "¥100,000", "金額": "¥100,000"},
    ],

    # バウンディングボックス（可視化用）
    "bounding_boxes": [
        {
            "label":   "請求元",         # フィールド名 or 行テキストの先頭20文字
            "value":   "株式会社〇〇",   # ホバー表示テキスト
            "page":    1,                # ページ番号（1始まり）
            "polygon": [x1,y1,x2,y2,...],# 座標（インチ単位）
            "color":   "green",          # 色名
            "style":   "filled",         # "filled" or "outline"
        },
    ],

    # 生データ（デバッグ用）
    "raw_fields": {
        # invoice: {"VendorName": {...}, ...}  Azure API の生レスポンス
        # layout:  {"full_text": "..."}        全文テキスト
    },
}
```

---

## よく使われる Python の書き方

### `f.get("content")` vs `f["content"]`

| 書き方 | キーが存在しない場合 |
|--------|------------------|
| `f["content"]` | `KeyError` 例外が発生 |
| `f.get("content")` | `None` を返す（エラーにならない） |

API レスポンスにフィールドが含まれない場合があるため、`get()` を使っています。

### `(obj.get("Description") or {}).get("content")`

`obj.get("Description")` が `None` を返す可能性がある場合、`.get("content")` を連鎖させるには `None.get()` を防ぐ必要があります。`or {}` は「`None` なら空辞書を使う」という意味です。

### `hasattr(page, "page_number")`

オブジェクトが特定の属性を持っているかを確認します。SDK のバージョン間で属性名が変わることに備えた防御的なコードです。
