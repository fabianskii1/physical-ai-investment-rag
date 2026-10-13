"""카탈로그의 legacy 경로와 제출 State 경로를 다른 cwd에서도 동일하게 읽는다."""
from pathlib import Path
import subprocess
import sys

from reportlab.pdfgen import canvas

from rag.PDF.pdf_loader import resolve_pdf_source_path


def test_relative_and_project_prefixed_paths_agree_independently_of_cwd(tmp_path, monkeypatch):
    project = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    expected = project / 'rag/data/raw/figure_ai/figure_helix.pdf'
    assert resolve_pdf_source_path('data/raw/figure_ai/figure_helix.pdf') == expected
    assert resolve_pdf_source_path('./rag/data/raw/figure_ai/figure_helix.pdf') == expected
    assert resolve_pdf_source_path('output/submission-live/web_sources/source.pdf') == project / 'output/submission-live/web_sources/source.pdf'
    assert resolve_pdf_source_path('com_data/Figure_AI.pdf') == project / 'com_data/Figure_AI.pdf'
    assert resolve_pdf_source_path(expected) == expected


def test_moved_loader_reads_all_supported_snapshot_locations_from_unrelated_cwd(tmp_path):
    original = Path(__file__).resolve().parents[1]
    copied = tmp_path / 'unpacked-project'
    loader = copied / 'rag/PDF/pdf_loader.py'
    loader.parent.mkdir(parents=True)
    loader.write_bytes((original / 'rag/PDF/pdf_loader.py').read_bytes())
    for relative in ('rag/data/raw/source.pdf', 'output/submission-live/web_sources/web.pdf', 'com_data/company.pdf'):
        path = copied / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(path))
        pdf.drawString(60, 750, 'Portable PDF evidence remains readable.')
        pdf.save()
    outside = tmp_path / 'unrelated-cwd'
    outside.mkdir()
    script = '''
import sys
from pathlib import Path
project = Path(sys.argv[1])
sys.path.insert(0, str(project))
from rag.PDF.pdf_loader import resolve_pdf_source_path, load_pdf
locations = ['data/raw/source.pdf', 'rag/data/raw/source.pdf',
             'output/submission-live/web_sources/web.pdf', 'com_data/company.pdf',
             str(project / 'rag/data/raw/source.pdf')]
for location in locations:
    source = resolve_pdf_source_path(location)
    assert source.is_file()
    assert 'Portable PDF evidence' in load_pdf(source, 'DOC', 'Company')[0].page_content
print('5 source paths verified from an unrelated cwd')
'''
    result = subprocess.run([sys.executable, '-c', script, str(copied)], cwd=outside,
                            capture_output=True, text=True, check=True)
    assert '5 source paths verified' in result.stdout
