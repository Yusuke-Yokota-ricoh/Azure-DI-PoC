from concurrent.futures import ThreadPoolExecutor

import fitz  # pymupdf
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw

from ocr.analyzer import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MID,
    DISPLAY_FIELDS,
    analyze_invoice,
    analyze_layout_regex,
)

# ─── 定数 ────────────────────────────────────────────────
DPI = 150

_COLOR_RGB: dict[str, tuple[int, int, int]] = {
    "green":  ( 34, 197,  94),
    "blue":   ( 59, 130, 246),
    "purple": (168,  85, 247),
    "orange": (249, 115,  22),
    "red":    (239,  68,  68),
}
_COLOR_HEX: dict[str, str] = {
    "green":  "#22C55E",
    "blue":   "#3B82F6",
    "purple": "#A855F7",
    "orange": "#F97316",
    "red":    "#EF4444",
}

_FIELD_LABELS: dict[str, str] = dict(DISPLAY_FIELDS)

# 3列グリッド用の並び順
_FIELD_GRID: list[tuple[str, str, str]] = [
    ("vendor_name",   "customer_name",  "invoice_id"),
    ("invoice_date",  "due_date",       "invoice_total"),
    ("sub_total",     "total_tax",      "amount_due"),
]


# ─── UI ヘルパー ──────────────────────────────────────────
def _confidence_badge(confidence: float | None) -> str:
    """信頼度を色付きHTMLバッジとして返す。"""
    if confidence is None:
        return '<span style="background:#6B7280;color:white;padding:1px 5px;border-radius:3px;font-size:11px">N/A</span>'
    if confidence >= CONFIDENCE_HIGH:
        color = "#22C55E"
        icon = "✓"
    elif confidence >= CONFIDENCE_MID:
        color = "#F97316"
        icon = "⚠"
    else:
        color = "#EF4444"
        icon = "✗"
    return (
        f'<span style="background:{color};color:white;'
        f'padding:1px 5px;border-radius:3px;font-size:11px">'
        f'{icon} {confidence:.0%}</span>'
    )


def _show_fields_basic(result: dict):
    """信頼度なしで主要フィールドを st.metric グリッド表示。"""
    for row in _FIELD_GRID:
        cols = st.columns(3)
        for col, key in zip(cols, row):
            field = result.get(key) or {}
            col.metric(_FIELD_LABELS[key], field.get("value") or "—")


def _show_fields_with_confidence(result: dict):
    """信頼度バッジ付きで主要フィールドを表示。"""
    # 凡例
    st.markdown(
        "信頼度: "
        '<span style="background:#22C55E;color:white;padding:1px 5px;border-radius:3px;font-size:11px">✓ 高（≥90%）</span> '
        '<span style="background:#F97316;color:white;padding:1px 5px;border-radius:3px;font-size:11px">⚠ 中（70-89%）</span> '
        '<span style="background:#EF4444;color:white;padding:1px 5px;border-radius:3px;font-size:11px">✗ 低（<70%）</span>',
        unsafe_allow_html=True,
    )
    st.divider()

    for row in _FIELD_GRID:
        cols = st.columns(3)
        for col, key in zip(cols, row):
            field = result.get(key) or {}
            value = field.get("value") or "—"
            badge = _confidence_badge(field.get("confidence"))
            col.markdown(
                f"**{_FIELD_LABELS[key]}**  \n{value} {badge}",
                unsafe_allow_html=True,
            )
        st.write("")  # 行間スペース


