from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

try:
    import fitz
except ImportError:  # pragma: no cover - optional dependency
    fitz = None


@dataclass
class InvoiceItem:
    resumo: str
    valor_total: str
    valor_imposto: str
    codigo_moeda: str
    data_emissao: str
    data_vencimento: str
    logradouro_origem: str
    cidade_origem: str
    pais_origem: str
    coordenadas_origem: list
    produtos: list[dict]
    nome_empresa: str
    nome_arquivo: str
    custo: float
    tempo_processamento: int


def slugify(text: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    safe = safe.strip("._")
    return safe or "arquivo"


def find_pdftoppm(poppler_bin: Optional[Path]) -> Optional[str]:
    candidates: list[Path] = []
    if poppler_bin:
        candidates.append(poppler_bin / "pdftoppm.exe")
        candidates.append(poppler_bin / "pdftoppm")

    base_dir = Path(__file__).resolve().parent
    candidates.append(base_dir / "poppler-25.12.0" / "Library" / "bin" / "pdftoppm.exe")

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return shutil.which("pdftoppm")


def convert_pdf_to_images_poppler(
    pdftoppm_path: str,
    pdf_path: Path,
    output_dir: Path,
    dpi: int,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "page"
    cmd = [
        pdftoppm_path,
        "-png",
        "-r",
        str(dpi),
        str(pdf_path),
        str(prefix),
    ]

    env = os.environ.copy()
    env["PATH"] = f"{Path(pdftoppm_path).parent}{os.pathsep}{env.get('PATH', '')}"

    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    images = list(output_dir.glob("page-*.png"))
    return sorted(images, key=_page_sort_key)


def convert_pdf_to_images_fitz(
    pdf_path: Path,
    output_dir: Path,
    dpi: int,
) -> list[Path]:
    if fitz is None:
        raise RuntimeError("PyMuPDF nao instalado")

    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    images: list[Path] = []
    try:
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi)
            image_path = output_dir / f"page-{index}.png"
            pix.save(str(image_path))
            images.append(image_path)
    finally:
        doc.close()

    return images


def convert_pdf_to_images(
    pdftoppm_path: Optional[str],
    pdf_path: Path,
    output_dir: Path,
    dpi: int,
) -> list[Path]:
    if pdftoppm_path:
        try:
            return convert_pdf_to_images_poppler(pdftoppm_path, pdf_path, output_dir, dpi)
        except subprocess.CalledProcessError:
            if fitz is None:
                raise
            return convert_pdf_to_images_fitz(pdf_path, output_dir, dpi)

    return convert_pdf_to_images_fitz(pdf_path, output_dir, dpi)


def _page_sort_key(path: Path) -> int:
    match = re.search(r"-(\d+)\.png$", path.name)
    if match:
        return int(match.group(1))
    return 0


def to_rel_url(path: Path, base_dir: Path) -> str:
    rel_path = Path(os.path.relpath(path, base_dir))
    return quote(rel_path.as_posix(), safe="/")


def load_items(json_path: Path) -> list[InvoiceItem]:
    with json_path.open("r", encoding="utf-8") as handle:
        raw_items = json.load(handle)

    items: list[InvoiceItem] = []
    for raw in raw_items:
        items.append(
            InvoiceItem(
                resumo=raw.get("resumo", raw.get("resumo_geral", "")),
                valor_total=raw.get("valor_total", ""),
                valor_imposto=raw.get("valor_imposto", ""),
                codigo_moeda=raw.get("codigo_moeda", ""),
                data_emissao=raw.get("data_emissao", ""),
                data_vencimento=raw.get("data_vencimento", ""),
                logradouro_origem=raw.get("logradouro_origem", ""),
                cidade_origem=raw.get("cidade_origem", ""),
                pais_origem=raw.get("pais_origem", ""),
                coordenadas_origem=raw.get("coordenadas_origem", []) or [],
                produtos=raw.get("produtos", []) or [],
                nome_empresa=raw.get("nome_empresa", ""),
                nome_arquivo=raw.get("nome_arquivo", ""),
                custo=raw.get("custo", 0.0),
                tempo_processamento=raw.get("tempo_processamento", 0),
            )
        )
    return items


def serialize_items(
    items: list[InvoiceItem],
    images_map: dict[str, list[str]],
    pdf_map: dict[str, str],
) -> str:
    payload = [
        {
            "resumo": item.resumo,
            "valor_total": item.valor_total,
            "valor_imposto": item.valor_imposto,
            "codigo_moeda": item.codigo_moeda,
            "data_emissao": item.data_emissao,
            "data_vencimento": item.data_vencimento,
            "logradouro_origem": item.logradouro_origem,
            "cidade_origem": item.cidade_origem,
            "pais_origem": item.pais_origem,
            "coordenadas_origem": item.coordenadas_origem,
            "produtos": item.produtos,
            "nome_empresa": item.nome_empresa,
            "nome_arquivo": item.nome_arquivo,
            "custo": item.custo,
            "tempo_processamento": item.tempo_processamento,
            "imagens": images_map.get(item.nome_arquivo, []),
            "pdf_data": pdf_map.get(item.nome_arquivo, ""),
        }
        for item in items
    ]
    encoded = json.dumps(payload, ensure_ascii=False, indent=4)
    return encoded.replace("</", "<\\/")


def image_to_data_uri(image_path: Path) -> str:
    content = image_path.read_bytes()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def pdf_to_data_uri(pdf_path: Path) -> str:
    content = pdf_path.read_bytes()
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:application/pdf;base64,{encoded}"


def build_html(
    items: list[InvoiceItem],
    files_dir: Path,
    images_dir: Path,
    html_dir: Path,
    inline_assets: bool,
    images_map: dict[str, list[str]],
    inline_pdf: bool,
    pdf_map: dict[str, str],
) -> str:
    files_dir_url = to_rel_url(files_dir, html_dir)
    images_dir_url = to_rel_url(images_dir, html_dir)
    json_payload = serialize_items(items, images_map, pdf_map)

    return f"""<!DOCTYPE html>
<html lang=\"pt\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Relatorio de Notas Fiscais</title>
    <style>
        :root {{
            color-scheme: light;
            --bg: #f4f1ed;
            --bg-accent: #dfe8f2;
            --card: rgba(255, 255, 255, 0.92);
            --text: #1d1b16;
            --muted: #5c5a55;
            --border: rgba(23, 23, 23, 0.12);
            --accent: #1f7a8c;
            --accent-strong: #0b3c49;
            --shadow: 0 24px 50px rgba(13, 23, 35, 0.12);
        }}

        * {{ box-sizing: border-box; }}

        body {{
            margin: 0;
            font-family: "Bahnschrift", "Segoe UI Variable", "Trebuchet MS", sans-serif;
            background: linear-gradient(160deg, var(--bg) 0%, var(--bg-accent) 55%, #f7efe7 100%);
            color: var(--text);
            min-height: 100vh;
        }}

        body::before {{
            content: "";
            position: fixed;
            inset: 0;
            background:
                radial-gradient(circle at 12% 12%, rgba(31, 122, 140, 0.12), transparent 45%),
                radial-gradient(circle at 90% 20%, rgba(233, 170, 78, 0.18), transparent 50%),
                radial-gradient(circle at 60% 90%, rgba(23, 55, 94, 0.12), transparent 55%);
            z-index: -1;
        }}

        header.page {{
            padding: 40px 24px 16px;
            max-width: 1200px;
            margin: 0 auto;
        }}

        header.page h1 {{
            margin: 0 0 8px;
            font-size: 30px;
            letter-spacing: 0.02em;
        }}

        header.page p {{
            margin: 0;
            color: var(--muted);
        }}

        .selector {{
            padding: 0 24px 16px;
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
            max-width: 1200px;
            margin: 0 auto;
        }}

        .selector label {{
            font-weight: 600;
        }}

        .selector select {{
            min-width: 280px;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid var(--border);
            font-size: 14px;
            background: #ffffff;
            box-shadow: 0 8px 20px rgba(13, 23, 35, 0.08);
        }}

        main {{
            padding: 0 24px 56px;
            display: grid;
            gap: 24px;
            max-width: 1200px;
            margin: 0 auto;
        }}

        .card {{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 24px;
            box-shadow: var(--shadow);
            backdrop-filter: blur(8px);
        }}

        .card-header {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
        }}

        .card-header h2 {{
            margin: 0 0 4px;
            font-size: 20px;
        }}

        .arquivo {{
            margin: 0;
            color: var(--muted);
            font-size: 13px;
        }}

        .pdf-link {{
            color: var(--accent);
            text-decoration: none;
            font-weight: 600;
        }}

        .pdf-link:hover {{
            color: var(--accent-strong);
        }}

        .pdf-link.disabled {{
            opacity: 0.6;
            pointer-events: none;
        }}

        .resumo {{
            margin: 12px 0 16px;
            color: var(--muted);
        }}

        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }}

        .stats div {{
            background: rgba(31, 122, 140, 0.08);
            border-radius: 12px;
            padding: 12px 14px;
        }}

        .stats span {{
            display: block;
            font-size: 12px;
            color: var(--muted);
            margin-bottom: 4px;
        }}

        .stats strong {{
            font-size: 16px;
        }}

        .meta {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 18px;
        }}

        .meta div {{
            background: rgba(233, 170, 78, 0.12);
            border: 1px solid rgba(233, 170, 78, 0.35);
            border-radius: 999px;
            padding: 8px 16px;
            display: flex;
            align-items: baseline;
            gap: 8px;
        }}

        .meta span {{
            font-size: 12px;
            color: var(--muted);
        }}

        .produtos-wrap h3 {{
            margin: 0 0 8px;
            font-size: 16px;
        }}

        .produtos {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 16px;
        }}

        .produtos th,
        .produtos td {{
            text-align: left;
            padding: 8px 10px;
            border-bottom: 1px solid var(--border);
            font-size: 14px;
        }}

        .produtos th {{
            color: var(--muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            font-size: 12px;
        }}

        .produtos td.num {{
            text-align: right;
        }}

        .empty {{
            color: var(--muted);
            font-size: 14px;
        }}

        .pages {{
            display: grid;
            gap: 12px;
        }}

        .pages img,
        .pages object {{
            width: 100%;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: #fafafa;
        }}

        .pages object {{
            min-height: 520px;
        }}

        .pages.empty {{
            padding: 12px;
            border: 1px dashed var(--border);
            border-radius: 8px;
        }}

        .origin {{
            display: grid;
            grid-template-columns: minmax(220px, 1fr) minmax(260px, 1.1fr);
            gap: 16px;
            align-items: stretch;
            margin-bottom: 20px;
        }}

        .origin-details {{
            background: rgba(255, 255, 255, 0.75);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 14px 16px;
        }}

        .origin-details h3 {{
            margin: 0 0 8px;
            font-size: 16px;
        }}

        .origin-details p {{
            margin: 0 0 6px;
            color: var(--muted);
        }}

        .origin-details strong {{
            color: var(--text);
        }}

        .map {{
            border-radius: 14px;
            overflow: hidden;
            border: 1px solid var(--border);
            min-height: 220px;
            background: #f4f4f2;
            display: grid;
        }}

        .map iframe {{
            width: 100%;
            height: 100%;
            border: 0;
        }}

        .map-fallback {{
            padding: 16px;
            display: grid;
            gap: 8px;
            align-content: center;
            text-align: center;
            color: var(--muted);
        }}

        .map-link {{
            color: var(--accent);
            text-decoration: none;
            font-weight: 600;
        }}

        @media (max-width: 720px) {{
            header.page {{
                padding: 24px 16px 8px;
            }}

            .selector {{
                padding: 0 16px 12px;
            }}

            main {{
                padding: 0 16px 32px;
            }}

            .origin {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <header class=\"page\">
        <h1>Relatorio de Notas Fiscais</h1>
        <p>Total de arquivos: <span id=\"total-count\">0</span></p>
    </header>
    <section class=\"selector\">
        <label for=\"nota-select\">Escolha a nota</label>
        <select id=\"nota-select\">
            <option value=\"\">Selecione uma nota...</option>
        </select>
    </section>
    <main id=\"app\"></main>

    <script id=\"data-json\" type=\"application/json\">
{json_payload}
    </script>
    <script>
        const filesDir = "{files_dir_url}";
        const imagesDir = "{images_dir_url}";
        const inlineAssets = {str(inline_assets).lower()};
        const inlinePdf = {str(inline_pdf).lower()};
        const dataElement = document.getElementById("data-json");
        const data = JSON.parse(dataElement.textContent.trim() || "[]");
        const totalCount = document.getElementById("total-count");
        const selector = document.getElementById("nota-select");
        const app = document.getElementById("app");

        totalCount.textContent = data.length.toString();

        function showMessage(message) {{
            app.innerHTML = "";
            const p = createElement("p", "empty", message);
            app.appendChild(p);
        }}

        function formatCurrency(value, currency) {{
            if (value === undefined || value === null) {{
                return "-";
            }}
            const text = value.toString().trim();
            if (!text) {{
                return "-";
            }}
            return `${{currency || ""}} ${{text}}`.trim();
        }}

        function formatBrl(value) {{
            const num = Number(value);
            if (Number.isNaN(num)) {{
                return "-";
            }}
            return `R$ ${{num.toFixed(4)}}`;
        }}

        function formatSeconds(value) {{
            const num = Number(value);
            if (Number.isNaN(num)) {{
                return "-";
            }}
            return `${{Math.trunc(num)}} s`;
        }}

        function formatText(value) {{
            if (value === undefined || value === null) {{
                return "-";
            }}
            const text = value.toString().trim();
            return text || "-";
        }}

        function normalizeText(value) {{
            if (value === undefined || value === null) {{
                return "";
            }}
            return value.toString().trim();
        }}

        function formatAddress(item) {{
            const parts = [
                normalizeText(item.logradouro_origem),
                normalizeText(item.cidade_origem),
                normalizeText(item.pais_origem),
            ].filter(Boolean);
            return parts.length ? parts.join(", ") : "-";
        }}

        function parseCoordinates(coords) {{
            if (!Array.isArray(coords) || coords.length < 2) {{
                return null;
            }}
            const lat = Number(coords[0]);
            const lon = Number(coords[1]);
            if (Number.isNaN(lat) || Number.isNaN(lon)) {{
                return null;
            }}
            return {{
                lat,
                lon,
                text: `${{lat.toFixed(6)}}, ${{lon.toFixed(6)}}`,
            }};
        }}

        function slugify(text) {{
            const safe = text.trim().replace(/[^A-Za-z0-9._-]+/g, "_");
            const trimmed = safe.replace(/^[._]+|[._]+$/g, "");
            return trimmed || "arquivo";
        }}

        function fileUrl(filename) {{
            if (!filename) {{
                return "";
            }}
            return `${{filesDir}}/${{encodeURIComponent(filename)}}`;
        }}

        function createElement(tag, className, text) {{
            const element = document.createElement(tag);
            if (className) {{
                element.className = className;
            }}
            if (text !== undefined) {{
                element.textContent = text;
            }}
            return element;
        }}

        function addPdfFallback(container, pdfUrl) {{
            container.classList.add("empty");
            const message = pdfUrl
                ? "Imagens nao geradas. Use o link abaixo para abrir o PDF."
                : "Imagens nao geradas.";
            const text = createElement("p", null, message);
            container.append(text);
            if (pdfUrl) {{
                const link = createElement("a", "pdf-link", "Abrir PDF");
                link.href = pdfUrl;
                const object = document.createElement("object");
                object.data = pdfUrl;
                object.type = "application/pdf";
                object.setAttribute("aria-label", "PDF da nota fiscal");
                container.append(link, object);
            }}
        }}

        function loadImages(container, slug, pdfUrl) {{
            let page = 1;
            let loadedAny = false;

            function tryNext() {{
                const img = new Image();
                img.alt = "Pagina do PDF";
                img.onload = () => {{
                    loadedAny = true;
                    container.appendChild(img);
                    page += 1;
                    tryNext();
                }};
                img.onerror = () => {{
                    if (!loadedAny) {{
                        addPdfFallback(container, pdfUrl);
                    }}
                }};
                img.src = `${{imagesDir}}/${{slug}}/page-${{page}}.png`;
            }}

            tryNext();
        }}

        function renderProdutos(produtos) {{
            if (!Array.isArray(produtos) || produtos.length === 0) {{
                return createElement("p", "empty", "Nenhum item encontrado.");
            }}

            const table = createElement("table", "produtos");
            const thead = document.createElement("thead");
            const headRow = document.createElement("tr");
            ["Qtd", "Produto", "Total"].forEach((label) => {{
                const th = document.createElement("th");
                th.textContent = label;
                headRow.appendChild(th);
            }});
            thead.appendChild(headRow);
            table.appendChild(thead);

            const tbody = document.createElement("tbody");
            produtos.forEach((produto) => {{
                const row = document.createElement("tr");
                const qtd = createElement("td", null, produto.quantidade || "-");
                const nome = createElement("td", null, produto.nome_produto || "-");
                const total = createElement("td", "num", produto.valor_total || "-");
                row.append(qtd, nome, total);
                tbody.appendChild(row);
            }});
            table.appendChild(tbody);
            return table;
        }}

        function buildMap(coords) {{
            const map = createElement("div", "map");
            if (!coords) {{
                const fallback = createElement("div", "map-fallback");
                fallback.append(
                    createElement("strong", null, "Coordenadas nao informadas"),
                    createElement("span", null, "Inclua latitude e longitude para ver o mapa.")
                );
                map.appendChild(fallback);
                return map;
            }}

            const deltaLat = 0.004;
            const deltaLon = 0.006;
            const left = coords.lon - deltaLon;
            const right = coords.lon + deltaLon;
            const top = coords.lat + deltaLat;
            const bottom = coords.lat - deltaLat;
            const iframe = document.createElement("iframe");
            iframe.loading = "lazy";
            iframe.referrerPolicy = "no-referrer-when-downgrade";
            iframe.src = `https://www.openstreetmap.org/export/embed.html?bbox=${{left}}%2C${{bottom}}%2C${{right}}%2C${{top}}&layer=mapnik&marker=${{coords.lat}}%2C${{coords.lon}}`;
            map.appendChild(iframe);
            return map;
        }}

        function renderCard(item) {{
            const card = createElement("section", "card");
            const header = createElement("header", "card-header");

            const headerLeft = document.createElement("div");
            const title = createElement("h2", null, item.nome_empresa || "-");
            const arquivo = createElement("p", "arquivo", `Arquivo: ${{item.nome_arquivo || "-"}}`);
            headerLeft.append(title, arquivo);

            const pdfUrl = inlinePdf
                ? (item.pdf_data || "")
                : (inlineAssets ? "" : fileUrl(item.nome_arquivo || ""));
            const link = createElement("a", "pdf-link", "Abrir PDF");
            link.href = pdfUrl || "#";
            if (inlineAssets && !inlinePdf) {{
                link.textContent = "PDF nao incluido";
                link.classList.add("disabled");
                link.setAttribute("aria-disabled", "true");
            }}

            header.append(headerLeft, link);

            const resumo = createElement("p", "resumo", item.resumo || "-");

            const stats = createElement("div", "stats");
            const total = createElement("div");
            total.append(
                createElement("span", null, "Total"),
                createElement("strong", null, formatCurrency(item.valor_total, item.codigo_moeda))
            );
            const imposto = createElement("div");
            imposto.append(
                createElement("span", null, "Imposto"),
                createElement("strong", null, formatCurrency(item.valor_imposto, item.codigo_moeda))
            );
            const tempo = createElement("div");
            tempo.append(
                createElement("span", null, "Tempo"),
                createElement("strong", null, formatSeconds(item.tempo_processamento))
            );
            const custo = createElement("div");
            custo.append(
                createElement("span", null, "Custo"),
                createElement("strong", null, formatBrl(item.custo))
            );

            stats.append(total, imposto, tempo, custo);

            const meta = createElement("div", "meta");
            const emissao = createElement("div");
            emissao.append(
                createElement("span", null, "Emissao"),
                createElement("strong", null, formatText(item.data_emissao))
            );
            const vencimento = createElement("div");
            vencimento.append(
                createElement("span", null, "Vencimento"),
                createElement("strong", null, formatText(item.data_vencimento))
            );
            meta.append(emissao, vencimento);

            const origin = createElement("div", "origin");
            const details = createElement("div", "origin-details");
            const coords = parseCoordinates(item.coordenadas_origem);
            details.append(
                createElement("h3", null, "Origem"),
                createElement("p", null, formatAddress(item)),
                createElement("p", null, `Coordenadas: ${{coords ? coords.text : "-"}}`)
            );
            const map = buildMap(coords);
            const mapLink = coords ? `https://www.openstreetmap.org/?mlat=${{coords.lat}}&mlon=${{coords.lon}}#map=16/${{coords.lat}}/${{coords.lon}}` : "";
            if (coords && mapLink) {{
                const link = createElement("a", "map-link", "Abrir no mapa");
                link.href = mapLink;
                link.target = "_blank";
                link.rel = "noopener";
                details.appendChild(link);
            }}
            origin.append(details, map);

            const produtosWrap = createElement("div", "produtos-wrap");
            const produtosTitle = createElement("h3", null, "Itens");
            produtosWrap.append(produtosTitle, renderProdutos(item.produtos));

            const pages = createElement("div", "pages");
            const slug = slugify(item.nome_arquivo || "");
            if (inlineAssets) {{
                if (Array.isArray(item.imagens) && item.imagens.length > 0) {{
                    item.imagens.forEach((src) => {{
                        const img = new Image();
                        img.alt = "Pagina do PDF";
                        img.src = src;
                        pages.appendChild(img);
                    }});
                }} else {{
                    addPdfFallback(pages, pdfUrl);
                }}
            }} else {{
                loadImages(pages, slug, pdfUrl);
            }}

            card.append(header, resumo, stats, meta, origin, produtosWrap, pages);
            return card;
        }}

        if (!selector) {{
            showMessage("Selecao nao encontrada.");
        }} else if (data.length === 0) {{
            showMessage("Nenhuma nota encontrada.");
        }} else {{
            data.forEach((item, index) => {{
                const option = document.createElement("option");
                const empresa = item.nome_empresa || "Sem empresa";
                const arquivo = item.nome_arquivo || "Sem arquivo";
                option.value = index.toString();
                option.textContent = `${{empresa}} - ${{arquivo}}`;
                selector.appendChild(option);
            }});

            showMessage("Selecione uma nota para visualizar.");

            selector.addEventListener("change", () => {{
                const selected = selector.value;
                const index = Number(selected);
                if (!selected || Number.isNaN(index) || !data[index]) {{
                    showMessage("Selecione uma nota para visualizar.");
                    return;
                }}
                app.innerHTML = "";
                app.appendChild(renderCard(data[index]));
            }});
        }}
    </script>
</body>
</html>
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Gerar relatorio HTML de notas fiscais.")
    parser.add_argument("--json", default="result.json", help="Caminho para o JSON.")
    parser.add_argument("--files-dir", default="files", help="Diretorio com os PDFs.")
    parser.add_argument("--images-dir", default="relatorio_imagens", help="Diretorio para imagens.")
    parser.add_argument("--output", default="relatorio.html", help="Arquivo HTML de saida.")
    parser.add_argument("--dpi", type=int, default=150, help="Resolucao das imagens.")
    parser.add_argument("--skip-images", action="store_true", help="Nao gerar imagens.")
    parser.add_argument("--poppler-bin", default="", help="Diretorio do Poppler (bin).")
    parser.add_argument(
        "--inline-assets",
        action="store_true",
        help="Gera HTML unico com imagens embutidas.",
    )
    parser.add_argument(
        "--inline-pdf",
        action="store_true",
        help="Embute os PDFs dentro do HTML.",
    )
    args = parser.parse_args(argv)

    base_dir = Path(__file__).resolve().parent
    json_path = (base_dir / args.json).resolve()
    files_dir = (base_dir / args.files_dir).resolve()
    images_dir = (base_dir / args.images_dir).resolve()
    output_path = (base_dir / args.output).resolve()

    if not json_path.exists():
        print(f"JSON nao encontrado: {json_path}", file=sys.stderr)
        return 1

    items = load_items(json_path)

    pdftoppm_path = None if args.skip_images else find_pdftoppm(
        Path(args.poppler_bin) if args.poppler_bin else None
    )

    if not args.skip_images and not pdftoppm_path:
        print("pdftoppm nao encontrado. Gerando HTML sem imagens.", file=sys.stderr)

    images_map: dict[str, list[str]] = {}
    pdf_map: dict[str, str] = {}

    for item in items:
        pdf_path = files_dir / item.nome_arquivo
        if not pdf_path.exists():
            print(f"PDF nao encontrado: {pdf_path}", file=sys.stderr)
            continue

        if args.inline_pdf:
            try:
                pdf_map[item.nome_arquivo] = pdf_to_data_uri(pdf_path)
            except OSError as exc:
                print(f"Falha ao embutir {pdf_path.name}: {exc}", file=sys.stderr)

        if args.skip_images or not pdftoppm_path:
            continue

        out_dir = images_dir / slugify(pdf_path.stem)
        try:
            image_paths = convert_pdf_to_images(pdftoppm_path, pdf_path, out_dir, args.dpi)
            if args.inline_assets:
                images_map[item.nome_arquivo] = [image_to_data_uri(path) for path in image_paths]
        except subprocess.CalledProcessError as exc:
            print(
                f"Falha ao converter {pdf_path.name} com Poppler: {exc}",
                file=sys.stderr,
            )
            if fitz is None:
                print("PyMuPDF nao instalado. Use --skip-images.", file=sys.stderr)
        except RuntimeError as exc:
            print(f"Falha ao converter {pdf_path.name}: {exc}", file=sys.stderr)

    html = build_html(
        items,
        files_dir,
        images_dir,
        output_path.parent,
        args.inline_assets,
        images_map,
        args.inline_pdf,
        pdf_map,
    )
    output_path.write_text(html, encoding="utf-8")
    print(f"Relatorio gerado em: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
