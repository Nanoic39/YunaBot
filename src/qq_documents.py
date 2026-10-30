"""Bounded text extraction in a separate process; documents are never executed."""
import io
import sys
import zipfile
import xml.etree.ElementTree as ET

MAX_BYTES = 2 * 1024 * 1024
TEXT_SUFFIXES = {'.txt', '.md', '.csv', '.json', '.yaml', '.yml', '.toml', '.ini', '.cfg'}
SUFFIXES = TEXT_SUFFIXES | {'.docx', '.pdf'}


def extract(data: bytes, suffix: str) -> str:
    if len(data) > MAX_BYTES or suffix not in SUFFIXES:
        raise ValueError('不支持此格式或文件过大')
    if suffix in TEXT_SUFFIXES:
        for encoding in ['utf-8-sig', 'gb18030']:
            try:
                text = data.decode(encoding)
                if '\x00' in text:
                    raise ValueError('不是纯文本')
                return text[:6000]
            except UnicodeDecodeError:
                continue
        raise ValueError('无法识别文本编码')
    if suffix == '.docx':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > MAX_BYTES:
                raise ValueError('文档解压后过大')
            with archive.open(info) as source:
                xml = source.read(MAX_BYTES + 1)
            if len(xml) > MAX_BYTES:
                raise ValueError('文档解压后过大')
            root = ET.fromstring(xml)
            return '\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))[:6000]
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise ValueError('不读取加密文档')
    text = ''
    from itertools import islice
    for page in islice(reader.pages, 20):
        text += (page.extract_text() or '') + '\n'
        if len(text) >= 6000:
            break
    if not text.strip():
        raise ValueError('PDF 没有可提取文字，可能需要OCR')
    return text[:6000]


if __name__ == '__main__':
    import json
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024**2, 384 * 1024**2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))
        text = extract(sys.stdin.buffer.read(MAX_BYTES + 1), sys.argv[1])
        print(json.dumps({'text': text}, ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'error': type(error).__name__}))
        sys.exit(1)
