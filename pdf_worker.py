"""Isolated PDF operations. SPDX-License-Identifier: AGPL-3.0-or-later."""
import json
from pathlib import Path
import sys
import pymupdf


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


def main():
    folder, mode, number = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    try:
        with pymupdf.open(folder / 'input.pdf') as pdf:
            if pdf.needs_pass:
                raise ValueError('暂不支持加密 PDF，请先使用自己的密码解密。')
            if not 1 <= len(pdf) <= 2000:
                raise ValueError('PDF 需包含 1–2000 页。')
            if mode == 'extract':
                texts = []
                for i, page in enumerate(pdf):
                    texts.append(page.get_text(sort=True))
                    write_json(folder / 'progress.json', {'page': i + 1, 'total': len(pdf)})
                write_json(folder / 'result.json', {'texts': texts})
            else:
                if not 1 <= number <= len(pdf):
                    raise ValueError('页码超出范围。')
                page = pdf[number - 1]
                scale = min(1.6, 1600 / max(page.rect.width, page.rect.height))
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(folder / 'page.png')
                write_json(folder / 'result.json', {'ok': True})
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else '无法解析此 PDF，文件可能已损坏。'
        write_json(folder / 'result.json', {'error': message})
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
