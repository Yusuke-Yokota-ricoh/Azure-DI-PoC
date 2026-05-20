import os
import re
from io import BytesIO

from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from dotenv import load_dotenv

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


def _make_client() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient(
        endpoint=os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"],
        credential=AzureKeyCredential(os.environ["AZURE_DOCUMENT_INTELLIGENCE_KEY"]),
    )


def _empty_result() -> dict:
    """全フィールドを None で初期化した結果テンプレートを返す。"""
    out = {k: {"value": None, "confidence": None} for k, _ in DISPLAY_FIELDS}
    out.update({"items": [], "bounding_boxes": [], "raw_fields": {}})
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
                "label":   content[:20],
                "value":   content,
                "page":    page_num,
                "polygon": polygon or [],
                "color":   "layout",
                "style":   "outline",
            })

    return out


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
