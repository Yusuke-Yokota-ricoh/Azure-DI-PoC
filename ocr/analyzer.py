import base64
import json
import os
import re
from io import BytesIO

import fitz
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 信頼度の閾値
CONFIDENCE_HIGH = 0.90
CONFIDENCE_MID  = 0.70

# フィールド名 → (日本語ラベル, 可視化カラー)
FIELD_CONFIG: dict[str, tuple[str, str]] = {
    "VendorName":   ("請求元",     "green"),
    "CustomerName": ("請求先",     "blue"),
    "InvoiceId":    ("請求書番号", "purple"),
    "InvoiceDate":  ("請求日",     "orange"),
    "DueDate":      ("支払期限",   "orange"),
    "InvoiceTotal": ("合計金額",   "red"),
    "SubTotal":     ("小計",       "red"),
    "TotalTax":     ("消費税",     "red"),
    "AmountDue":    ("支払残高",   "red"),
}

# UI 表示順・日本語ラベル
DISPLAY_FIELDS: list[tuple[str, str]] = [
    ("vendor_name",   "請求元"),
    ("customer_name", "請求先"),
    ("invoice_id",    "請求書番号"),
    ("invoice_date",  "請求日"),
    ("due_date",      "支払期限"),
    ("invoice_total", "合計金額"),
    ("sub_total",     "小計"),
    ("total_tax",     "消費税"),
    ("amount_due",    "支払残高"),
]

# 日本語請求書向け正規表現パターン（優先順）
_REGEX_PATTERNS: dict[str, list[str]] = {
    "vendor_name": [
        r'([^\s　\n]*(?:株式会社|有限会社|合同会社|合資会社)[^\s　\n]*)',
    ],
    "customer_name": [
        r'([^\s　\n]*(?:株式会社|有限会社|合同会社)[^\s　\n]*)\s*(?:御中|様)',
        r'(?:請求先|御請求先)\s*[：:]\s*([^\n]+)',
    ],
    "invoice_id": [
        r'(?:請求書番号|No\.|番号)\s*[：:．.]\s*([A-Za-z0-9\-_]+)',
        r'請求書\s*No[．.]\s*([A-Za-z0-9\-]+)',
    ],
    "invoice_date": [
        r'(\d{4}年\s*\d{1,2}月\s*\d{1,2}日)',
        r'(令和\s*\d+年\s*\d{1,2}月\s*\d{1,2}日)',
        r'(\d{4}[/\-]\d{1,2}[/\-]\d{1,2})',
    ],
    "due_date": [
        r'(?:支払期限|お支払期限|振込期限)\s*[：:]\s*([^\n]+)',
    ],
    "invoice_total": [
        r'(?:ご?請求(?:合計)?金額|合計金額|お支払(?:合計)?金額)\s*[：:]?\s*[¥￥]?\s*([\d,]+)',
        r'(?:^|\n)合計\s*[¥￥]?\s*([\d,]+)',
    ],
    "sub_total": [
        r'小計\s*[¥￥]?\s*([\d,]+)',
    ],
    "total_tax": [
        r'(?:消費税|税額)\s*[¥￥]?\s*([\d,]+)',
    ],
    "amount_due": [
        r'(?:ご?請求金額|お支払金額)\s*[¥￥]?\s*([\d,]+)',
    ],
}

_FIELD_MAP: dict[str, str] = {
    "VendorName":   "vendor_name",
    "CustomerName": "customer_name",
    "InvoiceId":    "invoice_id",
    "InvoiceDate":  "invoice_date",
    "DueDate":      "due_date",
    "InvoiceTotal": "invoice_total",
    "SubTotal":     "sub_total",
    "TotalTax":     "total_tax",
    "AmountDue":    "amount_due",
}

_FIELD_COLORS: dict[str, str] = {
    "vendor_name":   "green",
    "customer_name": "blue",
    "invoice_id":    "purple",
    "invoice_date":  "orange",
    "due_date":      "orange",
    "invoice_total": "red",
    "sub_total":     "red",
    "total_tax":     "red",
    "amount_due":    "red",
}

# GPT に渡すフィールド抽出プロンプト（テキスト・画像共通）
_EXTRACTION_PROMPT = """\
請求書のPDF画像から、読み取れるすべての文章・文字を抽出してください。
ヘッダー・本文・表の内容・フッター・印鑑テキストなど、すべての情報を対象にしてください。
各テキストに簡潔な日本語ラベルを付けて items に列挙してください。

また、以下の定義済みフィールドも個別に抽出してください（モデル比較用。値が見つからない場合は null）。

JSONのみを返してください（説明文不要）:
{
  "items": [
    {"label": "ラベル", "value": "読み取ったテキスト"},
    ...
  ],
  "vendor_name": "請求元会社名",
  "customer_name": "請求先会社名（御中・様を除く）",
  "invoice_id": "請求書番号",
  "invoice_date": "請求日",
  "due_date": "支払期限",
  "invoice_total": "合計金額",
  "sub_total": "小計",
  "total_tax": "消費税額",
  "amount_due": "支払残高"
}\
"""


