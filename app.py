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
    "layout": (107, 114, 128),  # layout 行ボックス用グレー
}
_COLOR_HEX: dict[str, str] = {
    "green":  "#22C55E",
    "blue":   "#3B82F6",
    "purple": "#A855F7",
    "orange": "#F97316",
    "red":    "#EF4444",
    "layout": "#6B7280",
}

_FIELD_LABELS: dict[str, str] = dict(DISPLAY_FIELDS)


# ─── 描画ヘルパー ─────────────────────────────────────────
def _render_page(pdf_bytes: bytes, page_num: int, boxes: list[dict]) -> Image.Image:
    """
    PDF の 1 ページをラスタライズしてボックスを描画する。

    boxes の各要素に "style" キーを持たせることで描画スタイルを切り替える:
      "filled"  → 半透明塗り + 色枠（Invoice フィールドボックス）
      "outline" → 枠線のみ（Layout 行ボックス）
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_num - 1]
    pix = page.get_pixmap(matrix=fitz.Matrix(DPI / 72, DPI / 72))
    base = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
    doc.close()

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for box in boxes:
        if box["page"] != page_num or not box.get("polygon"):
            continue
        poly = box["polygon"]
        pts = [(poly[i] * DPI, poly[i + 1] * DPI) for i in range(0, len(poly), 2)]
        rgb = _COLOR_RGB.get(box.get("color", "layout"), (107, 114, 128))

        if box.get("style", "filled") == "outline":
            draw.polygon(pts, outline=rgb + (160,))
        else:
            draw.polygon(pts, fill=rgb + (50,), outline=rgb + (230,))

    return Image.alpha_composite(base, overlay).convert("RGB")


def _page_count(pdf_bytes: bytes) -> int:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n = doc.page_count
    doc.close()
    return n


def _render_all_pages(pdf_bytes: bytes, boxes: list[dict]) -> list[Image.Image]:
    return [_render_page(pdf_bytes, p, boxes) for p in range(1, _page_count(pdf_bytes) + 1)]


# ─── UI ヘルパー ──────────────────────────────────────────
def _confidence_badge(confidence: float | None) -> str:
    if confidence is None:
        return '<span style="background:#6B7280;color:white;padding:1px 5px;border-radius:3px;font-size:11px">N/A</span>'
    if confidence >= CONFIDENCE_HIGH:
        color, icon = "#22C55E", "✓"
    elif confidence >= CONFIDENCE_MID:
        color, icon = "#F97316", "⚠"
    else:
        color, icon = "#EF4444", "✗"
    return (
        f'<span style="background:{color};color:white;'
        f'padding:1px 5px;border-radius:3px;font-size:11px">'
        f'{icon} {confidence:.0%}</span>'
    )


def _show_pdf_column(images: list[Image.Image]):
    """左カラム: PDF 各ページ画像を表示。"""
    for i, img in enumerate(images):
        if len(images) > 1:
            st.caption(f"ページ {i + 1}")
        st.image(img, use_container_width=True)


def _show_invoice_panel(result: dict):
    """右パネル: Invoice フィールド（信頼度バッジ付き）+ 明細 + 生JSON。"""
    st.markdown(
        "**信頼度凡例:** "
        '<span style="background:#22C55E;color:white;padding:1px 5px;border-radius:3px;font-size:11px">✓ ≥90%</span> '
        '<span style="background:#F97316;color:white;padding:1px 5px;border-radius:3px;font-size:11px">⚠ 70-89%</span> '
        '<span style="background:#EF4444;color:white;padding:1px 5px;border-radius:3px;font-size:11px">✗ <70%</span>',
        unsafe_allow_html=True,
    )
    st.divider()

    for key, label in DISPLAY_FIELDS:
        field = result.get(key) or {}
        value = field.get("value") or "—"
        badge = _confidence_badge(field.get("confidence"))
        st.markdown(f"**{label}**: {value} {badge}", unsafe_allow_html=True)

    if result.get("items"):
        st.divider()
        st.markdown("**明細**")
        st.dataframe(pd.DataFrame(result["items"]), use_container_width=True, hide_index=True)

    with st.expander("生データ（JSON）"):
        st.json(result.get("raw_fields", {}))


def _show_layout_panel(result: dict):
    """右パネル: Layout + 正規表現フィールド（信頼度なし）+ 全文テキスト。"""
    st.markdown("**抽出フィールド（正規表現）**")
    st.divider()

    for key, label in DISPLAY_FIELDS:
        field = result.get(key) or {}
        value = field.get("value") or "—"
        st.markdown(f"**{label}**: {value}", unsafe_allow_html=True)

    with st.expander("抽出テキスト（全文）"):
        full_text = (result.get("raw_fields") or {}).get("full_text", "")
        st.text(full_text[:3000] + ("..." if len(full_text) > 3000 else ""))


def _show_combined_panel(inv: dict, lay: dict):
    """右パネル: Invoice vs Layout フィールド比較（一致・不一致を視覚化）。"""
    st.markdown("**Invoice vs Layout 比較**")
    st.divider()

    for key, label in DISPLAY_FIELDS:
        inv_f = inv.get(key) or {}
        lay_f = lay.get(key) or {}
        inv_val  = inv_f.get("value") or "—"
        lay_val  = lay_f.get("value") or "—"
        inv_conf = inv_f.get("confidence")
        badge    = _confidence_badge(inv_conf)

        # 一致判定（どちらかが「—」なら比較不能）
        if inv_val != "—" and lay_val != "—":
            match_icon = "✓" if inv_val == lay_val else "≠"
            match_color = "#22C55E" if inv_val == lay_val else "#EF4444"
        else:
            match_icon, match_color = "—", "#9CA3AF"

        match_span = (
            f'<span style="color:{match_color};font-weight:bold">{match_icon}</span>'
        )
        st.markdown(
            f"**{label}** {match_span}  \n"
            f"Invoice: {inv_val} {badge}  \n"
            f"Layout:  {lay_val}",
            unsafe_allow_html=True,
        )
        st.write("")


# ─── メイン ───────────────────────────────────────────────
st.set_page_config(page_title="請求書 OCR デモ", layout="wide")
st.title("請求書 OCR デモ")
st.caption("Azure Document Intelligence — モデル比較デモ")

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

    tab1, tab2, tab3 = st.tabs([
        "Invoice + 信頼度",
        "Layout + 正規表現",
        "統合（Invoice + Layout）",
    ])

    # ── Tab 1: Invoice + 信頼度 ───────────────────────────
    with tab1:
        st.caption("prebuilt-invoice モデル — フィールド単位のボックス（色 = フィールド種別）")
        img_col, panel_col = st.columns([3, 2])
        with img_col:
            _show_pdf_column(_render_all_pages(pdf_bytes, inv["bounding_boxes"]))
        with panel_col:
            _show_invoice_panel(inv)

    # ── Tab 2: Layout + 正規表現 ─────────────────────────
    with tab2:
        st.caption("prebuilt-layout モデル — 行単位のボックス（グレー枠）＋正規表現抽出")
        img_col, panel_col = st.columns([3, 2])
        with img_col:
            _show_pdf_column(_render_all_pages(pdf_bytes, lay["bounding_boxes"]))
        with panel_col:
            _show_layout_panel(lay)

    # ── Tab 3: 統合（Invoice + Layout）───────────────────
    with tab3:
        st.caption("両モデルのボックスを重ねて表示 — 色付き塗り = Invoice、グレー枠 = Layout行")
        combined_boxes = inv["bounding_boxes"] + lay["bounding_boxes"]
        img_col, panel_col = st.columns([3, 2])
        with img_col:
            _show_pdf_column(_render_all_pages(pdf_bytes, combined_boxes))
        with panel_col:
            _show_combined_panel(inv, lay)
