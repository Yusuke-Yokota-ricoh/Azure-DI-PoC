import io
import pandas as pd
import fitz  # pymupdf
import streamlit as st
from PIL import Image, ImageDraw

from ocr.analyzer import FIELD_CONFIG, analyze_invoice

# ─── カラー設定 ───────────────────────────────────────────
_COLOR_RGB: dict[str, tuple[int, int, int]] = {
    "green":  (34,  197,  94),
    "blue":   (59,  130, 246),
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
DPI = 150


# ─── 可視化ヘルパー ───────────────────────────────────────
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


# ─── Streamlit UI ─────────────────────────────────────────
st.set_page_config(page_title="請求書 OCR デモ", layout="wide")
st.title("請求書 OCR デモ")
st.caption("Azure Document Intelligence (prebuilt-invoice) を使った請求書分析")

uploaded = st.file_uploader("PDF ファイルをアップロードしてください", type=["pdf"])

if uploaded:
    pdf_bytes = uploaded.read()

    with st.spinner("Azure Document Intelligence で分析中..."):
        try:
            result = analyze_invoice(pdf_bytes)
        except Exception as e:
            st.error(f"分析に失敗しました: {e}")
            st.stop()

    tab_result, tab_viz = st.tabs(["抽出結果", "可視化"])

    # ── タブ1: 抽出結果 ──────────────────────────────────
    with tab_result:
        st.subheader("主要フィールド")

        col1, col2, col3 = st.columns(3)
        col1.metric("請求元",     result["vendor_name"]   or "—")
        col2.metric("請求先",     result["customer_name"] or "—")
        col3.metric("請求書番号", result["invoice_id"]    or "—")

        col4, col5, col6 = st.columns(3)
        col4.metric("請求日",   result["invoice_date"]  or "—")
        col5.metric("支払期限", result["due_date"]       or "—")
        col6.metric("合計金額", result["invoice_total"]  or "—")

        col7, col8 = st.columns(2)
        col7.metric("小計",   result["sub_total"] or "—")
        col8.metric("消費税", result["total_tax"]  or "—")

        if result["items"]:
            st.subheader("明細")
            st.dataframe(
                pd.DataFrame(result["items"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("明細行は検出されませんでした。")

        with st.expander("生データ（JSON）"):
            st.json(result["raw_fields"])

    # ── タブ2: 可視化 ────────────────────────────────────
    with tab_viz:
        n_pages = _page_count(pdf_bytes)
        boxes = result["bounding_boxes"]

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