def _make_client() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient(
        endpoint=os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"],
        credential=AzureKeyCredential(os.environ["AZURE_DOCUMENT_INTELLIGENCE_KEY"]),
    )


def _make_openai_client() -> OpenAI:
    return OpenAI(
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_KEY"],
    )


def _empty_result() -> dict:
    out = {k: {"value": None, "confidence": None} for k, _ in DISPLAY_FIELDS}
    out.update({"items": [], "bounding_boxes": [], "raw_fields": {}, "gpt_items": []})
    return out


def _parse_gpt_response(content: str) -> dict:
    out = _empty_result()
    out["raw_fields"] = {"gpt_response": content}
    try:
        data = json.loads(content)
        for key, _ in DISPLAY_FIELDS:
            val = data.get(key)
            if val is not None:
                out[key] = {"value": str(val), "confidence": None}
        raw_items = data.get("items")
        if isinstance(raw_items, list):
            out["gpt_items"] = [
                item for item in raw_items
                if isinstance(item, dict) and item.get("label") and item.get("value")
            ]
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    return out


def analyze_invoice(pdf_bytes: bytes) -> dict:
    """
    prebuilt-invoice モデルで PDF を分析する。

    各フィールドは {"value": str | None, "confidence": float | None} の形式で返す。
    bounding_boxes に可視化用ポリゴン情報を含む。
    """
    client = _make_client()
    poller = client.begin_analyze_document(
        "prebuilt-invoice",
        body=BytesIO(pdf_bytes),
        content_type="application/octet-stream",
        locale="ja-JP",
    )
    result = poller.result()
    out = _empty_result()

    if not result.documents:
        return out

    doc = result.documents[0]
    fields = doc.fields or {}
    out["raw_fields"] = {k: v.as_dict() for k, v in fields.items()}

    for api_key, out_key in _FIELD_MAP.items():
        f = fields.get(api_key)
        if f:
            out[out_key] = {
                "value":      f.get("content"),
                "confidence": f.get("confidence"),
            }

    # バウンディングボックス情報
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

    # 明細行
    items_field = fields.get("Items")
    if items_field:
        for item in items_field.get("valueArray") or []:
            obj = item.get("valueObject") or {}
            out["items"].append({
                "品目": (obj.get("Description") or {}).get("content"),
                "数量": (obj.get("Quantity")    or {}).get("content"),
                "単価": (obj.get("UnitPrice")   or {}).get("content"),
                "金額": (obj.get("Amount")      or {}).get("content"),
            })

    return out


def analyze_layout_regex(pdf_bytes: bytes) -> dict:
    """
    prebuilt-layout でテキストと行ボックスを抽出し、正規表現でフィールドを抽出する。

    bounding_boxes には各ページの行単位ボックスを含む（style="outline"）。
    confidence は常に None（スコアなし）。
    """
    client = _make_client()
    poller = client.begin_analyze_document(
        "prebuilt-layout",
        body=BytesIO(pdf_bytes),
        content_type="application/octet-stream",
        locale="ja-JP",
    )
    result = poller.result()
    out = _empty_result()

    full_text = result.content or ""
    out["raw_fields"] = {"full_text": full_text}

    # 正規表現によるフィールド抽出
    for field_key, patterns in _REGEX_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, full_text, re.MULTILINE)
            if m:
                out[field_key] = {"value": m.group(1).strip(), "confidence": None}
                break

    # 行単位のバウンディングボックス（layout モデルが認識した全テキスト行）
    for page in (result.pages or []):
        page_num = page.page_number if hasattr(page, "page_number") else 1
        for line in (page.lines or []):
            polygon = line.polygon if hasattr(line, "polygon") else []
            content = line.content if hasattr(line, "content") else ""
            out["bounding_boxes"].append({
                "label":   "",
                "value":   content,
                "page":    page_num,
                "polygon": polygon or [],
                "color":   "layout",
                "style":   "outline",
            })

    return out


