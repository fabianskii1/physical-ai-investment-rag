# PDF Loader

PDF의 각 페이지를 LangChain `Document` 하나로 변환하는 모듈입니다.
핵심 함수와 실행용 CLI를 `pdf_loader.py` 하나에 넣었으며, 다른 코드에서
import해도 파일 선택창이나 터미널 출력이 자동 실행되지 않습니다.

## 요구사항과 반환 형식

```python
load_pdf(pdf_path, doc_id, company) -> list[Document]
```

- PDF 한 페이지마다 `Document` 하나를 반환합니다.
- `page_content`에는 해당 페이지에서 추출한 텍스트가 들어갑니다.
- `metadata`에는 `doc_id`, `company`, `file_path`, `page`만 들어갑니다.
- `page`는 원본 PDF 기준 1부터 시작합니다.
- `file_path`는 실행 위치가 달라져도 동일하게 사용할 수 있도록 절대경로로
  저장합니다.

반환되는 `Document` 예시는 다음과 같습니다.

```python
Document(
    page_content="첫 번째 페이지의 텍스트",
    metadata={
        "doc_id": "report-2026-001",
        "company": "example-company",
        "file_path": "/absolute/path/to/report.pdf",
        "page": 1,
    },
)
```

## 팀원에게 전달할 파일

실행에 필요한 최소 파일은 다음 세 개입니다.

```text
pdf_loader.py
requirements.txt
README.md
```

테스트까지 전달하려면 다음 파일도 포함합니다.

```text
requirements-dev.txt
pyproject.toml
tests/
input/.gitkeep
.gitignore
```

`.venv/`, `__pycache__/`, `.pytest_cache/`는 컴퓨터마다 새로 생성되므로 전달하지
않습니다. 실제 PDF에 개인정보나 사내 자료가 있다면 PDF도 별도로 공유 여부를
확인합니다.

## 처음 받은 팀원의 실행 방법

먼저 전달받은 폴더를 VS Code로 열고, VS Code의 터미널에서 해당 폴더로
이동합니다.

```bash
cd "전달받은/폴더/경로"
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

터미널 앞에 `(.venv)` 또는 가상환경 이름이 표시되면 활성화된 상태입니다.
VS Code에서 Python 인터프리터를 선택할 때도 이 폴더의 `.venv`를 선택합니다.

## PDF를 넣고 실행하는 방법

### 방법 1: 파일 선택창 사용 — 가장 쉬운 방법

```bash
python pdf_loader.py
```

지원되는 데스크톱 환경에서는 파일 선택창이 열립니다. 여기서 PDF 하나 또는
여러 개를 선택하면 됩니다. 파일 경로를 직접 입력할 필요가 없습니다.

선택창을 사용할 수 없는 환경에서는 프로젝트의 `input/` 폴더를 자동으로
확인합니다.

### 방법 2: `input` 폴더에 PDF 넣기 — 여러 파일에 편리

1. `pdf_loader.py`와 같은 위치의 `input/` 폴더에 PDF를 넣습니다.
2. 다음 명령을 실행합니다.

```bash
python pdf_loader.py --no-dialog --company "회사명"
```

`input/` 아래의 하위 폴더까지 검색하며 모든 PDF를 순서대로 처리합니다.

### 방법 3: 파일 경로 직접 전달

경로에 공백이나 한글이 있을 수 있으므로 경로 전체를 큰따옴표로 감쌉니다.

```bash
python pdf_loader.py \
  "/Users/name/Documents/report.pdf" \
  --doc-id "report-001" \
  --company "example-company"
```

Windows 예시:

```powershell
python pdf_loader.py `
  "C:\Users\name\Documents\report.pdf" `
  --doc-id "report-001" `
  --company "example-company"
```

한 줄로 실행해도 됩니다.

```bash
python pdf_loader.py "./report.pdf" --doc-id "report-001" --company "example-company"
```

### 방법 4: 폴더 또는 여러 입력 처리

폴더 하나를 넘기면 해당 폴더와 하위 폴더의 PDF를 모두 처리합니다.

```bash
python pdf_loader.py "./pdf-files" --company "example-company"
```

파일과 폴더를 함께 넘길 수도 있습니다.

```bash
python pdf_loader.py "./a.pdf" "./b.pdf" "./more-pdfs" --company "example-company"
```

`--doc-id`는 PDF가 정확히 하나일 때만 사용할 수 있습니다. 여러 파일을 처리할
때는 파일명을 기본 `doc_id`로 사용하며, 같은 이름의 파일이 여러 개면 내용
해시를 붙여 충돌을 방지합니다.

## 실행 옵션

```text
--doc-id VALUE        PDF 하나의 문서 ID 지정
--company VALUE       모든 결과에 넣을 회사명 (생략 시 unknown)
--preview-chars N     첫 페이지 미리보기 길이 (기본 300자)
--show-all-pages      모든 페이지의 본문과 metadata 출력
--no-dialog           선택창을 열지 않고 input/ 폴더 검색
```

전체 옵션은 다음 명령으로 확인합니다.

```bash
python pdf_loader.py --help
```

## 출력 결과 확인 방법

정상 실행되면 다음과 같은 검증 결과가 표시됩니다.

```text
Requirement check:
    return type       : PASS (list[Document])
    documents/pages   : PASS (3)
    text extraction   : PASS (3/3 pages, 1,926 characters)
    1-based page nums : PASS (1-3)
    metadata keys     : PASS (doc_id, company, file_path, page)
```

