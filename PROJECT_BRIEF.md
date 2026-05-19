# Azure Document Intelligence OCR デモ — プロジェクト概要

## 目的

Azure Document Intelligence を用いて請求書 PDF の OCR を行うデモアプリを構築する。
商談での提示を目的とするため、シンプルな Web UI を付属させる。
また、チームが後から自走できるよう、学習用ドキュメントも整備する。

---

## 要件

| 区分 | 内容 |
|------|------|
| 入力 | 請求書 PDF（日本語含む） |
| 処理 | Azure Document Intelligence による OCR・構造化抽出 |
| 出力 | 抽出テキスト・フィールド（金額、日付、宛先など）の表示 |
| UI | Streamlit による Web 画面（ローカル起動） |
| ドキュメント | Azure Document Intelligence の使い方ガイド（教育用） |

---

## 技術スタック

```
Azure Document Intelligence  ← OCR エンジン（クラウド API）
Python 3.x                   ← メイン言語
Streamlit                    ← デモ UI
azure-ai-documentintelligence ← Azure SDK
python-dotenv                ← 環境変数管理（APIキー等）
```

---

## ディレクトリ構成（予定）

```
MHSC-Azure-AI-OCR/
├── PROJECT_BRIEF.md          # このファイル（プロジェクト概要）
├── README.md                 # セットアップ手順
├── .env.example              # 環境変数テンプレート（APIキーは含まない）
├── requirements.txt          # Python依存パッケージ
├── app.py                    # Streamlit デモアプリ本体
├── ocr/
│   └── analyzer.py           # Azure Document Intelligence 呼び出しロジック
├── docs/
│   └── azure_doc_intelligence_guide.md  # 教育用ガイド
└── データ/
    ├── 請求書サンプル.pdf
    └── MSCPL7008請求書20250219(松田様).pdf
```

---

## Azure Document Intelligence について

Azure Document Intelligence（旧: Form Recognizer）は、PDF・画像から
テキストや構造化データを抽出するマネージドサービス。

### 主なモデル

| モデル | 用途 |
|--------|------|
| `prebuilt-invoice` | 請求書専用モデル。金額・日付・ベンダー名などを自動抽出 |
| `prebuilt-read` | 汎用 OCR。テキスト抽出のみ |
| `prebuilt-layout` | テキスト＋レイアウト（表・段落）を抽出 |

このデモでは **`prebuilt-invoice`** を使用する。

---

## 次にやること（優先順）

### Step 1 — Azure リソースの準備（手動作業）
- [ ] Azure ポータルで **Document Intelligence** リソースを作成
- [ ] `エンドポイント URL` と `APIキー` を取得
- [ ] `.env` ファイルに設定

### Step 2 — 教育用ドキュメントの作成
- [ ] `docs/azure_doc_intelligence_guide.md` を作成
  - サービス概要・料金・モデル種別・API の使い方

### Step 3 — Python 環境のセットアップ
- [ ] `requirements.txt` 作成
- [ ] 仮想環境 (venv) の構築手順を `README.md` に記載

### Step 4 — OCR ロジックの実装
- [ ] `ocr/analyzer.py` に Azure SDK 呼び出しを実装
- [ ] サンプル PDF で動作確認（CLI で単体テスト）

### Step 5 — Streamlit UI の実装
- [ ] `app.py` に PDF アップロード → OCR → 結果表示の画面を実装
- [ ] ブラウザで動作確認

### Step 6 — 仕上げ
- [ ] `README.md` にセットアップ〜起動手順を記載
- [ ] `.env.example` 作成

---

## 補足：Streamlit を選んだ理由

- Python のみで完結（HTML/JS 不要）
- `st.file_uploader` でファイル受け取りが数行で実装可能
- 商談デモ用途として十分なUIクオリティ
- Azure SDK との相性が良い

---

_最終更新: 2026-05-19_