def analyze_layout_gpt(lay_result: dict, pdf_bytes: bytes) -> dict:
    """
    prebuilt-layout の結果（ボックスリスト＋座標）と PDF 画像を GPT-4o に渡し、
    各ボックスに自由ラベルを付けつつ既知フィールドを特定する。

    画像を併用することで、請求元/請求先の取り違えなど
    テキスト座標だけでは判断しにくいケースの精度を改善する。
    """
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    client = _make_openai_client()
    out = _empty_result()

    boxes = lay_result.get("bounding_boxes", [])
    if not boxes:
        return out

    # ボックスごとに重心座標を計算してリスト化（テキストは最大60文字）
    box_list = []
    for i, box in enumerate(boxes):
        poly = box.get("polygon") or []
        if len(poly) >= 4:
            xs = poly[0::2]
            ys = poly[1::2]
            cx = round(sum(xs) / len(xs), 2)
            cy = round(sum(ys) / len(ys), 2)
        else:
            cx, cy = 0.0, 0.0
        box_list.append({
            "i": i,
            "t": (box.get("value") or "")[:60],
            "p": box.get("page", 1),
            "x": cx,
            "y": cy,
        })

    fields_desc = "\n".join(f"- {key}: {label}" for key, label in DISPLAY_FIELDS)

    prompt = f"""\
以下はPDF請求書からOCRで抽出したテキストボックスの一覧です。
i=インデックス、t=テキスト、p=ページ番号、x/y=重心座標（インチ）。
添付の画像も参照しながら、視覚的なレイアウトを踏まえて判断してください。

【タスク1】全ボックスに簡潔な日本語ラベルを自由に付けてください。
内容を表す短いラベルを付けてください（例:「会社名」「住所」「電話番号」「合計金額」「表の見出し」「品目」「日付」など）。

【タスク2】以下のフィールドに対応するボックスのインデックスを特定してください。
ラベル行ではなく実際の値が書かれているボックスを選び、見つからない場合は null にしてください。
請求元・請求先の判別は、印鑑の位置・「御中」「様」との近接・レイアウト上の配置を視覚的に確認してください。
{fields_desc}

ボックス一覧:
{json.dumps(box_list, ensure_ascii=False)}

JSON形式で返してください:
{{
  "labels": {{"0": "ラベル", "1": "ラベル", ...}},
  "fields": {{"vendor_name": 3, "customer_name": 7, "invoice_id": null, ...}}
}}
"""

    # テキストプロンプト + PDF 全ページ画像をマルチモーダルで送信
    content: list[dict] = [{"type": "text", "text": prompt}]
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })
    doc.close()

    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        max_tokens=1500,
    )

    raw = response.choices[0].message.content
    out["raw_fields"] = {"gpt_response": raw}

    try:
        gpt_result: dict = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        out["bounding_boxes"] = boxes
        return out

    free_labels: dict = gpt_result.get("labels") or {}
    classification: dict = gpt_result.get("fields") or {}

    # フィールド値の抽出と index→(label, color) マップの構築
    index_to_field: dict[int, tuple[str, str]] = {}
    for key, label in DISPLAY_FIELDS:
        raw_idx = classification.get(key)
        if raw_idx is None:
            continue
        try:
            idx = int(raw_idx)
        except (ValueError, TypeError):
            continue
        if 0 <= idx < len(boxes):
            out[key] = {"value": boxes[idx].get("value", ""), "confidence": None}
            index_to_field[idx] = (label, _FIELD_COLORS.get(key, "layout"))

    # ボックスにラベルを付与（フィールド一致 → 色付き塗り、それ以外 → GPT自由ラベル＋グレー枠）
    labeled: list[dict] = []
    for i, box in enumerate(boxes):
        gpt_label = free_labels.get(str(i), "")
        if i in index_to_field:
            lbl, clr = index_to_field[i]
            labeled.append({**box, "label": lbl, "color": clr, "style": "filled"})
        else:
            labeled.append({**box, "label": gpt_label})

    out["bounding_boxes"] = labeled
    return out


def analyze_gpt_vision(pdf_bytes: bytes) -> dict:
    """
    GPT-4o Vision で PDF の全ページを画像として分析し、フィールドを抽出する。

    bounding_boxes は常に空（GPT は座標を返さない）。
    confidence は常に None。
    """
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    client = _make_openai_client()

    # PDF 全ページを PNG base64 に変換（150 DPI）
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    content: list[dict] = [{"type": "text", "text": _EXTRACTION_PROMPT}]
    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
        })
    doc.close()

    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        max_tokens=3000,
    )
    return _parse_gpt_response(response.choices[0].message.content)


def run_all_patterns(pdf_bytes: bytes) -> dict:
    """
    invoice・layout+regex の両パターンを返す。
    呼び出し元で並列実行することを推奨。

    Returns:
        {"invoice": ..., "layout_regex": ...}
    """
    return {
        "invoice":      analyze_invoice(pdf_bytes),
        "layout_regex": analyze_layout_regex(pdf_bytes),
    }