- `documents/pages`: 생성된 페이지별 `Document` 수입니다.
- `text extraction`: 텍스트가 있는 페이지 수와 전체 글자 수입니다.
- `1-based page nums`: `page`가 1부터 마지막 페이지까지 연속인지 확인합니다.
- `metadata keys`: 요구된 네 개의 메타데이터 키만 있는지 확인합니다.
- 첫 번째 `Document`의 본문은 실제 줄바꿈을 유지해서 미리보기로 표시합니다.

일부 페이지가 이미지로만 구성되어 있으면 `text extraction`이 `WARN`으로 나올
수 있습니다. 이 경우 페이지 생성은 성공했지만 별도의 OCR이 필요합니다.

## 다른 코드와 통합하는 방법

아래 코드는 Python 파일 안에 작성해야 합니다. `from`, `try`, `except` 문을
zsh나 PowerShell 터미널에 그대로 붙여 넣으면 셸 명령으로 인식되어 오류가
발생합니다.

```python
from pdf_loader import PDFLoaderError, load_pdf


def index_report(pdf_path: str):
    try:
        documents = load_pdf(
            pdf_path=pdf_path,
            doc_id="report-2026-001",
            company="example-company",
        )
    except PDFLoaderError as exc:
        # 프로젝트 공통 로깅이나 실패 처리를 연결합니다.
        print(f"PDF 처리 실패: {exc}")
        return []

    return documents
```

`pdf_loader.py`는 다음 구조 덕분에 다른 모듈에서 안전하게 import할 수 있습니다.

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

따라서 `load_pdf`를 import할 때 파일 선택창이나 CLI가 실행되지 않습니다.

## 테스트 실행

개발 및 테스트 의존성을 설치한 뒤 실행합니다.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

현재 테스트는 정상 PDF, 다중 페이지, 빈 페이지, 잘못된 확장자, 손상된 PDF,
빈 메타데이터, CLI의 일부 파일 실패 후 계속 처리 등을 확인합니다.

## 자주 발생하는 문제

### `ModuleNotFoundError`

가상환경이 활성화되지 않았거나 의존성이 설치되지 않은 경우입니다.

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows에서는 다음 명령을 사용합니다.

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### `company: unknown`으로 표시됨

`--company`를 생략한 정상 결과입니다. 실제 회사명이 필요하면 다음과 같이
지정합니다.

```bash
python pdf_loader.py "./report.pdf" --company "회사명"
```

### 경로를 입력했는데 `PDF file not found`가 표시됨

- 파일 선택창에서는 경로를 입력하지 말고 파일을 직접 선택합니다.
- 터미널에서 경로를 넘길 때는 전체 경로를 큰따옴표로 감쌉니다.
- 경로 끝에 불필요한 큰따옴표 문자가 들어가지 않았는지 확인합니다.
- PDF 경로만 별도의 줄에 입력하면 셸이 PDF를 프로그램처럼 실행하여
  `permission denied`가 발생할 수 있습니다.

### 터미널에서 `zsh: command not found: from`이 표시됨

Python 코드를 터미널 셸에 직접 입력한 경우입니다. Python 코드는 `.py` 파일에
작성한 뒤 `python 파일명.py`로 실행합니다.

### 텍스트가 비어 있음

스캔 문서처럼 글자가 이미지로 저장된 PDF는 일반 텍스트 추출이 되지 않습니다.
현재 로더는 OCR을 자동 실행하지 않으므로 OCR 처리 단계를 별도로 연결해야
합니다.

### 대용량 PDF 처리

PDF를 페이지 단위로 처리하지만, 반환 결과는 최종적으로 메모리의
`list[Document]`에 보관됩니다. 매우 큰 PDF를 동시에 많이 처리하는 운영
환경에서는 파일별 작업 큐를 사용하고 한 파일씩 처리하는 방식을 권장합니다.

## 공개 예외

- `PDFLoaderError`: 모든 PDF 로더 예외의 기본 클래스
- `PDFPathError`: 경로 또는 파일 접근 문제
- `PDFNotFoundError`: 파일 누락
- `PDFDirectoryError`: 파일 대신 디렉터리 전달
- `PDFAccessError`: 읽기 권한 문제
- `PDFEncryptedError`: 암호화 PDF
- `PDFValidationError`: PDF 형식 또는 구조 검증 실패
- `PDFMetadataError`: `doc_id` 또는 `company` 검증 실패
- `PDFInvalidFileError`: PDF가 아니거나 손상된 PDF
- `PDFEmptyError`: 페이지가 없는 PDF
- `PDFExtractionError`: 예상하지 못한 텍스트 추출 실패

## 통합 시 참고사항

- `load_pdf`는 동기 함수입니다. 비동기 웹 서버에서는 worker thread나 작업
  큐에서 실행합니다.
- `doc_id`는 가능하면 DB의 문서 ID처럼 시스템 전체에서 고유하고 파일 이동에
  영향받지 않는 값을 사용합니다.
- 여러 사용자가 업로드한 파일은 사용자별 임시 폴더에 저장하고, 확장자뿐 아니라
  업로드 크기와 접근 권한도 상위 시스템에서 제한하는 것이 좋습니다.
- 이 모듈은 PDF 로딩과 텍스트 추출만 담당합니다. 청킹, 임베딩, 벡터 DB 저장은
  다음 단계에서 별도의 모듈로 연결합니다.
