import os
from io import BytesIO

from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence import DocumentIntelligenceClient
from dotenv import load_dotenv

load_dotenv()

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

# 単純フィールドの API キー → 出力キー マッピング
_SIMPLE_FIELD_MAP: dict[str, str] = {
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


def analyze_invoice(pdf_bytes: bytes) -> dict:
    """
    PDF のバイト列を受け取り、prebuilt-invoice モデルで分析した結果を返す。

    Returns:
        vendor_name, customer_name, invoice_id, invoice_date, due_date,
        invoice_total, sub_total, total_tax, amount_due : str | None
        items         : list of dict（明細行）
        bounding_boxes: list of dict（可視化用ポリゴン情報）
        raw_fields    : dict（デバッグ用生データ）
    """
    endpoint = os.environ["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"]
    key = os.environ["AZURE_DOCUMENT_INTELLIGENCE_KEY"]

    client = DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key),
    )

    poller = client.begin_analyze_document(
        "prebuilt-invoice",
        body=BytesIO(pdf_bytes),
        content_type="application/octet-stream",
        locale="ja-JP",
    )
    result = poller.result()

    out: dict = {
        "vendor_name":    None,
        "customer_name":  None,
        "invoice_id":     None,
        "invoice_date":   None,
        "due_date":       None,
        "invoice_total":  None,
        "sub_total":      None,
        "total_tax":      None,
        "amount_due":     None,
        "items":          [],
        "bounding_boxes": [],
        "raw_fields":     {},
    }

    if not result.documents:
        return out

    doc = result.documents[0]
    fields = doc.fields or {}

    # 生データ（デバッグ用）
    out["raw_fields"] = {k: v.as_dict() for k, v in fields.items()}

    # 単純フィールドの抽出
    for api_key, out_key in _SIMPLE_FIELD_MAP.items():
        f = fields.get(api_key)
        if f:
            out[out_key] = f.get("content")

    # バウンディングボックス情報の収集
    boxes = []
    for api_key, (label, color) in FIELD_CONFIG.items():
        f = fields.get(api_key)
        if not f:
            continue
        for region in f.get("boundingRegions") or []:
            boxes.append({
                "label":   label,
                "value":   f.get("content", ""),
                "page":    region.get("pageNumber", 1),
                "polygon": region.get("polygon", []),
                "color":   color,
            })
    out["bounding_boxes"] = boxes

    # 明細行（Items）の抽出
    items_field = fields.get("Items")
    if items_field:
        for item in items_field.get("valueArray") or []:
            obj = item.get("valueObject") or {}
            out["items"].append({
                "品目":   (obj.get("Description") or {}).get("content"),
                "数量":   (obj.get("Quantity")    or {}).get("content"),
                "単価":   (obj.get("UnitPrice")   or {}).get("content"),
                "金額":   (obj.get("Amount")      or {}).get("content"),
            })

    return out
