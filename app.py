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


def _bbox(polygon: list[float]) -> tuple[float, float, float, float]:
    """ポリゴン座標から軸並行バウンディングボックスを返す (x_min, y_min, x_max, y_max)。"""
    xs = polygon[0::2]
    ys = polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _overlaps(poly1: list[float], poly2: list[float]) -> bool:
    """2つのポリゴンが重なるかどうかを判定する（軸並行矩形で近似）。"""
    x1_min, y1_min, x1_max, y1_max = _bbox(poly1)
    x2_min, y2_min, x2_max, y2_max = _bbox(poly2)
    return not (x1_max < x2_min or x2_max < x1_min or y1_max < y2_min or y2_max < y1_min)


def _layout_text_at(inv_box: dict, lay_boxes: list[dict]) -> str:
    """Invoice フィールドボックスと重なる Layout 行のテキストを結合して返す。"""
    inv_poly = inv_box.get("polygon", [])
    inv_page = inv_box.get("page", 1)
    texts = [
        b["value"]
        for b in lay_boxes
        if b.get("page") == inv_page
        and b.get("polygon")
        and _overlaps(inv_poly, b["polygon"])
        and b.get("value")
    ]
    return " / ".join(texts) if texts else "—"


def _build_combined_boxes(inv_boxes: list[dict], lay_boxes: list[dict]) -> list[dict]:
    """
    Invoice ボックス（塗り）＋ Invoice ボックスと重なる Layout 行（枠線・同色）のみを返す。
    重ならない Layout 行は除外してノイズを減らす。
    """
    combined = list(inv_boxes)
    for lay_box in lay_boxes:
        lay_poly = lay_box.get("polygon", [])
        lay_page = lay_box.get("page", 1)
        for inv_box in inv_boxes:
            if (inv_box.get("page") == lay_page
                    and inv_box.get("polygon")
                    and lay_poly
                    and _overlaps(inv_box["polygon"], lay_poly)):
                combined.append({
                    **lay_box,
                    "color": inv_box["color"],  # Invoice フィールドと同色の枠線
                    "style": "outline",
                })
                break
    return combined


def _show_combined_panel(inv: dict, lay: dict):
    """右パネル: Invoice フィールド位置に重なる Layout テキストを並べて比較。"""
    st.markdown("**Invoice フィールド位置 × Layout テキスト 比較**")
    st.caption("Layout 列は、Invoice が検出した同じ座標範囲内にある Layout 行のテキストです。")
    st.divider()

    lay_boxes = lay["bounding_boxes"]
    # label（日本語）→ Invoice bounding box のマップ
    boxes_by_label: dict[str, list[dict]] = {}
    for box in inv["bounding_boxes"]:
        boxes_by_label.setdefault(box["label"], []).append(box)

    for key, label in DISPLAY_FIELDS:
        inv_f    = inv.get(key) or {}
        inv_val  = inv_f.get("value") or "—"
        inv_conf = inv_f.get("confidence")
        badge    = _confidence_badge(inv_conf)

        # 同位置の Layout テキスト
        field_boxes = boxes_by_label.get(label, [])
        lay_text = _layout_text_at(field_boxes[0], lay_boxes) if field_boxes else "—"

        # 一致判定（空白・記号を除去して部分一致）
        def _norm(s: str) -> str:
            return s.replace(" ", "").replace("　", "").replace("¥", "").replace(",", "")

        if inv_val != "—" and lay_text != "—":
            if _norm(inv_val) in _norm(lay_text) or _norm(lay_text) in _norm(inv_val):
                match_icon, match_color = "✓", "#22C55E"
            else:
                match_icon, match_color = "≠", "#EF4444"
        else:
            match_icon, match_color = "—", "#9CA3AF"

        match_span = f'<span style="color:{match_color};font-weight:bold">{match_icon}</span>'
        st.markdown(
            f"**{label}** {match_span}  \n"
            f"Invoice: {inv_val} {badge}  \n"
            f"Layout位置: {lay_text}",
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
        st.caption(
            "Invoice ボックス（塗り）＋ 同位置の Layout 行（同色枠線）を重ねて表示。"
            "右パネルは Invoice フィールドと同座標範囲の Layout テキストを比較。"
        )
        combined_boxes = _build_combined_boxes(inv["bounding_boxes"], lay["bounding_boxes"])
        img_col, panel_col = st.columns([3, 2])
        with img_col:
            _show_pdf_column(_render_all_pages(pdf_bytes, combined_boxes))
        with panel_col:
            _show_combined_panel(inv, lay)