def _show_items(result: dict):
    """明細テーブルを表示。"""
    if result.get("items"):
        st.subheader("明細")
        st.dataframe(
            pd.DataFrame(result["items"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("明細行は検出されませんでした。")


def _show_raw(result: dict):
    """生データをトグルで表示。"""
    with st.expander("生データ（JSON）"):
        st.json(result.get("raw_fields", {}))


def _show_comparison(inv: dict, lay: dict):
    """全パターンを横並びで比較するテーブルを表示。"""
    st.subheader("フィールド比較")
    st.caption("Invoice 基本・信頼度付き・Layout+正規表現 の3パターンを並べて表示します。")

    rows = []
    for key, label in DISPLAY_FIELDS:
        inv_f = inv.get(key) or {}
        lay_f = lay.get(key) or {}

        inv_val  = inv_f.get("value") or "—"
        inv_conf = inv_f.get("confidence")
        lay_val  = lay_f.get("value") or "—"

        # 信頼度の表示文字列
        if inv_conf is not None:
            if inv_conf >= CONFIDENCE_HIGH:
                conf_str = f"✅ {inv_val} ({inv_conf:.0%})"
            elif inv_conf >= CONFIDENCE_MID:
                conf_str = f"⚠️ {inv_val} ({inv_conf:.0%})"
            else:
                conf_str = f"❌ {inv_val} ({inv_conf:.0%})"
        else:
            conf_str = inv_val

        rows.append({
            "フィールド":          label,
            "Invoice 基本":        inv_val,
            "Invoice + 信頼度":    conf_str,
            "Layout + 正規表現":   lay_val,
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_page(pdf_bytes: bytes, page_num: int, boxes: list[dict]) -> Image.Image:
    """PDF の 1 ページをラスタライズしてバウンディングボックスを描画する。"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_num - 1]
    pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72))
    base = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
    doc.close()

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for box in boxes:
        if box["page"] != page_num or not box["polygon"]:
            continue
        poly = box["polygon"]
        pts = [(poly[i] * DPI, poly[i + 1] * DPI) for i in range(0, len(poly), 2)]
        rgb = _COLOR_RGB.get(box["color"], (255, 0, 0))
        draw.polygon(pts, fill=rgb + (50,), outline=rgb + (230,))

    return Image.alpha_composite(base, overlay).convert("RGB")


def _page_count(pdf_bytes: bytes) -> int:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n = doc.page_count
    doc.close()
    return n


def _show_visualization(pdf_bytes: bytes, boxes: list[dict]):
    """PDF 画像 + バウンディングボックスを表示。"""
    n_pages = _page_count(pdf_bytes)

    for page_num in range(1, n_pages + 1):
        page_boxes = [b for b in boxes if b["page"] == page_num]

        if n_pages > 1:
            st.subheader(f"ページ {page_num}")

        img_col, legend_col = st.columns([3, 1])

        with img_col:
            img = _render_page(pdf_bytes, page_num, page_boxes)
            st.image(img, use_container_width=True)

        with legend_col:
            st.markdown("**検出フィールド**")
            if page_boxes:
                for box in page_boxes:
                    hex_color = _COLOR_HEX.get(box["color"], "#888")
                    st.markdown(
                        f'<span style="color:{hex_color}">■</span> '
                        f'**{box["label"]}**  \n{box["value"]}',
                        unsafe_allow_html=True,
                    )
            else:
                st.info("このページに検出フィールドはありません。")

        if page_num < n_pages:
            st.divider()


# ─── メイン ───────────────────────────────────────────────
st.set_page_config(page_title="請求書 OCR デモ", layout="wide")
st.title("請求書 OCR デモ")
st.caption("Azure Document Intelligence を使った請求書分析 — 複数パターン比較")

uploaded = st.file_uploader("PDF ファイルをアップロードしてください", type=["pdf"])

if uploaded:
    pdf_bytes = uploaded.read()

    with st.spinner("Azure Document Intelligence で分析中（2モデル並列実行）..."):
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_inv = executor.submit(analyze_invoice,      pdf_bytes)
                future_lay = executor.submit(analyze_layout_regex, pdf_bytes)
                inv = future_inv.result()
                lay = future_lay.result()
        except Exception as e:
            st.error(f"分析に失敗しました: {e}")
            st.stop()

    tab_inv, tab_conf, tab_lay, tab_cmp, tab_viz = st.tabs([
        "Invoice 基本",
        "Invoice + 信頼度",
        "Layout + 正規表現",
        "比較",
        "可視化",
    ])

    with tab_inv:
        st.subheader("抽出結果（prebuilt-invoice）")
        _show_fields_basic(inv)
        _show_items(inv)
        _show_raw(inv)

    with tab_conf:
        st.subheader("抽出結果（prebuilt-invoice + 信頼度スコア）")
        _show_fields_with_confidence(inv)
        _show_items(inv)
        _show_raw(inv)

    with tab_lay:
        st.subheader("抽出結果（prebuilt-layout + 正規表現）")
        _show_fields_basic(lay)
        _show_raw(lay)

    with tab_cmp:
        _show_comparison(inv, lay)

    with tab_viz:
        st.subheader("可視化（prebuilt-invoice バウンディングボックス）")
        _show_visualization(pdf_bytes, inv["bounding_boxes"])
